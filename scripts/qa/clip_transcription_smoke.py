"""Operator speech-model status, explicit warmup, and real offline inference.

No arguments: local status only, no network. --verify checks existing weights.
--warm explicitly downloads the configured official model and verifies it.
--render additionally performs a fresh transcription and actual clip render.
The bounded child process never prints sample words, secret values or file paths.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.clip_studio import speech_model

DEFAULT_SAMPLE = ROOT/'features/clip_studio/app/sample/sample-podcast.mp4'


def verify_existing_model(source, work_directory, *, start=10, duration=20):
    source = Path(source)
    if (not source.is_file() or source.is_symlink() or not math.isfinite(start)
            or start < 0 or not math.isfinite(duration) or not 5 <= duration <= 60):
        raise ValueError('Choose a local speech video and a 5–60 second check window.')
    source = source.resolve()
    before = speech_model.fingerprints(speech_model.model_directory())
    wav = Path(work_directory)/'speech-check.wav'
    command = ['ffmpeg', '-v', 'error', '-protocol_whitelist', 'file,pipe',
               '-format_whitelist', 'mov,matroska,webm', '-ss', str(start),
               '-i', str(source), '-t', str(duration), '-vn', '-ac', '1',
               '-ar', '16000', '-acodec', 'pcm_s16le', str(wav), '-y']
    completed = subprocess.run(command, capture_output=True, timeout=90, check=False)
    if completed.returncode or not wav.is_file():
        raise ValueError('The speech-check video could not be decoded.')
    import numpy as np
    with wave.open(str(wav), 'rb') as handle:
        if handle.getnchannels() != 1 or handle.getframerate() != 16000 or handle.getsampwidth() != 2:
            raise ValueError('The speech-check audio format is invalid.')
        pcm = handle.readframes(handle.getnframes())
    if len(pcm) < 16000*2*3:
        raise ValueError('The selected speech window is too short.')
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)/32768.0
    if float(np.sqrt(np.mean(audio**2))) < 0.001:
        raise ValueError('The selected speech window is silent or too quiet.')
    # No health record is required for the verifier itself. It must actually
    # load the local model and complete inference before creating that record.
    model = speech_model.load_local_model(require_health=False)
    segments, _ = model.transcribe(audio, word_timestamps=True, beam_size=1,
                                  language='en', vad_filter=True)
    words = []
    for segment in segments:
        for word in segment.words or []:
            if (word.word.strip() and math.isfinite(word.start) and math.isfinite(word.end)
                    and 0 <= word.start < word.end <= len(audio)/16000+1):
                words.append(word)
    if len(words) < 3:
        raise ValueError('The speech model did not recognize enough timed words.')
    status = speech_model.record_success(len(words), pcm, expected_files=before)
    if not status['ready']:
        raise ValueError('The speech model check did not become ready.')
    return {**status, 'word_count': len(words), 'check': 'offline_real_inference'}


def bounded_verification(source, *, warm=False, start=10, duration=20, timeout=900):
    with tempfile.TemporaryDirectory(prefix='autoposter-speech-check-') as folder:
        command = [sys.executable, str(Path(__file__).resolve()), '--internal-check',
                   '--source', str(source), '--work-directory', folder,
                   '--start', str(start), '--duration', str(duration)]
        if warm:
            command.append('--warm')
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, start_new_session=(os.name == 'posix'))
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.communicate()
            raise ValueError('The speech model check exceeded its time limit.') from None
        try:
            result = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            raise ValueError('The speech model check could not complete.') from None
        if process.returncode or not result.get('ready'):
            raise ValueError(result.get('message', 'The speech model check could not complete.'))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', help='Verify existing local weights without network access')
    parser.add_argument('--warm', action='store_true', help='Explicitly download the official configured model, then verify it')
    parser.add_argument('--render', action='store_true', help='After verification, run a fresh transcription and real clip render')
    parser.add_argument('--source', type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--start', type=float, default=10)
    parser.add_argument('--duration', type=float, default=20)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--internal-check', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--work-directory', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 30 <= args.timeout <= 3600:
        parser.error('Choose a timeout between 30 and 3600 seconds.')
    if args.render and args.output_dir is None:
        parser.error('--render requires a private --output-dir for the resulting clip.')
    if not args.internal_check and not (args.verify or args.warm or args.render):
        status = speech_model.speech_model_status()
        print(json.dumps(status))
        return 0 if status['ready'] else 2
    try:
        if args.internal_check:
            if args.work_directory is None or not args.work_directory.is_dir():
                raise ValueError('The private speech-check directory is unavailable.')
            if args.warm:
                speech_model.download_official_model()
            result = verify_existing_model(args.source, args.work_directory,
                                           start=args.start, duration=args.duration)
        else:
            result = bounded_verification(args.source, warm=args.warm,
                                          start=args.start, duration=args.duration,
                                          timeout=args.timeout)
            if args.render:
                from features.clip_studio import render_clip
                rendered = render_clip(str(args.source.resolve()),
                    {'start': args.start, 'end': args.start+args.duration},
                    out_dir=str(args.output_dir.resolve()), local_only=True,
                    timeout=min(args.timeout, 1800))
                if (rendered.get('build') or {}).get('transcript_reused') is not False:
                    raise ValueError('The render did not perform a fresh transcription.')
                result.update(check='offline_real_inference_and_render',
                              render_qa=(rendered.get('summary') or {}).get('qa_status'))
        print(json.dumps(result))
        return 0
    except Exception:
        # Provider exceptions can contain signed download URLs or private paths.
        print(json.dumps({'ready': False, 'reason': 'verification_failed',
                          'message': 'Speech verification failed. Check model access, installed weights, speech audio and server resources; no live inference is verified by this result.'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
