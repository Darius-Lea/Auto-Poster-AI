"""Attributed popular-video inspiration, separate from curated editing presets.

YouTube's mostPopular chart supplies current public video metadata and observed
counts. It does not supply a ranking of editing effects, sounds or future reach.
Provider credentials stay on the server and no invented trend data is returned.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import sqlite3
import time

from flask import Blueprint, jsonify, request
import requests

trends_bp = Blueprint('editing_trends', __name__)
API_URL = 'https://www.googleapis.com/youtube/v3/videos'
SOURCE_URL = 'https://developers.google.com/youtube/v3/docs/videos/list'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RETRY_SECONDS = 300
REGIONS = ('US', 'CA', 'GB', 'AU', 'IN')
_ID = re.compile(r'[A-Za-z0-9_-]{11}\Z')
_REGION = re.compile(r'[A-Z]{2}\Z')
_DURATION = re.compile(r'PT(?:(\d{1,6})H)?(?:(\d{1,6})M)?(?:(\d{1,6})S)?\Z')
PRESETS = (
    {'id': 'standard', 'name': 'Clear and bold',
     'description': 'Readable highlighted captions, a strong opening and your brand color.'},
    {'id': 'bar', 'name': 'A clear headline',
     'description': 'A headline treatment and caption highlights for an explanatory moment.'},
    {'id': 'minimal', 'name': 'Keep it simple',
     'description': 'A restrained caption treatment that keeps attention on the speaker.'},
)


def connect():
    c = sqlite3.connect(os.getenv('AUTOPOST_WEB_DB', 'autopost_web.db'), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init():
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS editing_trend_cache(
            region TEXT PRIMARY KEY, fetched_at INTEGER NOT NULL DEFAULT 0,
            attempted_at INTEGER NOT NULL DEFAULT 0, lease_until INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '')''')


def _region(value=None):
    default = os.getenv('AUTOPOST_TRENDS_REGION', 'US').strip().upper()
    value = str(value or default).strip().upper()
    if not _REGION.fullmatch(value):
        raise ValueError('Choose a two-letter country code for popular videos.')
    if value not in REGIONS and value != default:
        raise ValueError('Choose one of the available countries for popular videos.')
    return value


def _ttl():
    try:
        return max(1800, min(86400, int(os.getenv('AUTOPOST_TRENDS_CACHE_SECONDS', '1800'))))
    except ValueError:
        return 1800


def configured():
    return bool(os.getenv('YOUTUBE_DATA_API_KEY', '').strip()) and os.getenv('AUTOPOST_EDITING_TRENDS_ENABLED', 'true').lower() == 'true'


def _count(value):
    # Unknown observations must not be represented as zero engagement.
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**20:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,20}', value):
        return int(value)
    return None


def _published(value, now):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.timestamp() > now + 300:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def _duration(value):
    matched = _DURATION.fullmatch(value) if isinstance(value, str) else None
    if not matched or not any(matched.groups()):
        return None
    h, m, s = (int(v or 0) for v in matched.groups())
    return h * 3600 + m * 60 + s


def parse_videos(payload, now=None):
    """Normalize only documented public fields; never retain the raw response."""
    now = int(time.time() if now is None else now)
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise ValueError('Popular-video data could not be read.')
    videos = []
    seen = set()
    for item in payload['items'][:20]:
        if not isinstance(item, dict):
            continue
        identifier = item.get('id')
        snippet = item.get('snippet')
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in seen or not isinstance(snippet, dict):
            continue
        title = snippet.get('title')
        channel = snippet.get('channelTitle')
        published = _published(snippet.get('publishedAt'), now)
        if not isinstance(title, str) or not title.strip() or not published:
            continue
        details = item.get('contentDetails') if isinstance(item.get('contentDetails'), dict) else {}
        statistics = item.get('statistics') if isinstance(item.get('statistics'), dict) else {}
        duration = _duration(details.get('duration'))
        videos.append({
            'id': identifier, 'title': title.strip()[:240],
            'channel': channel[:160] if isinstance(channel, str) else '',
            'url': 'https://www.youtube.com/watch?v=' + identifier,
            'published_at': published, 'duration_seconds': duration,
            'short_video': duration is not None and 0 < duration <= 180,
            'views': _count(statistics.get('viewCount')),
            'likes': _count(statistics.get('likeCount')),
            'comments': _count(statistics.get('commentCount')),
        })
        seen.add(identifier)
    return videos


def fetch_videos(region, *, now=None):
    """Official fixed-host HTTPS GET only; inherited proxy/TLS policy is retained."""
    key = os.getenv('YOUTUBE_DATA_API_KEY', '').strip()
    if not key:
        raise ValueError('Popular-video inspiration is not connected on this service yet.')
    response = None
    try:
        response = requests.get(API_URL,
            params={'part': 'snippet,statistics,contentDetails', 'chart': 'mostPopular',
                    'regionCode': _region(region), 'maxResults': 20, 'key': key},
            headers={'Accept': 'application/json'}, timeout=(3.05, 8),
            allow_redirects=False, stream=True)
        if response.status_code != 200:
            # Exceptions, response URLs and bodies may contain a credential.
            raise ValueError('Popular-video inspiration could not update. Try again later.')
        declared = response.headers.get('Content-Length')
        if declared and (not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES):
            raise ValueError('Popular-video data could not be read.')
        chunks = []
        count = 0
        deadline = time.monotonic() + 12
        for chunk in response.iter_content(chunk_size=16384):
            count += len(chunk)
            if count > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                raise ValueError('Popular-video data could not be read.')
            chunks.append(chunk)
        payload = json.loads(b''.join(chunks))
        return parse_videos(payload, now=now)
    except (requests.RequestException, json.JSONDecodeError, UnicodeDecodeError, TypeError):
        raise ValueError('Popular-video inspiration could not update. Try again later.') from None
    finally:
        if response is not None:
            response.close()


def _refresh(region, now):
    """A global per-region lease and cooldown prevent visitor-driven quota loops."""
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR IGNORE INTO editing_trend_cache(region) VALUES(?)', (region,))
        row = c.execute('SELECT * FROM editing_trend_cache WHERE region=?', (region,)).fetchone()
        if row['lease_until'] > now or row['fetched_at'] and now - row['fetched_at'] < _ttl() or row['attempted_at'] and now - row['attempted_at'] < RETRY_SECONDS:
            return
        c.execute('UPDATE editing_trend_cache SET attempted_at=?,lease_until=? WHERE region=?', (now, now+30, region))
    try:
        videos = fetch_videos(region, now=now)
        with connect() as c:
            c.execute('UPDATE editing_trend_cache SET fetched_at=?,payload_json=?,error=?,lease_until=0 WHERE region=?',
                      (now, json.dumps(videos, ensure_ascii=False), '', region))
    except ValueError as exc:
        # fetch_videos returns canned messages only; never persist exception repr.
        message = str(exc) if str(exc) in ('Popular-video data could not be read.', 'Popular-video inspiration could not update. Try again later.') else 'Popular-video inspiration could not update. Try again later.'
        with connect() as c:
            c.execute('UPDATE editing_trend_cache SET error=?,lease_until=0 WHERE region=?', (message, region))


def get_snapshot(region=None, *, refresh=False, now=None):
    region = _region(region)
    now = int(time.time() if now is None else now)
    base = {'region': region, 'source': 'YouTube most popular videos', 'source_url': SOURCE_URL,
            'configured': configured(), 'fetched_at': None, 'stale': True, 'videos': [],
            'presets': list(PRESETS), 'cache_seconds': _ttl(),
            'notice': 'Popular videos can inspire topics and openings. These observations do not rank editing effects or predict your engagement. Editing looks below are curated presets, not measured live trends.'}
    if not base['configured']:
        return {**base, 'status': 'unconfigured', 'message': 'Live popular-video inspiration is not connected on this service yet. You can still choose an editing look below.'}
    init()
    if refresh:
        _refresh(region, now)
    with connect() as c:
        row = c.execute('SELECT * FROM editing_trend_cache WHERE region=?', (region,)).fetchone()
    if not row or not row['fetched_at']:
        return {**base, 'status': 'unavailable', 'message': row['error'] if row and row['error'] else 'No current popular-video observations have been fetched yet.'}
    try:
        videos = json.loads(row['payload_json'])
        if not isinstance(videos, list):
            raise ValueError
    except (ValueError, TypeError):
        videos = []
    stale = now - row['fetched_at'] >= _ttl() or row['fetched_at'] > now + 300
    message = ('Showing older observations. A live update is unavailable.' if stale else
               'Current observations from YouTube’s most-popular chart for this country.' if videos else
               'The current provider response contains no videos for this country.')
    return {**base, 'status': 'stale' if stale else 'current' if videos else 'empty',
            'message': message, 'error': row['error'] or None, 'videos': videos,
            'stale': stale, 'fetched_at': row['fetched_at']}


@trends_bp.route('/clips/trends')
def trends():
    from app_mvp import require_login, preview_mode
    require_login()
    try:
        snapshot = get_snapshot(request.args.get('region'), refresh=not preview_mode())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    response = jsonify(snapshot)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
