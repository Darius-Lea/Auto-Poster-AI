"""Authenticated local-upload Clip Studio. No legacy publisher or remote URLs."""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file, session

clip_bp = Blueprint('clips', __name__)
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}
_ID = re.compile(r'[0-9a-f]{32}\Z')


def upload_limit():
    try:
        return max(1, min(200, int(os.getenv('AUTOPOST_CLIP_MAX_UPLOAD_MB', '200')))) * 1024 * 1024
    except ValueError:
        return MAX_UPLOAD_BYTES


def connect():
    c = sqlite3.connect(os.getenv('AUTOPOST_WEB_DB', 'autopost_web.db'), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def data_root():
    fallback = Path(os.getenv('AUTOPOST_WEB_DB', 'autopost_web.db')).resolve().parent / 'clip-data'
    return Path(os.getenv('AUTOPOST_CLIP_DATA_DIR', str(fallback))).resolve()


def job_dir(identifier):
    if not _ID.fullmatch(identifier):
        abort(404)
    return data_root() / identifier


def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS clip_jobs(
          id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,status TEXT NOT NULL,
          created INTEGER NOT NULL,updated INTEGER NOT NULL,source_ext TEXT NOT NULL,
          source_duration REAL NOT NULL,options_json TEXT NOT NULL,
          summary_json TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '',
          caption_revision INTEGER NOT NULL DEFAULT 0);
          CREATE INDEX IF NOT EXISTS clip_jobs_workspace ON clip_jobs(workspace_id,created);
          CREATE TABLE IF NOT EXISTS clip_daily_usage(
          workspace_id TEXT NOT NULL,day TEXT NOT NULL,count INTEGER NOT NULL,
          PRIMARY KEY(workspace_id,day));''')


def recover_interrupted():
    """Single-worker startup: hold interrupted work as failed, never retry it."""
    init()
    with connect() as c:
        c.execute("UPDATE clip_jobs SET status='failed',error='This operation was interrupted. Create a new clip to try again.',updated=? WHERE status IN ('uploading','rendering')", (int(time.time()),))


def renderer_readiness():
    enabled = os.getenv('AUTOPOST_CLIP_ENABLED', 'false').lower() == 'true'
    if not enabled:
        return {'enabled': False, 'ready': False, 'message': 'Clip rendering is not enabled on this service yet. Your other drafts and automations remain available.'}
    missing = [name for name in ('ffmpeg', 'ffprobe') if not shutil.which(name)]
    for module in ('faster_whisper', 'cv2', 'PIL', 'numpy'):
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    return {'enabled': True, 'ready': not missing,
            'message': 'Ready for local video uploads. The first transcription may need the service to download its speech model.' if not missing else 'The service operator must finish installing the video renderer before you can upload.'}


def actor():
    from app_mvp import current_workspace, require_login
    require_login()
    return current_workspace()


def _can_render():
    from app_mvp import preview_mode
    if preview_mode():
        return 'Rendering is unavailable in this preview. No video is uploaded.', 409
    if session.get('account_id'):
        import customer_accounts
        user = customer_accounts.get(session['account_id'])
        if not user or not user.get('email_verified'):
            return 'Confirm your email in Account settings before uploading video.', 403
    if not renderer_readiness()['ready']:
        return renderer_readiness()['message'], 503
    return None


def _csrf():
    submitted = request.headers.get('X-CSRF-Token') or request.form.get('_csrf', '')
    expected = session.get('csrf', '')
    if not submitted or not expected or not secrets.compare_digest(str(submitted), str(expected)):
        abort(403)


def _owned(identifier, workspace):
    if not _ID.fullmatch(identifier):
        abort(404)
    with connect() as c:
        row = c.execute('SELECT * FROM clip_jobs WHERE id=? AND workspace_id=?', (identifier, workspace)).fetchone()
    if not row:
        abort(404)
    return dict(row)


def _public(row):
    return {'id': row['id'], 'status': row['status'], 'created': row['created'],
            'error': row['error'], 'options': json.loads(row['options_json']),
            'summary': json.loads(row['summary_json']),
            'downloads': {kind: '/clips/jobs/' + row['id'] + '/download/' + kind for kind in ('mp4', 'srt', 'json')} if row['status'] == 'ready' else {}}


def probe_source(path):
    """Inspect only the uploaded file; ffprobe cannot fetch nested network URLs."""
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2,matroska,webm',
            '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=15, check=False)
        info = json.loads(result.stdout)
        duration = float(info['format']['duration'])
        streams = info.get('streams', [])
        if result.returncode or not math.isfinite(duration) or not 15 <= duration <= 5400:
            raise ValueError
        if not any(s.get('codec_type') == 'video' for s in streams) or not any(s.get('codec_type') == 'audio' for s in streams):
            raise ValueError
        return duration
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        raise ValueError('Choose a readable video with speech audio, between 15 seconds and 90 minutes long.') from None


def _options(form):
    try:
        duration = float(form.get('duration', 45))
        start = float(form.get('start', 0))
    except (TypeError, ValueError):
        raise ValueError('Enter valid clip times.') from None
    if not math.isfinite(duration) or not 15 <= duration <= 60 or not math.isfinite(start) or start < 0:
        raise ValueError('Choose a clip between 15 and 60 seconds and a nonnegative start time.')
    mode = form.get('mode', 'auto')
    layout = form.get('layout', 'cutaway')
    style = form.get('style', 'standard')
    if mode not in ('auto', 'manual') or layout not in ('cutaway', 'split') or style not in ('standard', 'bar', 'minimal'):
        raise ValueError('Choose one of the available clip styles and layouts.')
    title = form.get('title', '').strip()
    hook = form.get('hook', '').strip()
    brand_name = form.get('business_name', '').strip()
    accent = form.get('accent', '#A7F36A').strip()
    if len(brand_name) > 80 or any(ord(x) < 32 for x in brand_name) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', accent):
        raise ValueError('Keep the business name under 80 characters and choose a valid brand color.')
    if len(title) > 100 or len(hook) > 160 or any(ord(x) < 32 for x in title + hook):
        raise ValueError('Keep the title under 100 characters and hook under 160, on one line.')
    options = {'duration': duration, 'layout': layout, 'style': style, 'title': title or brand_name, 'hook': hook,
               'brand_name': brand_name or title or 'Your video', 'accent': accent.upper()}
    if mode == 'manual':
        options.update(start=start, end=start + duration)
    return options


def _reserve(workspace, extension, options, source_duration=0, revision=0):
    identifier = uuid.uuid4().hex
    now = int(time.time())
    day = datetime.now(timezone.utc).date().isoformat()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if workspace != 'owner':
            account = c.execute('SELECT email_verified FROM customer_accounts WHERE workspace_id=?', (workspace,)).fetchone()
            if not account or not account['email_verified']:
                raise PermissionError('Sign in to a confirmed account before rendering a clip.')
        if c.execute("SELECT COUNT(*) FROM clip_jobs WHERE workspace_id=? AND status IN ('uploading','queued','rendering')", (workspace,)).fetchone()[0] >= 2:
            raise PermissionError('You already have two clips waiting or rendering. Wait for one to finish.')
        if c.execute("SELECT COUNT(*) FROM clip_jobs WHERE status IN ('uploading','queued','rendering')").fetchone()[0] >= 10:
            raise PermissionError('The renderer is busy. Try again after a clip finishes.')
        usage = c.execute('SELECT count FROM clip_daily_usage WHERE workspace_id=? AND day=?', (workspace, day)).fetchone()
        if usage and usage['count'] >= 3:
            raise PermissionError('You have used today’s three clip renders. You can still download or delete completed clips.')
        c.execute('INSERT INTO clip_jobs(id,workspace_id,status,created,updated,source_ext,source_duration,options_json,caption_revision) VALUES(?,?,?,?,?,?,?,?,?)',
                  (identifier, workspace, 'uploading', now, now, extension, source_duration, json.dumps(options), revision))
        c.execute('INSERT INTO clip_daily_usage VALUES(?,?,1) ON CONFLICT(workspace_id,day) DO UPDATE SET count=count+1', (workspace, day))
    folder = job_dir(identifier)
    try:
        folder.mkdir(parents=True, mode=0o700)
        os.chmod(folder, 0o700)
    except OSError:
        _validation_failed(identifier, 'The service could not store this upload. Please try again later.')
        raise
    return identifier, folder


def _fail(identifier, message):
    with connect() as c:
        c.execute("UPDATE clip_jobs SET status='failed',error=?,updated=? WHERE id=?", (message, int(time.time()), identifier))


def _validation_failed(identifier, message):
    """Refund only an unqueued reservation, once; rendering failures retain usage."""
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT workspace_id,created FROM clip_jobs WHERE id=? AND status='uploading'", (identifier,)).fetchone()
        if not row:
            return
        c.execute("UPDATE clip_jobs SET status='failed',error=?,updated=? WHERE id=? AND status='uploading'", (message, int(time.time()), identifier))
        day = datetime.fromtimestamp(row['created'], timezone.utc).date().isoformat()
        c.execute('UPDATE clip_daily_usage SET count=MAX(0,count-1) WHERE workspace_id=? AND day=?', (row['workspace_id'], day))


@clip_bp.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@clip_bp.route('/clips')
def studio():
    workspace = actor()
    import creative_jobs
    from app_mvp import preview_mode
    unavailable = _can_render()
    return render_template('clip_studio.html', csrf=session['csrf'], readiness=renderer_readiness(), preview_mode=preview_mode(),
                           business_name=creative_jobs.get_profile(workspace).get('name', ''), can_render=not unavailable,
                           render_notice=unavailable[0] if unavailable else '', needs_verification=bool(unavailable and unavailable[1] == 403))


@clip_bp.route('/clips/example')
def example():
    # A fixed bundled preview only; never accepts a customer-controlled path.
    path = Path(__file__).resolve().parent / 'features/clip_studio/app/assets/example-clip.mp4'
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype='video/mp4', conditional=True)


@clip_bp.route('/clips/jobs', methods=['GET', 'POST'])
def jobs():
    workspace = actor()
    init()
    if request.method == 'GET':
        with connect() as c:
            rows = c.execute('SELECT * FROM clip_jobs WHERE workspace_id=? ORDER BY created DESC,id DESC LIMIT 30', (workspace,)).fetchall()
        return jsonify(jobs=[_public(r) for r in rows])
    _csrf()
    unavailable = _can_render()
    if unavailable:
        return jsonify(error=unavailable[0]), unavailable[1]
    if request.form.get('permission_confirmed') != 'yes':
        return jsonify(error='Confirm you own this video or have permission to edit and use it.'), 400
    upload = request.files.get('video')
    extension = Path(upload.filename or '').suffix.lower() if upload else ''
    if not upload or extension not in _EXTENSIONS or request.form.get('url') or request.form.get('source'):
        return jsonify(error='Upload an MP4, MOV, MKV, WEBM or M4V file. Remote links and server paths are not accepted.'), 400
    identifier = None
    try:
        options = _options(request.form)
        identifier, folder = _reserve(workspace, extension, options)
        source = folder / ('source' + extension)
        size = 0
        with source.open('xb') as output:
            os.chmod(source, 0o600)
            while chunk := upload.stream.read(1024 * 1024):
                size += len(chunk)
                if size > upload_limit():
                    raise ValueError('Keep the uploaded video under 200 MB.')
                output.write(chunk)
        if not size:
            raise ValueError('Choose a nonempty video file.')
        duration = probe_source(source)
        if options.get('start') is None:
            options['duration'] = min(options['duration'], duration)
        if options['duration'] > duration or options.get('end', 0) > duration:
            raise ValueError('The chosen clip must fit inside the uploaded video.')
        with connect() as c:
            c.execute("UPDATE clip_jobs SET status='queued',source_duration=?,options_json=?,updated=? WHERE id=?", (duration, json.dumps(options), int(time.time()), identifier))
        return jsonify(job=_public(_owned(identifier, workspace))), 202
    except (ValueError, PermissionError) as exc:
        if identifier:
            _validation_failed(identifier, str(exc))
            shutil.rmtree(job_dir(identifier), ignore_errors=True)
        return jsonify(error=str(exc)), 429 if isinstance(exc, PermissionError) else 400
    except OSError:
        if identifier:
            _validation_failed(identifier, 'The service could not store this upload. Please try again later.')
            shutil.rmtree(job_dir(identifier), ignore_errors=True)
        return jsonify(error='The service could not store this upload. Please try again later.'), 503


@clip_bp.route('/clips/jobs/<identifier>/download/<kind>')
def download(identifier, kind):
    row = _owned(identifier, actor())
    if row['status'] != 'ready' or kind not in ('mp4', 'srt', 'json'):
        abort(404)
    filename = 'summary.json' if kind == 'json' else 'clip.' + kind
    path = job_dir(identifier) / 'output' / filename
    if not path.is_file() or path.is_symlink():
        abort(404)
    return send_file(path, mimetype={'mp4':'video/mp4','srt':'text/plain; charset=utf-8','json':'application/json'}[kind],
                     as_attachment=kind != 'mp4' or request.args.get('attachment') == '1', download_name='auto-poster-clip.' + kind, conditional=True)


def _validate_captions(text, duration):
    if not text or len(text) > 30000 or '\x00' in text:
        raise ValueError('Provide edited SRT captions under 30,000 characters.')
    timings = re.findall(r'(?m)^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})\s*$', text)
    if not timings:
        raise ValueError('Keep the SRT timestamp lines when editing captions.')
    for parts in timings:
        a, b, c, d, e, f, g, h = map(int, parts)
        start, end = a*3600+b*60+c+d/1000, e*3600+f*60+g+h/1000
        if b > 59 or c > 59 or f > 59 or g > 59 or not 0 <= start < end <= duration + 1:
            raise ValueError('Caption times must fit inside this clip.')
    return text


@clip_bp.route('/clips/jobs/<identifier>/captions', methods=['POST'])
def captions(identifier):
    workspace = actor()
    _csrf()
    unavailable = _can_render()
    if unavailable:
        return jsonify(error=unavailable[0]), unavailable[1]
    parent = _owned(identifier, workspace)
    if parent['status'] != 'ready':
        return jsonify(error='Wait for the first clip to finish before editing captions.'), 409
    options = json.loads(parent['options_json'])
    summary = json.loads(parent['summary_json'])
    moment = summary.get('moment') or {}
    child = None
    try:
        edited = _validate_captions(request.form.get('captions', ''), options['duration'])
        # Auto-pick must reuse the actual original window, not choose a new moment.
        if options.get('start') is None:
            options['start'] = float(moment['start'])
            options['end'] = float(moment['end'])
        child, folder = _reserve(workspace, parent['source_ext'], options, parent['source_duration'], parent['caption_revision'] + 1)
        original = job_dir(identifier)
        shutil.copyfile(original / ('source' + parent['source_ext']), folder / ('source' + parent['source_ext']))
        transcript = original / 'output' / 'clip.transcript.json'
        if not transcript.is_file():
            raise ValueError('This render has no reusable transcript. Create a new clip instead.')
        shutil.copyfile(transcript, folder / 'transcript.json')
        (folder / 'edited.srt').write_text(edited, encoding='utf-8')
        with connect() as c:
            c.execute("UPDATE clip_jobs SET status='queued',updated=? WHERE id=?", (int(time.time()), child))
        return jsonify(job=_public(_owned(child, workspace))), 202
    except (ValueError, PermissionError, KeyError, OSError, TypeError) as exc:
        if child:
            _validation_failed(child, 'Caption revision could not be prepared. Your original clip is unchanged.')
            shutil.rmtree(job_dir(child), ignore_errors=True)
        return jsonify(error=str(exc) if isinstance(exc, (ValueError, PermissionError)) else 'Caption revision could not be prepared. Your original clip is unchanged.'), 429 if isinstance(exc, PermissionError) else 400


@clip_bp.route('/clips/jobs/<identifier>/delete', methods=['POST'])
def delete(identifier):
    workspace = actor()
    _csrf()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT status FROM clip_jobs WHERE id=? AND workspace_id=?', (identifier, workspace)).fetchone()
        if not row:
            abort(404)
        if row['status'] in ('rendering', 'uploading'):
            return jsonify(error='Wait for this operation to finish before deleting its files.'), 409
        c.execute('DELETE FROM clip_jobs WHERE id=? AND workspace_id=?', (identifier, workspace))
    shutil.rmtree(job_dir(identifier), ignore_errors=True)
    return jsonify(message='Clip and source video deleted. Today’s render allowance is unchanged.')


def workspace_busy(workspace):
    init()
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM clip_jobs WHERE workspace_id=? AND status IN ('uploading','queued','rendering') LIMIT 1", (workspace,)).fetchone())


def delete_workspace(workspace):
    init()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        rows = c.execute('SELECT id,status FROM clip_jobs WHERE workspace_id=?', (workspace,)).fetchall()
        if any(r['status'] in ('uploading','queued','rendering') for r in rows):
            raise ValueError('Wait for pending video clips to finish or delete queued clips before deleting your account.')
        c.execute('DELETE FROM clip_jobs WHERE workspace_id=?', (workspace,))
        c.execute('DELETE FROM clip_daily_usage WHERE workspace_id=?', (workspace,))
    for row in rows:
        shutil.rmtree(job_dir(row['id']), ignore_errors=True)


def process_next_job():
    """Called by the app maintenance worker; claim at most one global render."""
    if not renderer_readiness()['ready']:
        return False
    init()
    now = int(time.time())
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        # A restart must not leave interrupted jobs blocking the queue forever.
        c.execute("UPDATE clip_jobs SET status='failed',error='Rendering was interrupted. Please create a new clip.',updated=? WHERE status IN ('rendering','uploading') AND updated<?", (now, now-3600))
        if c.execute("SELECT 1 FROM clip_jobs WHERE status='rendering' LIMIT 1").fetchone():
            return False
        row = c.execute("SELECT * FROM clip_jobs WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
        if not row:
            return False
        row = dict(row)
        c.execute("UPDATE clip_jobs SET status='rendering',updated=? WHERE id=? AND status='queued'", (now, row['id']))
    folder = job_dir(row['id'])
    try:
        if row['workspace_id'] != 'owner':
            with connect() as c:
                account = c.execute('SELECT email_verified FROM customer_accounts WHERE workspace_id=?', (row['workspace_id'],)).fetchone()
            if not account or not account['email_verified']:
                raise ValueError('Confirm your email before rendering this clip.')
        from features.clip_studio import render_clip
        options = json.loads(row['options_json'])
        brand_name = options.pop('brand_name', 'Your video')
        accent = options.pop('accent', '#A7F36A')
        kit = folder / 'brand.json'
        kit.write_text(json.dumps({'name': brand_name, 'end_card_name': brand_name,
                                   'accent': accent, 'text_only': True}), encoding='utf-8')
        options['brand'] = str(kit)
        if row['caption_revision']:
            options['captions_file'] = str(folder / 'edited.srt')
            options['transcript_file'] = str(folder / 'transcript.json')
        result = render_clip(str(folder / ('source' + row['source_ext'])), options,
                             out_dir=str(folder / 'output'), out_name='clip.mp4', local_only=True, timeout=1800)
        output = folder / 'output'
        if (result.get('summary') or {}).get('qa_status') != 'pass' or not all((output / name).is_file() for name in ('clip.mp4','clip.srt','clip.transcript.json')):
            raise ValueError('The clip did not pass its output checks.')
        # Never expose raw build logs, source paths, provider details or local files.
        summary = {key: (result.get('summary') or {}).get(key) for key in ('hook','peak_line','loudness_lufs','true_peak_dbtp','qa_status','style','brand','moment')}
        (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        with connect() as c:
            c.execute("UPDATE clip_jobs SET status='ready',summary_json=?,updated=? WHERE id=?", (json.dumps(summary), int(time.time()), row['id']))
    except Exception:
        # Engine stderr/build logs can contain private paths/transcript; keep them private.
        _fail(row['id'], 'We could not finish a usable clip. Your video was not published. Try a clearer speaking section or a new upload.')
    return True
