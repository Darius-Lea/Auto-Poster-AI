"""Model readiness/inference controls; never downloads weights during tests."""
import contextlib
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from features.clip_studio import speech_model as speech
from features.clip_studio.engine import clip_engine as engine
from scripts.qa import clip_transcription_smoke as doctor


class SpeechModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)/'model'
        self.folder.mkdir()
        env = patch.dict(os.environ, {'AUTOPOST_CLIP_MODEL':'base.en',
            'AUTOPOST_CLIP_MODEL_DIR':str(self.folder), 'AUTOPOST_CLIP_CPU_THREADS':'2'})
        env.start(); self.addCleanup(env.stop)
        self.runtime = {'faster-whisper':'fixture1','ctranslate2':'fixture2','tokenizers':'fixture3'}
        runtime = patch.object(speech, 'runtime_signature', return_value=self.runtime)
        runtime.start(); self.addCleanup(runtime.stop)

    def weights(self, vocabulary='vocabulary.txt'):
        for name in (*speech.REQUIRED_FILES, vocabulary):
            (self.folder/name).write_bytes(('fixture '+name).encode())

    def health(self):
        return speech.record_success(7, b'fixture audio')

    def test_missing_weights_and_unverified_files_never_report_ready(self):
        with patch.object(speech, 'download_official_model', side_effect=AssertionError('network')):
            self.assertEqual(speech.speech_model_status()['reason'], 'missing_weights')
            self.weights()
            self.assertEqual(speech.speech_model_status()['reason'], 'not_verified')
        with self.assertRaises(RuntimeError): speech.require_ready_model()

    def test_success_records_local_inference_and_changed_files_require_recheck(self):
        self.weights(); status = self.health()
        self.assertTrue(status['ready'])
        payload = json.loads((self.folder/speech.HEALTH_FILE).read_text())
        self.assertEqual(payload['word_count'], 7)
        self.assertEqual(len(payload['sha256']['model.bin']), 64)
        self.assertNotIn('fixture audio', json.dumps(payload))
        self.assertNotIn(self.tmp.name, json.dumps(status))
        (self.folder/'model.bin').write_bytes(b'changed model')
        self.assertFalse(speech.speech_model_status()['ready'])

    def test_runtime_upgrade_and_forged_status_are_not_ready(self):
        self.weights(); self.health()
        with patch.object(speech, 'runtime_signature', return_value={**self.runtime,'ctranslate2':'new'}):
            self.assertFalse(speech.speech_model_status()['ready'])
        marker = self.folder/speech.HEALTH_FILE
        payload = json.loads(marker.read_text()); payload['inference_passed'] = False
        marker.write_text(json.dumps(payload))
        self.assertFalse(speech.speech_model_status()['ready'])

    def test_vocabulary_json_supported_and_symlink_or_empty_weights_rejected(self):
        self.weights(vocabulary='vocabulary.json'); self.assertTrue(self.health()['ready'])
        (self.folder/'model.bin').unlink()
        outside = Path(self.tmp.name)/'elsewhere'; outside.write_bytes(b'private model')
        (self.folder/'model.bin').symlink_to(outside)
        self.assertFalse(speech.speech_model_status()['ready'])
        (self.folder/'model.bin').unlink(); (self.folder/'model.bin').write_bytes(b'')
        self.assertFalse(speech.speech_model_status()['ready'])

    def test_invalid_config_is_safe_and_short_inference_cannot_certify(self):
        with patch.dict(os.environ, {'AUTOPOST_CLIP_MODEL':'https://untrusted.test/model'}):
            result = speech.speech_model_status()
            self.assertEqual(result['reason'], 'invalid_configuration')
            self.assertNotIn('untrusted', json.dumps(result))
        self.weights()
        with self.assertRaises(ValueError): speech.record_success(2, b'audio')
        self.assertFalse((self.folder/speech.HEALTH_FILE).exists())

    def test_racing_weight_replacement_cannot_write_success(self):
        self.weights(); before = speech.fingerprints(self.folder)
        (self.folder/'config.json').write_bytes(b'new model config')
        with self.assertRaises(ValueError):
            speech.record_success(7, b'audio', expected_files=before)

    def test_offline_load_never_passes_a_model_name_or_allows_download(self):
        self.weights(); self.health()
        fake = SimpleNamespace(WhisperModel=lambda *args, **kwargs:(args,kwargs))
        with patch.dict(sys.modules, {'faster_whisper':fake}):
            args, options = speech.load_local_model()
        self.assertEqual(args, (str(self.folder),))
        self.assertTrue(options['local_files_only'])
        self.assertEqual(options['num_workers'], 1)
        self.assertEqual(options['cpu_threads'], 2)

    def test_hosted_transcription_fails_before_extracting_or_downloading(self):
        with patch.object(speech, 'load_local_model', side_effect=RuntimeError('not ready')):
            with patch.object(engine, 'run', side_effect=AssertionError('audio extraction')):
                with self.assertRaisesRegex(SystemExit, 'Speech recognition is not ready'):
                    engine.transcribe('does-not-matter.mp4', self.tmp.name)

    def test_explicit_operator_download_only_uses_configured_official_repository(self):
        self.weights()
        fake = SimpleNamespace(snapshot_download=lambda **kwargs: kwargs)
        with patch.dict(sys.modules, {'huggingface_hub':fake}):
            with patch.object(fake, 'snapshot_download', wraps=fake.snapshot_download) as download:
                self.assertEqual(speech.download_official_model(), self.folder)
        options = download.call_args.kwargs
        self.assertEqual(options['repo_id'], 'Systran/faster-whisper-base.en')
        self.assertEqual(options['local_dir'], str(self.folder))
        self.assertFalse(options['local_files_only'])
        self.assertNotIn('token', options)

    def test_default_doctor_is_status_only_and_exits_not_ready(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(doctor, 'bounded_verification', side_effect=AssertionError('inference')):
            self.assertEqual(doctor.main([]), 2)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'missing_weights')

    def test_doctor_separates_explicit_warmup_and_sanitizes_errors(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with patch.object(doctor, 'bounded_verification', return_value={'ready':True}) as verify:
                self.assertEqual(doctor.main(['--verify']), 0)
                self.assertFalse(verify.call_args.kwargs['warm'])
                self.assertEqual(doctor.main(['--warm']), 0)
                self.assertTrue(verify.call_args.kwargs['warm'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(doctor, 'bounded_verification', side_effect=RuntimeError('secret/private/token')):
            self.assertEqual(doctor.main(['--verify']), 2)
        self.assertNotIn('secret/private/token', output.getvalue())

    def test_doctor_timeout_kills_child_and_reaps_it(self):
        fake = SimpleNamespace(pid=345, communicate=lambda **kwargs:('',None))
        with patch.object(doctor.subprocess, 'Popen', return_value=fake):
            with patch.object(fake, 'communicate', side_effect=[subprocess.TimeoutExpired('check',1),('',None)]) as reap:
                with patch.object(doctor.os, 'killpg') as kill:
                    with self.assertRaisesRegex(ValueError, 'time limit'):
                        doctor.bounded_verification('fixture.mp4', timeout=1)
                    if os.name == 'posix': kill.assert_called_once_with(345, signal.SIGKILL)
                    self.assertEqual(reap.call_count, 2)


if __name__ == '__main__': unittest.main()
