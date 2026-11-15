"""Private workspace logos and immutable Clip Studio brand snapshots.

Only decoded, normalized raster pixels enter the renderer. No customer-supplied
path, URL, SVG or serialized brand kit is accepted by the HTTP interface.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import uuid
import warnings

from flask import Blueprint, abort, jsonify, request, send_file, session
from werkzeug.exceptions import RequestEntityTooLarge

branding_bp = Blueprint('clip_branding', __name__)
MAX_LOGO_BYTES = 4 * 1024 * 1024
MAX_LOGO_PIXELS = 16_000_000
MAX_LOGO_DIMENSION = 8192
NORMALIZED_DIMENSION = 2048
_FORMATS = {'PNG': ('image/png', {'.png'}), 'JPEG': ('image/jpeg', {'.jpg', '.jpeg'}), 'WEBP': ('image/webp', {'.webp'})}


class StaleBrand(ValueError):
    pass


def connect():
    c = sqlite3.connect(os.getenv('AUTOPOST_WEB_DB', 'autopost_web.db'), timeout=15)
    c.row_factory = sqlite3.Row
    return c


def init():
    with connect() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS clip_brand_profiles(
          workspace_id TEXT PRIMARY KEY,name TEXT NOT NULL,accent TEXT NOT NULL,
          revision INTEGER NOT NULL,logo_digest TEXT NOT NULL DEFAULT '')''')


def _root():
    fallback = Path(os.getenv('AUTOPOST_WEB_DB', 'autopost_web.db')).resolve().parent / 'clip-data'
    return Path(os.getenv('AUTOPOST_CLIP_DATA_DIR', str(fallback))).resolve() / 'brands'


def _folder(workspace):
    if not isinstance(workspace, str) or not workspace:
        raise ValueError('A workspace is required.')
    return _root() / hashlib.sha256(workspace.encode('utf-8')).hexdigest()


def _public(row):
    return {'name': row['name'] if row else '', 'accent': row['accent'] if row else '#A7F36A',
            'revision': row['revision'] if row else 0, 'has_logo': bool(row and row['logo_digest']),
            'logo_url': '/clips/brand/logo?revision=' + str(row['revision']) if row and row['logo_digest'] else None}


def get_profile(workspace):
    with connect() as c:
        row = c.execute('SELECT * FROM clip_brand_profiles WHERE workspace_id=?', (workspace,)).fetchone()
    return _public(row)


def _fields(name, accent):
    name = str(name).strip()
    accent = str(accent).strip()
    if len(name) > 80 or any(ord(x) < 32 or ord(x) == 127 for x in name):
        raise ValueError('Keep your business name under 80 characters, on one line.')
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', accent):
        raise ValueError('Choose a valid brand color.')
    return name, accent.upper()


def _assert_workspace(connection, workspace):
    if not isinstance(workspace, str) or not workspace or len(workspace) > 128:
        raise ValueError('Choose a valid account workspace.')
    if workspace != 'owner' and not connection.execute(
            'SELECT 1 FROM customer_accounts WHERE workspace_id=?', (workspace,)).fetchone():
        raise ValueError('Your account is no longer available. Sign in again.')


def normalize_logo(data, filename, mimetype):
    if not data or len(data) > MAX_LOGO_BYTES:
        raise ValueError('Choose a PNG, JPG or WebP logo no larger than 4 MB.')
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise RuntimeError('Logo uploads are temporarily unavailable. Please try again later.') from None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                format_name = image.format
                allowed = _FORMATS.get(format_name)
                if not allowed or mimetype.lower() != allowed[0] or Path(filename).suffix.lower() not in allowed[1]:
                    raise ValueError('Choose a real PNG, JPG or WebP file matching its file type.')
                width, height = image.size
                if min(width, height) < 16 or max(width, height) > MAX_LOGO_DIMENSION or width * height > MAX_LOGO_PIXELS:
                    raise ValueError('Choose a logo at least 16 pixels per side and no larger than 16 million pixels or 8192 pixels per side.')
                if getattr(image, 'n_frames', 1) != 1:
                    raise ValueError('Choose a still image for your logo, rather than an animation.')
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                image = ImageOps.exif_transpose(image).convert('RGBA')
                image.thumbnail((NORMALIZED_DIMENSION, NORMALIZED_DIMENSION), Image.Resampling.LANCZOS)
                # A fresh pixel image carries none of the source EXIF, text or ICC metadata.
                # The renderer places the logo in a square. Letterbox with
                # transparent pixels here instead of stretching a wide mark.
                side = max(image.size)
                clean = Image.new('RGBA', (side, side), (0, 0, 0, 0))
                clean.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
                output = io.BytesIO()
                clean.save(output, format='PNG', optimize=True)
                return output.getvalue()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('This image is too large to use safely. Choose a smaller logo.') from None
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValueError('We could not read that image. Try exporting a fresh PNG, JPG or WebP logo.') from None


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.is_symlink():
        raise OSError('Unsafe logo storage target.')
    temporary = path.with_name('.upload-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_profile(workspace, *, name, accent, revision, logo=None, remove_logo=False):
    name, accent = _fields(name, accent)
    try:
        expected = int(revision)
        if expected < 0:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Reload your brand settings before saving.') from None
    if logo is not None and remove_logo:
        raise ValueError('Choose a new logo or remove your current one, rather than both.')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _assert_workspace(c, workspace)
        row = c.execute('SELECT * FROM clip_brand_profiles WHERE workspace_id=?', (workspace,)).fetchone()
        if expected != (row['revision'] if row else 0):
            raise StaleBrand('Your brand changed in another tab. Reload the latest settings before saving.')
        digest = row['logo_digest'] if row else ''
        if logo is not None:
            digest = hashlib.sha256(logo).hexdigest()
            _atomic_write(_folder(workspace) / ('logo-' + digest + '.png'), logo)
        elif remove_logo:
            digest = ''
        c.execute('''INSERT INTO clip_brand_profiles VALUES(?,?,?,?,?)
          ON CONFLICT(workspace_id) DO UPDATE SET name=excluded.name,accent=excluded.accent,
          revision=excluded.revision,logo_digest=excluded.logo_digest''', (workspace, name, accent, expected + 1, digest))
        # Content-addressed files make a crash before commit an unused blob, not
        # a changed logo attached to an older revision. Reads/snapshots share this lock.
    try:
        _prune_unused(workspace)
    except (OSError, sqlite3.Error):
        # The profile already committed. Cleanup may wait for another save;
        # a cleanup failure must not report that the profile failed to save.
        pass
    return get_profile(workspace)


def _prune_unused(workspace):
    # Independent snapshots have their own copies. Recheck the live digest under
    # a fresh lock before cleanup so a concurrent save can safely select a logo.
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT logo_digest FROM clip_brand_profiles WHERE workspace_id=?', (workspace,)).fetchone()
        keep = 'logo-' + row['logo_digest'] + '.png' if row and row['logo_digest'] else ''
        for path in _folder(workspace).glob('logo-*.png'):
            if path.name != keep and re.fullmatch(r'logo-[0-9a-f]{64}\.png', path.name):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def snapshot_brand(workspace, jobfolder, *, name=None, accent=None):
    """Before queueing, freeze this workspace's brand in trusted job storage.

    Callers pass only their server-selected job directory. Caption revisions
    must COPY the parent's brand directory instead of snapshotting new settings.
    """
    folder = Path(jobfolder) / 'brand'
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _assert_workspace(c, workspace)
        row = c.execute('SELECT * FROM clip_brand_profiles WHERE workspace_id=?', (workspace,)).fetchone()
        selected_name, selected_accent = _fields(
            name if name is not None else (row['name'] if row else ''),
            accent if accent is not None else (row['accent'] if row else '#A7F36A'))
        folder.mkdir(mode=0o700, parents=True, exist_ok=False)
        kit = {'name': selected_name or 'Your video', 'end_card_name': selected_name or 'Your video',
               'accent': selected_accent, 'text_only': True}
        if row and row['logo_digest']:
            source = _folder(workspace) / ('logo-' + row['logo_digest'] + '.png')
            if source.is_symlink() or not source.is_file():
                raise OSError('Your saved logo is unavailable. Save your brand again before creating a clip.')
            destination = folder / 'logo.png'
            with source.open('rb') as source_stream, destination.open('xb') as target:
                os.chmod(destination, 0o600)
                shutil.copyfileobj(source_stream, target)
            kit.update(logo='logo.png', logo_icon='logo.png', text_only=False)
        kit_path = folder / 'kit.json'
        with kit_path.open('x', encoding='utf-8') as output:
            os.chmod(kit_path, 0o600)
            json.dump(kit, output, ensure_ascii=False)
    return kit_path


def delete_workspace(workspace):
    """Call after account deletion commits; queued clips have independent copies.

    This acquires its own SQLite write lock, so do not call it inside another
    connection's write transaction. The caller must enforce its job guards.
    """
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM clip_brand_profiles WHERE workspace_id=?', (workspace,))
        shutil.rmtree(_folder(workspace), ignore_errors=True)


def _actor():
    from app_mvp import current_workspace, require_login
    require_login()
    return current_workspace()


def _csrf():
    supplied = request.headers.get('X-CSRF-Token') or request.form.get('_csrf', '')
    expected = session.get('csrf', '')
    if not supplied or not expected or not secrets.compare_digest(
            str(supplied).encode('utf-8'), str(expected).encode('utf-8')):
        abort(403)


@branding_bp.after_request
def private(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@branding_bp.before_request
def bound_upload():
    if request.method == 'POST':
        # The application also sets this before its global CSRF form parsing.
        request.max_content_length = MAX_LOGO_BYTES + 64 * 1024


@branding_bp.errorhandler(RequestEntityTooLarge)
def logo_too_large(_error):
    return jsonify(error='Choose a PNG, JPG or WebP logo no larger than 4 MB.'), 413


@branding_bp.route('/clips/brand', methods=['GET', 'POST'])
def brand_settings():
    workspace = _actor()
    if request.method == 'GET':
        return jsonify(profile=get_profile(workspace))
    _csrf()
    try:
        upload = request.files.get('logo')
        logo = None
        if upload and upload.filename:
            logo = normalize_logo(upload.stream.read(MAX_LOGO_BYTES + 1), upload.filename, upload.mimetype)
        profile = save_profile(workspace, name=request.form.get('name', ''), accent=request.form.get('accent', ''),
                               revision=request.form.get('revision'), logo=logo,
                               remove_logo=request.form.get('remove_logo') == 'yes')
        return jsonify(profile=profile, message='Your brand is saved. It will be used for new clips; queued clips keep their original branding.')
    except StaleBrand as error:
        return jsonify(error=str(error), profile=get_profile(workspace)), 409
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except (OSError, sqlite3.Error, RuntimeError):
        return jsonify(error='We could not confirm your saved brand right now. Reload saved settings before trying again.'), 503


@branding_bp.route('/clips/brand/logo')
def brand_logo():
    workspace = _actor()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM clip_brand_profiles WHERE workspace_id=?', (workspace,)).fetchone()
        if not row or not row['logo_digest']:
            abort(404)
        path = _folder(workspace) / ('logo-' + row['logo_digest'] + '.png')
        if path.is_symlink() or not path.is_file():
            abort(404)
        # Read under the lock so removal/replacement cannot race this request.
        data = path.read_bytes()
    return send_file(io.BytesIO(data), mimetype='image/png', download_name='my-logo.png', conditional=False)
