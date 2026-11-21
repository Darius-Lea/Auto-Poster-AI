"""Bounded, pinned HTTPS imports of direct MP4/WebM files for Clip Studio.

This module does not scrape watch pages, run yt-dlp, accept manifests or
follow media references. Call import_video from the background worker only.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import re
import socket
import ssl
import subprocess
import threading
import time
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_BYTES = 200 * 1024 * 1024
MAX_REDIRECTS = 4
_PROXIES = ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy')
_PAGE_HOSTS = ('youtube.com', 'youtu.be', 'kick.com', 'twitch.tv', 'facebook.com',
               'fb.watch', 'instagram.com', 'tiktok.com', 'x.com', 'twitter.com', 'vimeo.com')
_LOCAL_SUFFIXES = ('.local', '.internal', '.localhost', '.lan', '.home', '.test', '.invalid', '.example', '.onion')
_VIDEO_TYPES = {'video/mp4', 'application/mp4', 'video/webm', 'application/octet-stream', 'binary/octet-stream'}


class VideoSourceError(ValueError):
    """A safe, customer-facing import failure without credentials or URLs."""


def _proxy_configured():
    return any(os.getenv(name) for name in _PROXIES)


def capabilities():
    try:
        from youtube_import import enabled as youtube_enabled
        youtube = youtube_enabled()
    except ImportError:
        youtube = False
    return {
        'direct_video': not _proxy_configured(),
        'formats': ['mp4', 'webm'], 'max_bytes': MAX_BYTES,
        'watch_pages': youtube, 'youtube': youtube, 'kick': False,
        'message': ('Link imports need a compatible service network setup. Upload the video file instead.'
                    if _proxy_configured() else
                    ('Paste a supported public YouTube video or a direct HTTPS MP4/WebM file link. Not every YouTube format is supported; upload the original file if needed.' if youtube else
                     'Paste a direct public HTTPS MP4 or WebM file link. YouTube imports are not enabled; Kick and other watch-page links are unsupported.')),
    }


def _public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Transition mechanisms can encode an otherwise private IPv4 target.
        # Customer CDN hosts do not need mapped, 6to4, Teredo or NAT64 routes.
        if (address.ipv4_mapped or address.sixtofour or address.teredo
                or address in ipaddress.ip_network('64:ff9b::/96')
                or address in ipaddress.ip_network('64:ff9b:1::/48')):
            return False
    return bool(address.is_global and not (address.is_private or address.is_reserved or
                address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified))


def validate_video_url(value):
    """Validate URL shape without making a network request; return normalized URL.

    Public DNS is separately checked and pinned immediately before every GET.
    Query strings are accepted for expiring CDN links and never returned in
    customer metadata, exception messages or request logs by this module.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        raise VideoSourceError('Use a direct public HTTPS video file link.')
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower()
        port = parsed.port
    except (ValueError, UnicodeError):
        raise VideoSourceError('Use a direct public HTTPS video file link.') from None
    if (parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None
            or parsed.fragment or port not in (None, 443) or host.endswith('.') or '%' in host
            or host == 'localhost' or host.endswith(_LOCAL_SUFFIXES)):
        raise VideoSourceError('Use a public HTTPS video link without login details, a fragment or a custom port.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or '.' not in host or any(not part or len(part) > 63 or part.startswith('-') or part.endswith('-')
                                  or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in part)
                                  for part in host.split('.')):
            raise VideoSourceError('Use a public HTTPS video file hostname.')
    else:
        # Literal addresses are unnecessary for customer CDN imports and can
        # complicate certificate/SNI handling. DNS names receive public-IP checks.
        raise VideoSourceError('Use a public HTTPS video file hostname, rather than an IP address.')
    if any(host == name or host.endswith('.' + name) for name in _PAGE_HOSTS):
        raise VideoSourceError('That is a watch-page link. Upload your permitted video file, or use a direct HTTPS MP4 or WebM file link.')
    # Preserve existing signed-query escapes while making an ordinary pasted
    # Unicode path a valid HTTP request target. Never decode percent escapes.
    path = quote(parsed.path or '/', safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return urlunsplit(('https', host, path, query, ''))


def _public_addresses(host, timeout=8):
    # A daemon resolver gives the import an actual deadline even when the
    # operating-system resolver stalls. It never opens the download connection.
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            result.put((True, records))
        except OSError:
            result.put((False, None))

    threading.Thread(target=resolve, daemon=True, name='video-source-dns').start()
    try:
        ok, records = result.get(timeout=timeout)
    except queue.Empty:
        raise VideoSourceError('The video host took too long to respond. Try uploading the file.') from None
    if not ok:
        raise VideoSourceError('The video host could not be reached. Check the link or upload the file.')
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise VideoSourceError('Private, local and reserved video addresses are not allowed.')
    return addresses


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            # HTTPConnection may clear .sock for a Connection: close response
            # while HTTPResponse still owns its buffered socket. Retain this
            # handle solely so a total-deadline watchdog can interrupt reads.
            self._download_socket = self.sock
        except BaseException:
            raw.close()
            raise


def _signature(path):
    with path.open('rb') as stream:
        header = stream.read(4096)
    if len(header) >= 12 and header[4:8] == b'ftyp':
        return '.mp4'
    if header.startswith(b'\x1a\x45\xdf\xa3') and b'\x42\x82\x84webm' in header:
        return '.webm'
    raise VideoSourceError('The link did not return an MP4 or WebM video file. Use a direct download link or upload the file.')


def _probe(path, timeout):
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-format_whitelist', 'mov,matroska,webm',
            '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(path),
        ], capture_output=True, text=True, timeout=timeout, check=False)
        info = json.loads(result.stdout)
        duration = float(info['format']['duration'])
        kinds = {row.get('codec_type') for row in info.get('streams', [])}
        if result.returncode or not math.isfinite(duration) or not 15 <= duration <= 5400 or not {'audio', 'video'} <= kinds:
            raise ValueError
        return duration
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        raise VideoSourceError('The link must provide a readable video with speech audio, between 15 seconds and 90 minutes long.') from None


def import_video(url, destination, *, max_bytes=MAX_BYTES, timeout=180, _allowed_hosts=None):
    """Download a direct public video to a new fixed private destination.

    Never overwrite an existing path. On failure remove only the file created
    by this call. Caller supplies the private job directory, not user input.
    Return extension/bytes/duration/mime_type/host, never a signed source URL.
    """
    current = validate_video_url(url)
    if _proxy_configured():
        raise VideoSourceError('Link imports are unavailable with this service network setup. Upload the video file instead.')
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
        raise VideoSourceError('Choose a video size limit between 1 byte and 200 MB.')
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        raise VideoSourceError('Choose a valid video import time limit.') from None
    if not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise VideoSourceError('Choose a video import time limit between 1 and 300 seconds.')
    target = Path(destination)
    created = False
    deadline = time.monotonic() + timeout
    active = {'connection': None}
    expired = threading.Event()

    def interrupt():
        # A read timeout alone is not a total bound: a server can trickle
        # headers/body indefinitely. Interrupt the actual TLS socket at expiry.
        expired.set()
        connection = active['connection']
        transport = getattr(connection, '_download_socket', None) or getattr(connection, 'sock', None)
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    watchdog = threading.Timer(timeout, interrupt)
    watchdog.daemon = True

    def remaining(cap=10):
        left = deadline - time.monotonic()
        if expired.is_set() or left <= 0:
            raise VideoSourceError('The video download took too long. Try a smaller file or upload it instead.')
        return min(cap, left)

    try:
        watchdog.start()
        for redirect in range(MAX_REDIRECTS + 1):
            parsed = urlsplit(validate_video_url(current))
            if _allowed_hosts is not None and not any(parsed.hostname==host or parsed.hostname.endswith('.'+host) for host in _allowed_hosts):
                raise VideoSourceError('The video redirected outside its supported media service. Upload the original file instead.')
            addresses = _public_addresses(parsed.hostname, timeout=remaining(8))
            connection = _PinnedHTTPS(parsed.hostname, addresses[0], timeout=remaining())
            active['connection'] = connection
            try:
                remaining()
                connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''), headers={
                    'Accept': 'video/mp4,video/webm,application/octet-stream',
                    'Accept-Encoding': 'identity', 'User-Agent': 'AutoPosterAI-VideoImporter/1.0',
                })
                response = connection.getresponse()
                remaining()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location')
                    if not location or redirect == MAX_REDIRECTS:
                        raise VideoSourceError('The video link redirects too many times or has an invalid redirect.')
                    current = validate_video_url(urljoin(current, location))
                    continue
                if response.status != 200:
                    raise VideoSourceError('The video file could not be downloaded. Check its sharing permissions or upload the file.')
                if response.getheader('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                    raise VideoSourceError('The video link must return an uncompressed file download.')
                media_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
                if media_type not in _VIDEO_TYPES:
                    raise VideoSourceError('The link returned a webpage or unsupported file. Use a direct MP4 or WebM download link.')
                length = response.getheader('Content-Length')
                if length is not None and (not re.fullmatch(r'[0-9]{1,20}', length) or int(length) > max_bytes):
                    raise VideoSourceError('Keep the linked video within the service upload size limit.')
                size = 0
                with target.open('xb') as output:
                    created = True
                    os.chmod(target, 0o600)
                    while True:
                        if connection.sock:
                            connection.sock.settimeout(remaining())
                        # read1 returns after a transport read; read(n) can
                        # repeatedly refill an internal buffer before returning.
                        read = getattr(response, 'read1', response.read)
                        chunk = read(min(65536, max_bytes + 1 - size))
                        remaining()
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise VideoSourceError('Keep the linked video within the service upload size limit.')
                        output.write(chunk)
                if not size or length is not None and size != int(length):
                    raise VideoSourceError('The video download was empty or incomplete. Try a fresh link or upload the file.')
                extension = _signature(target)
                if media_type in ('video/mp4', 'application/mp4') and extension != '.mp4' or media_type == 'video/webm' and extension != '.webm':
                    raise VideoSourceError('The download type does not match its video file. Upload the original file instead.')
                duration = _probe(target, timeout=remaining(15))
                return {'extension': extension, 'bytes': size, 'duration': duration,
                        'mime_type': 'video/mp4' if extension == '.mp4' else 'video/webm',
                        'host': parsed.hostname}
            except (OSError, http.client.HTTPException, UnicodeError):
                remaining()
                raise VideoSourceError('The video download failed. Check the connection or upload the file.') from None
            finally:
                connection.close()
                active['connection'] = None
        raise VideoSourceError('The video link redirects too many times.')
    except BaseException as exc:
        if created:
            target.unlink(missing_ok=True)
        if isinstance(exc, (OSError, http.client.HTTPException, UnicodeError)):
            raise VideoSourceError('The video download failed. Check the connection or upload the file.') from None
        raise
    finally:
        watchdog.cancel()


def validate_import_url(url):
    """Validate one supported input without network access or extractor loading."""
    from youtube_import import is_youtube_url, validate_youtube_url, enabled
    if is_youtube_url(url):
        normalized=validate_youtube_url(url)
        if not enabled():
            raise VideoSourceError('YouTube imports are not enabled on this service. Upload your permitted video file instead.')
        return normalized
    return validate_video_url(url)


def import_video_dispatch(url,destination,*,max_bytes=MAX_BYTES,timeout=180):
    from youtube_import import is_youtube_url, import_youtube
    if is_youtube_url(url):
        return import_youtube(url,destination,max_bytes=max_bytes,timeout=timeout)
    return import_video(url,destination,max_bytes=max_bytes,timeout=timeout)
