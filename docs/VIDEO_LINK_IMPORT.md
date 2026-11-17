# Supported video link imports

Clip Studio can import a direct public **HTTPS MP4 or WebM file URL** for background rendering. An expiring CDN download link may contain a query string. The file must contain audio and video, last between 15 seconds and 90 minutes and fit the configured limit (at most 200 MiB). Users must own the material or have permission to edit and use it.

Kick, Twitch and other watch-page links are not direct files and remain unsupported. Use a platform's permitted export/download option and upload the resulting file. The optional YouTube path below is deliberately narrower than unrestricted yt-dlp downloads.

`video_sources.validate_video_url(url)` validates URL shape without network access. `video_sources.import_video(url, destination, max_bytes=..., timeout=180)` runs in a background job and returns `extension`, `bytes`, `duration`, `mime_type` and `host`. The destination must be a new fixed path in that job's private directory. It is never overwritten and a failed import removes the partial file. Source URLs and signed query strings are not returned as customer-facing metadata or included in this module's error messages.

The web bridge should use `validate_import_url(url)` and
`import_video_dispatch(url, destination, ...)` to select the direct-file or
operator-enabled YouTube path. The existing direct-file functions retain their
strict contract and continue rejecting all watch pages.

## Optional public YouTube import

`AUTOPOST_YOUTUBE_IMPORT_ENABLED=true` enables a conservative public-video path
when yt-dlp is installed and the host has compatible direct egress. It is off by
default until the operator verifies a permitted real video on the deployment.
One `youtube.com/watch?v=...`, `youtu.be/...`, or `/shorts/...` video is accepted;
playlists, live streams, sign-in, private and protected content are excluded.

The server starts the installed official `YoutubeIE` in an isolated bounded
Python child. Plugins, JavaScript runtimes, remote components, default/generic
extractors, downloaders and postprocessors are disabled. Every extractor request
uses checked public DNS and pinned HTTPS restricted to official YouTube domains;
cookies and authorization headers are not forwarded. Metadata requests, bytes
and redirects are bounded. The child's deadline also bounds parsing/extraction.

Only a direct progressive MP4/WebM format containing both audio and video is
selected, up to 1080p and the configured size limit. Its `googlevideo.com` URL and
any redirects remain within that media domain and use the same pinned direct
importer and decoder validation. DASH/HLS, separate audio/video streams, external
FFmpeg network downloads, cookie-based login and anti-bot/JS challenge bypasses
are not implemented. Many modern YouTube videos may therefore require uploading
the original file instead. This is not a claim that every public YouTube link
works, nor an approved YouTube account/OAuth integration.

Sixteen direct-import tests and ten YouTube confinement/dispatch tests passed,
including the installed official extractor constructor and a real FFmpeg media
probe. Tests use fixture metadata/transports; no live YouTube extraction or
download was performed because provider access was blocked by this workspace's
network policy. The YouTube switch must stay off until that live acceptance test
has passed on the intended service.

Every redirect receives fresh URL and DNS validation. Every resolved address must be public; the TLS connection is pinned to a checked address while the certificate is verified against the original hostname. Redirects, total download time and byte count are bounded. No cookies, authorization headers or customer's browser login are sent. Only supported content types and MP4/WebM signatures are accepted. FFprobe is restricted to approved file containers before parsing and cannot load a DASH/HLS playlist or network protocols.

The importer refuses execution when an HTTP/HTTPS/ALL proxy is configured, matching the existing feed importer's boundary: this direct transport cannot prove DNS pinning through an arbitrary proxy and must not bypass the operator's network policy. A normal Render deployment with direct public egress can use it. Proxy-only deployments need an approved network transport that provides equivalent destination restrictions; file upload remains available meanwhile.

This is a source import, not automatic social publishing, a promise of viral engagement, or evidence that a live Render deployment has been tested. Original and rendered media remain private to the customer's workspace and require durable private storage and the existing account/job deletion controls.

## November 17 availability correction

YouTube importing is enabled by default when the customer app has its bundled extractor and a compatible network. The application requirements now install the tested extractor, and the Docker/Render customer configuration explicitly enables it. An operator can still disable it. Known single-video watch, short, embed and recorded-live URLs normalize to one video; playlist tracking parameters do not request a playlist. Actual livestreams, private/protected videos, unsupported adaptive-only formats and other unsupported platforms remain unavailable. This is not a claim that every public link works or that a live Render download was verified.
