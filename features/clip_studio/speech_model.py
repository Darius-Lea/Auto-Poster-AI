"""Cheap local speech-model readiness; importing this module never contacts a provider."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from importlib import metadata

MODELS = {
    name: 'Systran/faster-whisper-' + name
    for name in ('tiny', 'tiny.en', 'base', 'base.en', 'small', 'small.en', 'medium', 'medium.en', 'large-v3')
}
REQUIRED_FILES = ('model.bin', 'config.json', 'tokenizer.json')
VOCABULARY_FILES = ('vocabulary.txt', 'vocabulary.json')
HEALTH_FILE = '.autoposter-speech-health.json'
NOT_READY_MESSAGE = 'Speech recognition is not ready. The service operator needs to prepare and verify the speech model before video uploads can render.'


def model_name():
    name = os.getenv('AUTOPOST_CLIP_MODEL', 'base.en').strip()
    if name not in MODELS:
        raise ValueError('Choose a supported Clip Studio speech model.')
    return name


def model_directory():
    name = model_name()
    explicit = os.getenv('AUTOPOST_CLIP_MODEL_DIR', '').strip()
    if explicit:
        folder = Path(explicit).expanduser()
    else:
        cache = os.getenv('AUTOPOST_CLIP_MODEL_CACHE', '').strip()
        if cache:
            root = Path(cache).expanduser()
        else:
            root = Path(os.getenv('HF_HOME', str(Path.home()/'.cache'/'huggingface'))).expanduser()/'clip-studio'
        folder = root/name
    return folder.resolve()


def runtime_signature():
    result = {}
    for name in ('faster-whisper', 'ctranslate2', 'tokenizers'):
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def fingerprints(directory):
    """Size/mtime checks are cheap; the verifier also records weight hashes once."""
    folder = Path(directory)
    files = {}
    vocabulary = next((name for name in VOCABULARY_FILES if (folder/name).is_file()), None)
    if vocabulary is None:
        raise ValueError('Speech model vocabulary is missing.')
    for name in (*REQUIRED_FILES, vocabulary):
        path = folder/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('Speech model files are missing.')
        stat = path.stat()
        if stat.st_size <= 0:
            raise ValueError('A speech model file is empty.')
        files[name] = {'size':stat.st_size, 'mtime_ns':stat.st_mtime_ns}
    return files


def speech_model_status():
    """Return safe customer-facing status, never a filesystem path or a credential."""
    try:
        name = model_name()
        folder = model_directory()
    except (OSError, ValueError):
        return {'ready':False,'model':None,'reason':'invalid_configuration','message':NOT_READY_MESSAGE}
    try:
        current = fingerprints(folder)
    except (OSError, ValueError):
        return {'ready':False,'model':name,'reason':'missing_weights','message':NOT_READY_MESSAGE}
    try:
        marker = folder/HEALTH_FILE
        if marker.is_symlink() or marker.stat().st_size > 16384:
            raise ValueError
        with marker.open(encoding='utf-8') as handle:
            health = json.load(handle)
        if (health.get('version') != 1 or health.get('model') != name
                or health.get('files') != current
                or health.get('inference_passed') is not True
                or health.get('runtime') != runtime_signature()
                or any(value is None for value in health.get('runtime', {}).values())
                or type(health.get('word_count')) is not int
                or health['word_count'] < 3
                or not isinstance(health.get('verified_at'), str)
                or not isinstance(health.get('sha256'), dict)
                or set(health['sha256']) != set(current)
                or any(not isinstance(value, str) or len(value) != 64
                       or any(char not in '0123456789abcdef' for char in value)
                       for value in health['sha256'].values())):
            raise ValueError
    except (OSError, ValueError, TypeError, AttributeError):
        return {'ready':False,'model':name,'reason':'not_verified','message':NOT_READY_MESSAGE}
    return {'ready':True,'model':name,'reason':'verified_local_model',
            'message':'Speech recognition is prepared and passed an offline sample check on this host.'}


def require_ready_model():
    if not speech_model_status()['ready']:
        raise RuntimeError(NOT_READY_MESSAGE)
    return model_directory()


def load_local_model(*, require_health=True):
    """Load only operator-provisioned bytes. Normal jobs never download models."""
    folder = require_ready_model() if require_health else model_directory()
    fingerprints(folder)
    from faster_whisper import WhisperModel
    try:
        threads = max(1, min(8, int(os.getenv('AUTOPOST_CLIP_CPU_THREADS', '2'))))
    except ValueError:
        threads = 2
    return WhisperModel(str(folder), device='cpu', compute_type='int8',
                        cpu_threads=threads, num_workers=1, local_files_only=True)


def record_success(word_count, sample_audio, *, expected_files=None):
    """Called only after an offline model load and successful sample inference.

    This local operator record is evidence of a smoke check, not accuracy
    certification. The UI reads cheap size/mtime fingerprints on later requests.
    """
    if type(word_count) is not int or word_count < 3:
        raise ValueError('The speech sample did not produce enough words.')
    folder = model_directory()
    before = fingerprints(folder)
    if expected_files is not None and before != expected_files:
        raise ValueError('The speech model changed during verification.')
    digests = {}
    for name in before:
        digest = hashlib.sha256()
        with (folder/name).open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024*1024), b''):
                digest.update(chunk)
        digests[name] = digest.hexdigest()
    if fingerprints(folder) != before:
        raise ValueError('The speech model changed during verification.')
    runtime = runtime_signature()
    if any(value is None for value in runtime.values()):
        raise ValueError('The speech runtime is not installed.')
    health = {'version': 1, 'model': model_name(), 'files': before,
              'sha256': digests, 'inference_passed': True,
              'runtime': runtime,
              'word_count': word_count,
              'sample_audio_sha256': hashlib.sha256(sample_audio).hexdigest(),
              'verified_at': datetime.now(timezone.utc).isoformat()}
    descriptor, temporary = tempfile.mkstemp(prefix='.speech-health-', dir=folder)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(health, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, folder/HEALTH_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return speech_model_status()


def download_official_model():
    """Explicit operator-only network action; never called by customer jobs."""
    folder = model_directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download
    options = {'repo_id': MODELS[model_name()], 'local_dir': str(folder),
               'allow_patterns': ['model.bin', 'config.json', 'tokenizer.json',
                                  'vocabulary.*', 'preprocessor_config.json'],
               'local_files_only': False}
    revision = os.getenv('AUTOPOST_CLIP_MODEL_REVISION', '').strip()
    if revision:
        options['revision'] = revision
    snapshot_download(**options)
    fingerprints(folder)
    return folder
