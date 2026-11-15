"""Direct-video imports use synthetic HTTPS transports, never live customer URLs."""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import video_sources as sources


class Response:
    def __init__(self, body=b'\x00\x00\x00\x18ftypisom' + b'\x00' * 30,
                 *, status=200, headers=None):
        self.status = status
        self.body = io.BytesIO(body)
        self.headers = {'Content-Type': 'video/mp4', **(headers or {})}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, count):
        return self.body.read(count)

    def read1(self, count):
        return self.body.read(count)


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.sock = None
        self.closed = False

    def request(self, method, path, headers):
        self.calls.append((method, path, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class VideoSourcesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / 'source.bin'
        self.proxy = patch.object(sources, '_proxy_configured', return_value=False)
        self.proxy.start(); self.addCleanup(self.proxy.stop)
        self.dns = patch.object(sources, '_public_addresses', return_value=['8.8.8.8'])
        self.lookup = self.dns.start(); self.addCleanup(self.dns.stop)
        self.probe = patch.object(sources, '_probe', return_value=20.0)
        self.probe_call = self.probe.start(); self.addCleanup(self.probe.stop)

    def download(self, *responses, **kwargs):
        transports = [Transport(response) for response in responses]
        with patch.object(sources, '_PinnedHTTPS', side_effect=transports) as factory:
            result = sources.import_video('https://video.example.org/source.mp4?signature=private-value', self.target, **kwargs)
        return result, transports, factory

    def test_url_shape_rejects_private_watch_pages_and_credentials_without_network(self):
        for url in ('http://video.example.org/file.mp4', 'https://127.0.0.1/a',
                    'https://[::1]/a', 'https://localhost/a', 'https://metadata.internal/a',
                    'https://192.168.1.10.nip.io/a#frag', 'https://user:secret@video.example.org/a',
                    'https://video.example.org:444/a', 'https://video.example.org./a',
                    'https://youtube.com/watch?v=abc', 'https://www.youtube.com/watch?v=abc',
                    'https://youtu.be/abc', 'https://kick.com/channel',
                    'https://video.example.org/a\nHeader: value', 'https://video.example.org\\@localhost/a'):
            with self.subTest(url=url), self.assertRaises(sources.VideoSourceError):
                sources.validate_video_url(url)
        self.lookup.assert_not_called()
        self.assertEqual(sources.validate_video_url('https://VIDEO.EXAMPLE.ORG:443/clip.mp4?signature=a%2Bb'),
                         'https://video.example.org/clip.mp4?signature=a%2Bb')
        self.assertEqual(sources.validate_video_url('https://video.example.org/café.mp4'),
                         'https://video.example.org/caf%C3%A9.mp4')

    def test_success_saves_only_validated_file_and_returns_no_signed_url(self):
        body = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 30
        result, transports, factory = self.download(Response(body, headers={'Content-Length': str(len(body))}))
        self.assertEqual(self.target.read_bytes(), body)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result, {'extension': '.mp4', 'bytes': len(body), 'duration': 20.0,
                                 'mime_type': 'video/mp4', 'host': 'video.example.org'})
        self.assertNotIn('private-value', json.dumps(result))
        self.assertEqual(transports[0].calls[0][0], 'GET')
        self.assertNotIn('Cookie', transports[0].calls[0][2])
        self.assertNotIn('Authorization', transports[0].calls[0][2])
        self.assertTrue(transports[0].closed)
        self.assertEqual(factory.call_args.args[:2], ('video.example.org', '8.8.8.8'))

    def test_webm_signature_is_accepted_and_wrong_content_type_is_rejected(self):
        result, _, _ = self.download(Response(b'\x1a\x45\xdf\xa3\x42\x82\x84webm' + b'\x00' * 30,
                                             headers={'Content-Type': 'video/webm'}))
        self.assertEqual(result['extension'], '.webm')
        self.target.unlink()
        with self.assertRaises(sources.VideoSourceError):
            self.download(Response(headers={'Content-Type':'video/webm'}))
        self.assertFalse(self.target.exists())

    def test_every_redirect_is_revalidated_and_dns_pinned(self):
        result, transports, factory = self.download(
            Response(status=302, headers={'Location':'https://cdn.example.org/media.mp4?token=another-private'}),
            Response())
        self.assertEqual(result['host'], 'cdn.example.org')
        self.assertEqual([call.args[0] for call in self.lookup.call_args_list],
                         ['video.example.org', 'cdn.example.org'])
        self.assertEqual([call.args[:2] for call in factory.call_args_list],
                         [('video.example.org', '8.8.8.8'), ('cdn.example.org', '8.8.8.8')])
        self.assertTrue(all(transport.closed for transport in transports))

    def test_private_redirect_is_rejected_before_second_fetch(self):
        for location in ('http://cdn.example.org/a', 'https://127.0.0.1/a', 'https://host.internal/a'):
            with self.subTest(location=location), self.assertRaises(sources.VideoSourceError):
                self.download(Response(status=302, headers={'Location':location}))
            self.assertFalse(self.target.exists())
        self.lookup.side_effect = [['8.8.8.8'], sources.VideoSourceError('Private address')]
        with self.assertRaises(sources.VideoSourceError):
            self.download(Response(status=302, headers={'Location':'https://cdn.example.org/a'}))
        self.assertFalse(self.target.exists())

    def test_untrusted_types_compression_status_and_redirect_loops_fail(self):
        cases = [Response(b'<html>Login</html>', headers={'Content-Type':'text/html'}),
                 Response(headers={'Content-Encoding':'gzip'}), Response(status=206),
                 Response(status=403), Response(status=302)]
        for response in cases:
            with self.subTest(status=response.status), self.assertRaises(sources.VideoSourceError):
                self.download(response)
            self.assertFalse(self.target.exists())
        with self.assertRaises(sources.VideoSourceError):
            self.download(*[Response(status=302, headers={'Location':'/again.mp4'}) for _ in range(5)])

    def test_missing_misleading_signatures_or_probe_failures_remove_partial_file(self):
        for body in (b'', b'<MPD><BaseURL>/private/source.mp4</BaseURL></MPD>',
                     b'#EXTM3U\n/private/source.mp4', b'not a video'):
            with self.subTest(body=body), self.assertRaises(sources.VideoSourceError):
                self.download(Response(body, headers={'Content-Type':'application/octet-stream'}))
            self.assertFalse(self.target.exists())
        self.probe_call.side_effect = sources.VideoSourceError('Bad media')
        with self.assertRaises(sources.VideoSourceError):
            self.download(Response())
        self.assertFalse(self.target.exists())

    def test_size_and_length_guards_apply_before_and_during_stream(self):
        for length in ('10000', '-1', 'one', '9' * 5000, '²'):
            with self.subTest(length=length), self.assertRaises(sources.VideoSourceError):
                self.download(Response(headers={'Content-Length':length}), max_bytes=40)
            self.assertFalse(self.target.exists())
        with self.assertRaises(sources.VideoSourceError):
            self.download(Response(), max_bytes=10)
        self.assertFalse(self.target.exists())
        with self.assertRaises(sources.VideoSourceError):
            self.download(Response(headers={'Content-Length':'50'}))
        self.assertFalse(self.target.exists())
        with self.assertRaises(sources.VideoSourceError):
            self.download(Response(), max_bytes=True)

    def test_existing_file_is_never_overwritten_or_removed(self):
        self.target.write_bytes(b'original private file')
        with self.assertRaises(sources.VideoSourceError):
            self.download(Response())
        self.assertEqual(self.target.read_bytes(), b'original private file')

    def test_proxy_is_not_bypassed_or_replaced(self):
        with patch.object(sources, '_proxy_configured', return_value=True), \
                patch.object(sources, '_PinnedHTTPS', side_effect=AssertionError('transport started')):
            with self.assertRaises(sources.VideoSourceError):
                sources.import_video('https://video.example.org/source.mp4', self.target)
            self.assertFalse(sources.capabilities()['direct_video'])
        self.assertFalse(self.target.exists())

    def test_network_error_never_echoes_source_query_or_server_message(self):
        with patch.object(sources, '_PinnedHTTPS', side_effect=OSError('private-value')):
            # Constructor failure is also sanitized by the import boundary.
            with self.assertRaises(sources.VideoSourceError) as failure:
                sources.import_video('https://video.example.org/source.mp4?token=private-value', self.target)
        self.assertNotIn('private-value', str(failure.exception))

    def test_total_deadline_interrupts_a_trickling_body(self):
        interrupted = threading.Event()
        fake_socket = Mock()
        fake_socket.shutdown.side_effect = lambda *_: interrupted.set()
        response = Response()
        def wait_for_shutdown(_count):
            if not interrupted.wait(3):
                raise AssertionError('deadline did not interrupt the transport')
            raise OSError('socket interrupted')
        response.read1 = wait_for_shutdown
        transport = Transport(response)
        transport.sock = fake_socket
        started = time.monotonic()
        with patch.object(sources, '_PinnedHTTPS', return_value=transport):
            with self.assertRaisesRegex(sources.VideoSourceError, 'too long'):
                sources.import_video('https://video.example.org/source.mp4', self.target, timeout=1)
        self.assertLess(time.monotonic()-started, 2.5)
        self.assertTrue(interrupted.is_set())
        self.assertFalse(self.target.exists())


class DNSAndDecoderTests(unittest.TestCase):
    def test_dns_rejects_any_private_or_transition_address(self):
        for address in ('127.0.0.1','10.0.0.1','192.168.1.1','169.254.169.254',
                        '::1','fd00::1','::ffff:127.0.0.1','64:ff9b::7f00:1',
                        '2002:7f00:1::1','2001:db8::1'):
            self.assertFalse(sources._public_ip(address), address)
        self.assertTrue(sources._public_ip('8.8.8.8'))
        self.assertTrue(sources._public_ip('2606:4700:4700::1111'))
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8',443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1',443))]
        with patch.object(sources.socket, 'getaddrinfo', return_value=records):
            with self.assertRaises(sources.VideoSourceError):
                sources._public_addresses('video.example.org')

    def test_dns_failure_is_bounded_and_sanitized(self):
        with patch.object(sources.socket, 'getaddrinfo', side_effect=OSError('secret resolver info')):
            with self.assertRaises(sources.VideoSourceError) as failure:
                sources._public_addresses('video.example.org')
        self.assertNotIn('secret', str(failure.exception))
        with patch.object(sources.socket, 'getaddrinfo', side_effect=lambda *_args,**_kw: time.sleep(.2)):
            started = time.monotonic()
            with self.assertRaises(sources.VideoSourceError):
                sources._public_addresses('video.example.org', timeout=.02)
            self.assertLess(time.monotonic()-started, .15)

    def test_pinned_tls_verifies_original_hostname(self):
        context = Mock()
        raw = Mock()
        with patch.object(sources.ssl, 'create_default_context', return_value=context), \
                patch.object(sources.socket, 'create_connection', return_value=raw) as connect:
            conn = sources._PinnedHTTPS('video.example.org','8.8.8.8',5)
            conn.connect()
        connect.assert_called_once_with(('8.8.8.8',443),timeout=5)
        context.wrap_socket.assert_called_once_with(raw,server_hostname='video.example.org')
        self.assertIs(conn.sock,context.wrap_socket.return_value)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is not installed')
    def test_actual_mp4_probe_accepts_media_and_rejects_disguised_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder)/'source.mp4'
            result = subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=8000','-t','16',
                '-c:v','libx264','-preset','ultrafast','-c:a','aac','-y',str(video)],
                capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr[-500:])
            self.assertEqual(sources._signature(video),'.mp4')
            self.assertAlmostEqual(sources._probe(video,10),16,places=1)
            manifest = Path(folder)/'disguised.mp4'
            manifest.write_text('<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><BaseURL>'+str(video)+'</BaseURL></Period></MPD>')
            with self.assertRaises(sources.VideoSourceError):sources._probe(manifest,10)


if __name__ == '__main__':
    unittest.main()
