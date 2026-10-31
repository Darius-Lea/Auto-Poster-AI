"""Bounded catch-up batches: up to ten current bookings per Facebook post.

The ledger reserves every member before uploading. An ambiguous remote result
or interrupted local acknowledgement blocks automatic repeats for review.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import tempfile

import requests

from adapters._common import UA, download_image
from adapters.allen_sheriff import NO_CHARGE_TEXT, parse_detail
from adapters.base import Record, get_adapter
from core import custody, delivery, grouped, watermark
from core.caption import _fallback
from core.facebook import FacebookDeliveryUnknown, FacebookError
from core.roster_monitor import source_key

log = logging.getLogger("core.catchup")
GROUP_SIZE = 10


def _published_members(conn: sqlite3.Connection, destination: str) -> set[str]:
    return {member['hash'] for group in grouped.list_groups(conn, destination, 'published')
            for member in group['members']}


def _previous_individuals(conn: sqlite3.Connection, destination: str,
                          current: set[str], published_members: set[str]) -> list[dict]:
    cursor = conn.execute("SELECT * FROM deliveries WHERE destination=? AND status='published'",
                          (destination,))
    columns = [field[0] for field in cursor.description]
    receipts = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return [receipt for receipt in receipts if receipt['record_hash'] in current
            and receipt['record_hash'] not in published_members]


def _archive_previous(conn: sqlite3.Connection, destination: str, current: set[str]) -> int:
    """Replace only current individual receipts, retaining their complete audit data.

    This never deletes or edits Facebook content. The source snapshot must have
    been validated before calling; published group members remain immutable.
    """
    with conn:
        if not conn.in_transaction:
            conn.execute('BEGIN IMMEDIATE')
        conn.execute("""
            CREATE TABLE IF NOT EXISTS catchup_replaced_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_hash TEXT NOT NULL,
                destination TEXT NOT NULL,
                remote_ref TEXT,
                delivery_json TEXT NOT NULL,
                custody_json TEXT,
                archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        previous = _previous_individuals(conn, destination, current,
                                          _published_members(conn, destination))
        for receipt in previous:
            cursor = conn.execute('SELECT * FROM custody_posts WHERE record_hash=? AND destination=?',
                                  (receipt['record_hash'], destination))
            fields = [field[0] for field in cursor.description]
            row = cursor.fetchone()
            observation = dict(zip(fields, row)) if row is not None else None
            conn.execute(
                'INSERT INTO catchup_replaced_deliveries '
                '(record_hash,destination,remote_ref,delivery_json,custody_json) VALUES (?,?,?,?,?)',
                (receipt['record_hash'], destination, receipt['remote_ref'],
                 json.dumps(receipt, ensure_ascii=False),
                 json.dumps(observation, ensure_ascii=False) if observation is not None else None),
            )
            removed = conn.execute(
                "DELETE FROM deliveries WHERE record_hash=? AND destination=? AND status='published' "
                'AND remote_ref IS ?', (receipt['record_hash'], destination, receipt['remote_ref']),
            )
            if removed.rowcount != 1:
                raise RuntimeError('An individual delivery changed during catch-up replacement')
            conn.execute('DELETE FROM custody_posts WHERE record_hash=? AND destination=?',
                         (receipt['record_hash'], destination))
    return len(previous)


def _remaining(conn: sqlite3.Connection, destination: str, rows: list[dict], hash_fields) -> int:
    covered = _published_members(conn, destination)
    return sum(identity not in covered and not delivery.blocked(conn, identity, destination)
               for identity in (hash_fields(row['name'], row['booking_date'], row['detail_url'])
                                for row in rows))


def _group_key(source: str, members: list[dict]) -> str:
    return hashlib.sha256((source + '\n' + '\n'.join(member['hash'] for member in members)).encode()).hexdigest()


def _prepare(rows: list[dict], city: dict, hash_fields, folder: str) -> tuple[list[dict], list[dict]]:
    members, images = [], []
    for index, row in enumerate(rows):
        response = requests.get(row['detail_url'], headers={'User-Agent': UA}, timeout=30)
        response.raise_for_status()
        detail = parse_detail(response.text)
        record = Record(row['name'], '; '.join(detail['charges']) or NO_CHARGE_TEXT,
                        row['booking_date'], row['detail_url'], row['image_url'], age=detail['age'])
        caption = _fallback(record)
        downloaded = download_image(record.image_url, referer=record.source_url)
        if not downloaded:
            raise RuntimeError(f"Image download failed for {record.name}")
        try:
            image = watermark.stamp(downloaded, city['logo'], str(Path(folder) / f'{index}.jpg'))
            if not image:
                raise RuntimeError(f"Watermark failed for {record.name}")
        finally:
            # download_image creates this task's disposable temporary file.
            try:
                os.unlink(downloaded)
            except FileNotFoundError:
                pass
        identity = hash_fields(record.name, record.booking_date, record.source_url)
        members.append({'hash': identity, 'name': record.name, 'booking_date': record.booking_date,
                        'source_url': record.source_url, 'image_url': record.image_url,
                        'charge': record.charge, 'caption': caption})
        images.append({'image_path': image, 'caption': caption})
    return members, images


def _confirm_result(result: object, page_id: str, count: int) -> tuple[str, list[str]]:
    if not isinstance(result, dict):
        raise FacebookDeliveryUnknown('Group upload returned no usable confirmation; check the Page before retrying')
    post_id, photo_ids = result.get('post_id'), result.get('photo_ids')
    if (not isinstance(post_id, str) or not re.fullmatch(re.escape(str(page_id)) + r'_[0-9]+', post_id)
            or not isinstance(photo_ids, list) or len(photo_ids) != count
            or any(not isinstance(value, str) or not re.fullmatch(r'[0-9]+', value) for value in photo_ids)
            or len(set(photo_ids)) != count):
        raise FacebookDeliveryUnknown('Group confirmation did not identify every booking/photo; check the Page before retrying')
    return post_id, photo_ids


def _hold(conn: sqlite3.Connection, key: str, destination: str, reason: str) -> None:
    current = grouped.get(conn, key, destination)
    if current and current['status'] == 'pending':
        grouped.mark_uncertain(conn, key, destination, reason)


def _failure_context(exc: Exception) -> str:
    """Retain known remote uploads even when creating the feed post failed.

    These are identifiers returned by Facebook, never access credentials. The
    checkpoint must preserve them so a lost response can be reconciled without
    blindly uploading all of the photos again.
    """
    detail = str(exc)
    stage = getattr(exc, 'stage', None)
    photo_ids = getattr(exc, 'uploaded_photo_ids', None)
    if stage in ('upload', 'feed'):
        detail += f'; stage={stage}'
    if isinstance(photo_ids, list):
        known = [value for value in photo_ids
                 if isinstance(value, str) and re.fullmatch(r'[0-9]+', value)]
        detail += '; known_uploaded_photo_ids=' + json.dumps(known)
    return detail


def run_batch(city: dict, conn: sqlite3.Connection, client, hash_fields, *,
              max_groups: int = 2, dry_run: bool = False, include_previous: bool = False) -> dict:
    """Attempt a bounded batch; completion means all eligible current rows are covered.

    The caller initializes delivery, custody and group schemas. Preview reads the
    source and history but makes no detail/image requests, reservations or posts.
    Existing pending/uncertain deliveries require reconciliation before catch-up.
    With explicit ``include_previous=True``, current individual delivery/custody
    rows are archived and replaced by new group receipts. Earlier group members
    are always skipped. No prior Facebook post is automatically deleted.
    """
    report = {'groups_published': 0, 'people_published': 0, 'groups_planned': 0,
              'remaining': None, 'complete': False, 'errors': [], 'paused': False,
              'replaced_individuals': 0, 'notes': []}
    if not isinstance(max_groups, int) or isinstance(max_groups, bool) or max_groups < 1:
        raise ValueError('max_groups must be a positive integer')
    if not isinstance(include_previous, bool):
        raise ValueError('include_previous must be a boolean')
    destination = f"meta:{client.page_id}:{city['workspace_key']}"
    pending = [row for row in delivery.list_unresolved(conn) if row['destination'] == destination]
    unsettled_groups = [group for group in grouped.list_groups(conn, destination)
                        if group['status'] in ('pending', 'uncertain')]
    if pending or unsettled_groups:
        report['paused'] = True
        report['errors'].append('Unresolved deliveries or groups exist; check the Page and reconcile them before catch-up')
        return report
    sources = [source for source in city['sources'] if source['adapter'] == 'allen_sheriff'
               and source.get('kind', 'booking') == 'booking']
    if len(sources) != 1:
        report['errors'].append('Catch-up requires exactly one Allen County booking source')
        return report
    source = sources[0]
    try:
        adapter = get_adapter('allen_sheriff')
        rows = adapter.snapshot_rows(source['url'])
        if not adapter.snapshot_complete or not rows:
            raise RuntimeError('Catch-up requires a complete, nonempty current roster')
    except Exception as exc:
        report['errors'].append(f'Roster scan failed: {exc}')
        return report
    published_members = _published_members(conn, destination)
    current = {hash_fields(row['name'], row['booking_date'], row['detail_url']) for row in rows}
    previous = _previous_individuals(conn, destination, current, published_members) if include_previous else []
    previous_hashes = {receipt['record_hash'] for receipt in previous}
    if include_previous and not dry_run:
        try:
            report['replaced_individuals'] = _archive_previous(conn, destination, current)
            if report['replaced_individuals']:
                report['notes'].append(f"Archived {report['replaced_individuals']} prior individual receipts locally; no Facebook posts were deleted")
        except Exception as exc:
            report['paused'] = True
            report['errors'].append(f'Could not archive prior individual delivery history: {exc}')
            return report
    # Source order is retained; ten consecutive eligible bookings make a group.
    eligible = [row for row in rows
                if (identity := hash_fields(row['name'], row['booking_date'], row['detail_url'])) not in published_members
                and (not delivery.blocked(conn, identity, destination)
                     or (include_previous and dry_run and identity in previous_hashes))]
    report['remaining'] = len(eligible)
    report['groups_planned'] = min(max_groups, (len(eligible) + GROUP_SIZE - 1) // GROUP_SIZE)
    if dry_run:
        report['complete'] = not eligible
        return report
    source_identity = source_key(city, source)
    for offset in range(0, min(len(eligible), max_groups * GROUP_SIZE), GROUP_SIZE):
        candidates = eligible[offset:offset + GROUP_SIZE]
        try:
            with tempfile.TemporaryDirectory(prefix='autopost-catchup-') as folder:
                members, images = _prepare(candidates, city, hash_fields, folder)
                caption = grouped.render_caption(members)
                key = _group_key(source_identity, members)
                if not grouped.create(conn, key, destination, source_identity, members, caption):
                    report['paused'] = True
                    report['errors'].append(f'Group {key} already exists; reconcile or explicitly retry its recorded outcome')
                    break
                claims = []
                try:
                    for member in members:
                        if not delivery.claim(conn, member['hash'], destination):
                            raise RuntimeError('A booking was reserved by another run before upload')
                        claims.append(member['hash'])
                except Exception as exc:
                    for identity in claims:
                        delivery.release(conn, identity, destination)
                    grouped.fail(conn, key, destination, str(exc))
                    report['paused'] = True
                    report['errors'].append(str(exc))
                    break
                try:
                    result = client.publish_group(images, caption)
                    post_id, photo_ids = _confirm_result(result, str(client.page_id), len(members))
                except FacebookDeliveryUnknown as exc:
                    reason = _failure_context(exc)
                    _hold(conn, key, destination, reason)
                    report['paused'] = True
                    report['errors'].append(f'Group {key}: {reason}')
                    break
                except FacebookError as exc:
                    reason = _failure_context(exc)
                    for identity in claims:
                        delivery.release(conn, identity, destination)
                    grouped.fail(conn, key, destination, reason)
                    report['errors'].append(f'Group {key}: {reason}')
                    break
                except Exception as exc:
                    _hold(conn, key, destination, f'Unexpected upload outcome: {exc}')
                    report['paused'] = True
                    report['errors'].append(f'Group {key}: upload outcome needs review ({type(exc).__name__})')
                    break
                # The remote group is confirmed even if saving subsequent local
                # acknowledgement fails. Retain known IDs before individual work.
                report['groups_published'] += 1
                report['people_published'] += len(members)
                try:
                    grouped.mark_published(conn, key, destination, post_id, photo_ids)
                    for member, photo_id in zip(members, photo_ids):
                        delivery.complete(conn, member['hash'], destination, 'meta:' + photo_id)
                        custody.track(conn, member['hash'], destination, source_identity, photo_id,
                                      member['caption'], member['source_url'], member['name'])
                except Exception as exc:
                    _hold(conn, key, destination, f'Post accepted but local acknowledgement failed: {exc}')
                    report['paused'] = True
                    report['errors'].append(f'Group {key}: post {post_id} accepted; local acknowledgement needs reconciliation')
                    break
                try:
                    client.publish_comment(post_id, grouped.render_comment(members))
                except FacebookError as exc:
                    note = f'Post {post_id} was published, but its booking comment was not confirmed: {exc}'
                    report['notes'].append(note)
                    log.warning(note)
                else:
                    cta = "Check the comments for this batch’s booking list."
                    if grouped.DISCLAIMER in caption:
                        caption_with_cta = caption.replace(
                            grouped.DISCLAIMER,
                            cta + "\n\n" + grouped.DISCLAIMER,
                            1,
                        )
                    else:
                        caption_with_cta = caption + "\n\n" + cta
                    try:
                        client.update_caption(post_id, caption_with_cta, expected_caption=caption)
                    except Exception as exc:
                        note = f'Post {post_id} has a booking comment, but the comments prompt could not be added: {exc}'
                        report['notes'].append(note)
                        log.warning(note)
                log.info('Confirmed catch-up post %s with %d bookings', post_id, len(members))
        except Exception as exc:
            report['errors'].append(f'Group preparation failed: {exc}')
            break
    report['remaining'] = _remaining(conn, destination, rows, hash_fields)
    report['complete'] = report['remaining'] == 0 and not report['errors'] and not report['paused']
    return report
