"""Reviewed, tenant-scoped Facebook video delivery, separate from clip rendering.

An upload response is only accepted for processing. Only a read-only provider
status check can mark it published. Uncertain POSTs are held for human review;
the scheduler never sends the same uncertain upload again.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import time

import app_control
import meta_accounts
import plan_limits

_CLIP_ID = re.compile(r'[0-9a-f]{32}\Z')
_ACTIVE = ('scheduled', 'sending', 'processing')


def connect():
    connection = sqlite3.connect(os.getenv('AUTOPOST_WEB_DB', 'autopost_web.db'), timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS clip_publish_jobs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id TEXT NOT NULL,
          clip_id TEXT NOT NULL,page_id TEXT NOT NULL,due_at TEXT NOT NULL,
          caption TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'scheduled',
          remote_id TEXT,permalink_url TEXT,error TEXT NOT NULL DEFAULT '',
          created INTEGER NOT NULL,updated INTEGER NOT NULL,poll_after INTEGER NOT NULL DEFAULT 0,
          UNIQUE(workspace_id,clip_id,page_id));
          CREATE INDEX IF NOT EXISTS clip_publish_due ON clip_publish_jobs(status,due_at);
          CREATE INDEX IF NOT EXISTS clip_publish_workspace ON clip_publish_jobs(workspace_id,clip_id);''')
    plan_limits.init()


def _date(value=None):
    return plan_limits._date(value)


def _account(c, workspace):
    if workspace == 'owner':
        return
    account = c.execute('SELECT email_verified FROM customer_accounts WHERE workspace_id=?', (workspace,)).fetchone()
    if not account or not account['email_verified']:
        raise PermissionError('Confirm your account email before scheduling video publishing.')


def _gate(workspace, page_id, now=None):
    if os.getenv('AUTOPOST_PREVIEW_MODE', 'false').lower() == 'true':
        raise PermissionError('Video publishing stays off in preview mode.')
    if app_control.publishing_paused(workspace):
        raise PermissionError('Publishing is paused. Resume your workspace before scheduling a video.')
    if not app_control.allowed(now=int(_date(now).timestamp()), workspace_id=workspace):
        raise PermissionError('Activate your subscription before scheduling video publishing.')
    with connect() as c:
        _account(c, workspace)
    # This read/decryption validates the selected Page without any external call.
    meta_accounts.get_page_token(workspace, page_id)


def _owned_clip(c, workspace, clip_id):
    if not isinstance(clip_id, str) or not _CLIP_ID.fullmatch(clip_id):
        raise ValueError('Choose one of your finished clips.')
    row = c.execute('SELECT * FROM clip_jobs WHERE workspace_id=? AND id=?', (workspace, clip_id)).fetchone()
    if not row or row['status'] != 'ready':
        raise ValueError('Wait for your clip to finish, then review it before publishing.')
    try:
        if json.loads(row['summary_json']).get('qa_status') != 'pass':
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError('This clip has not passed its output checks.') from None
    return row


def _media(workspace, clip_id):
    from clip_studio_web import job_dir
    with connect() as c:
        _owned_clip(c, workspace, clip_id)
    folder = job_dir(clip_id)
    path = folder / 'output' / 'clip.mp4'
    if folder.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 200 * 1024 * 1024:
        raise ValueError('The finished video is unavailable. Create a new clip before publishing.')
    return path


def _public(row):
    return {name: row[name] for name in ('id','clip_id','page_id','due_at','caption','status','remote_id','permalink_url','error','created','updated')}


def jobs(workspace, clip_id=None):
    init()
    with connect() as c:
        rows = c.execute('SELECT * FROM clip_publish_jobs WHERE workspace_id=?' + (' AND clip_id=?' if clip_id else '') + ' ORDER BY created DESC,id DESC LIMIT 100',
                         (workspace, clip_id) if clip_id else (workspace,)).fetchall()
    return [_public(row) for row in rows]


def schedule(workspace, clip_id, page_id, due_at, caption, permission_confirmed=False, now=None):
    init()
    if permission_confirmed is not True:
        raise ValueError('Confirm you reviewed this video and have permission to publish it to the selected Page.')
    if not isinstance(page_id, str) or not meta_accounts.PAGE_ID.fullmatch(page_id):
        raise ValueError('Choose a connected Facebook business Page.')
    if not isinstance(caption, str) or not 1 <= len(caption.strip()) <= 10000 or '\x00' in caption:
        raise ValueError('Write a video caption between 1 and 10,000 characters.')
    caption = caption.strip()
    moment, due = _date(now), _date(due_at)
    if not moment - timedelta(minutes=5) <= due <= moment + timedelta(days=366):
        raise ValueError('Choose now or a future time within the next year.')
    normalized = due.isoformat(timespec='seconds')
    _gate(workspace, page_id, moment)
    _media(workspace, clip_id)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _account(c, workspace)
        _owned_clip(c, workspace, clip_id)
        existing = c.execute('SELECT * FROM clip_publish_jobs WHERE workspace_id=? AND clip_id=? AND page_id=?', (workspace, clip_id, page_id)).fetchone()
        if existing and existing['status'] != 'canceled':
            if existing['caption'] == caption and existing['due_at'] == normalized:
                return _public(existing)
            raise ValueError('This clip already has a delivery for this Page. Review its status instead of submitting it again.')
        if c.execute("SELECT COUNT(*) FROM clip_publish_jobs WHERE workspace_id=? AND status='scheduled'", (workspace,)).fetchone()[0] >= 30:
            raise PermissionError('You have 30 video deliveries waiting. Finish or cancel one before adding another.')
        plan_limits.check_schedule(workspace, [('facebook_page', None, page_id)], normalized)
        stamp = int(moment.timestamp())
        if existing:
            c.execute("UPDATE clip_publish_jobs SET due_at=?,caption=?,status='scheduled',error='',updated=? WHERE id=? AND workspace_id=? AND status='canceled'", (normalized, caption, stamp, existing['id'], workspace))
            identifier = existing['id']
        else:
            identifier = c.execute('INSERT INTO clip_publish_jobs(workspace_id,clip_id,page_id,due_at,caption,created,updated) VALUES(?,?,?,?,?,?,?)', (workspace, clip_id, page_id, normalized, caption, stamp, stamp)).lastrowid
        return _public(c.execute('SELECT * FROM clip_publish_jobs WHERE id=?', (identifier,)).fetchone())


def cancel(workspace, identifier):
    init()
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        raise ValueError('Choose a valid video delivery.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM clip_publish_jobs WHERE id=? AND workspace_id=?', (identifier, workspace)).fetchone()
        if not row:
            raise ValueError('This video delivery does not belong to your workspace.')
        if row['status'] not in ('scheduled','quota_held','canceled'):
            raise ValueError('This upload has already started. Check the Page; canceling here cannot recall a published video.')
        c.execute("UPDATE clip_publish_jobs SET status='canceled',updated=? WHERE id=? AND workspace_id=?", (int(time.time()), identifier, workspace))
        return _public(c.execute('SELECT * FROM clip_publish_jobs WHERE id=?', (identifier,)).fetchone())


def recover_interrupted():
    init()
    with connect() as c:
        c.execute("UPDATE clip_publish_jobs SET status='needs_review',error='The upload was interrupted. Check your Facebook Page before creating another delivery.',updated=? WHERE status='sending'", (int(time.time()),))


def workspace_busy(workspace):
    init()
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM clip_publish_jobs WHERE workspace_id=? AND status IN ('scheduled','sending','processing') LIMIT 1", (workspace,)).fetchone())


def _update(identifier, status, error='', *, remote=None, permalink=None, moment=None):
    stamp = int(_date(moment).timestamp())
    with connect() as c:
        c.execute('UPDATE clip_publish_jobs SET status=?,error=?,remote_id=COALESCE(?,remote_id),permalink_url=COALESCE(?,permalink_url),updated=?,poll_after=? WHERE id=?',
                  (status,error,remote,permalink,stamp,stamp+60,identifier))


def _poll(moment):
    stamp = int(moment.timestamp())
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT * FROM clip_publish_jobs WHERE status='processing' AND poll_after<=? ORDER BY poll_after,id LIMIT 1", (stamp,)).fetchone()
        if not row:
            return False
        row = dict(row)
        c.execute('UPDATE clip_publish_jobs SET poll_after=? WHERE id=?', (stamp+60,row['id']))
    try:
        state = meta_accounts.get_video_status(row['workspace_id'],row['page_id'],row['remote_id'])
        if state['published']:
            _update(row['id'],'published',permalink=state.get('permalink_url'),moment=moment)
        elif state['failed'] or stamp-row['updated'] > 86400:
            _update(row['id'],'needs_review','Facebook has not confirmed a published video. Check its processing status on your Page.',moment=moment)
        # Keep the upload-completion time unchanged while polling so there is a
        # real 24-hour ceiling rather than a timer extended on every GET.
    except Exception:
        if stamp-row['updated'] > 86400:
            _update(row['id'],'needs_review','We could not confirm this video status. Reconnect the Page and check it before uploading another copy.',moment=moment)
        else:
            # GET is safe to retry. A transient network error must not force a
            # duplicate upload or abandon an otherwise successful submission.
            with connect() as c:
                c.execute("UPDATE clip_publish_jobs SET error='Waiting for Facebook to confirm this video. You can check the Page while we retry.',poll_after=? WHERE id=? AND status='processing'", (stamp+120,row['id']))
    return True


def process_next_publish(now=None):
    """At most one confirmation and one new upload per pass, without starvation."""
    init()
    moment = _date(now)
    polled = _poll(moment)
    with connect() as c:
        candidates = c.execute("SELECT * FROM clip_publish_jobs WHERE status='scheduled' AND due_at<=? ORDER BY due_at,id LIMIT 30", (moment.isoformat(timespec='seconds'),)).fetchall()
    for candidate in candidates:
        row = dict(candidate)
        try:
            _gate(row['workspace_id'],row['page_id'],moment)
        except (ValueError,PermissionError,RuntimeError):
            continue
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM clip_publish_jobs WHERE status='sending' LIMIT 1").fetchone():
                return polled
            _account(c,row['workspace_id'])
            changed = c.execute("UPDATE clip_publish_jobs SET status='sending',updated=? WHERE id=? AND status='scheduled'", (int(moment.timestamp()),row['id'])).rowcount
        if not changed:
            continue
        try:
            path = _media(row['workspace_id'],row['clip_id'])
            _gate(row['workspace_id'],row['page_id'],moment)
            plan_limits.reserve_clip_delivery(row['workspace_id'],row['id'],row['page_id'],now=moment)
            # publish_video repeats the final subscription/pause/Page-token
            # checks immediately before the only mutating network call.
            remote = meta_accounts.publish_video(row['workspace_id'],row['page_id'],path,row['caption'])
            _update(row['id'],'processing',remote=remote,moment=moment)
        except plan_limits.PlanLimitError as exc:
            _update(row['id'],'quota_held',str(exc),moment=moment)
        except Exception:
            _update(row['id'],'needs_review','Facebook did not confirm this upload. Check your Page before creating another delivery.',moment=moment)
        return True
    return polled
