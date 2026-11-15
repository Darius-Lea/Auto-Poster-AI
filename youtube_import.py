"""Confined, opt-in public YouTube metadata extraction and progressive import.

No yt-dlp downloader or generic extractor is used. All extractor networking goes
through the pinned transport below, in a bounded isolated child process.
"""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import parse_qs, urljoin, urlsplit

# The isolated child only imports modules from this server-owned source path.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_sources as direct

_ID = re.compile(r'[A-Za-z0-9_-]{11}\Z')
_HOSTS = ('youtube.com', 'youtu.be', 'youtubei.googleapis.com', 'googlevideo.com', 'ytimg.com')
_MESSAGE = 'This YouTube video could not be imported. Use an available public video you have permission to edit, or upload the original file.'


def enabled():
    if os.getenv('AUTOPOST_YOUTUBE_IMPORT_ENABLED', 'false').lower() != 'true':
        return False
    try:
        import importlib.util
        return importlib.util.find_spec('yt_dlp') is not None and not direct._proxy_configured()
    except (ImportError, ValueError):
        return False


def is_youtube_url(value):
    if not isinstance(value, str):
        return False
    try:
        return urlsplit(value).hostname in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be','www.youtu.be')
    except ValueError:
        return False


def validate_youtube_url(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value):
        raise direct.VideoSourceError('Paste a complete public YouTube video link.')
    try:
        parsed=urlsplit(value)
        if (parsed.scheme!='https' or parsed.port not in (None,443)
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or not is_youtube_url(value)):
            raise ValueError
        query=parse_qs(parsed.query)
        if any(key in query for key in ('list','index')):
            raise ValueError
        if parsed.hostname.endswith('youtu.be'):
            identifier=parsed.path.strip('/')
        elif parsed.path=='/watch':
            values=query.get('v',[])
            identifier=values[0] if len(values)==1 else ''
        elif parsed.path.startswith('/shorts/'):
            identifier=parsed.path[len('/shorts/'):].strip('/')
        else:
            raise ValueError
        if not _ID.fullmatch(identifier):
            raise ValueError
    except (ValueError, TypeError):
        raise direct.VideoSourceError('Use one public YouTube video link, without a playlist, login details or fragment.') from None
    return 'https://www.youtube.com/watch?v='+identifier


def _allowed_url(value, *, media=False):
    if not isinstance(value,str):
        raise direct.VideoSourceError(_MESSAGE)
    try:
        parsed=urlsplit(value)
        host=(parsed.hostname or '').lower()
        allowed=('googlevideo.com',) if media else _HOSTS
        if (not isinstance(value,str) or len(value)>16384 or any(ord(c)<=32 or ord(c)==127 for c in value)
                or '\\' in value or parsed.scheme!='https' or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None,443) or parsed.fragment
                or not any(host==name or host.endswith('.'+name) for name in allowed)):
            raise ValueError
    except (ValueError, TypeError):
        raise direct.VideoSourceError(_MESSAGE) from None
    return parsed


class _MetadataTransport:
    """Bound every official extractor request; redirects receive fresh DNS pins."""
    def __init__(self, timeout=60):
        self.deadline=time.monotonic()+timeout
        self.requests=0
        self.received=0

    def request(self, request):
        if direct._proxy_configured():
            raise direct.VideoSourceError(_MESSAGE)
        current=request.url
        method=request.method
        body=request.data
        if method not in ('GET','POST') or body is not None and (not isinstance(body,bytes) or len(body)>128*1024):
            raise direct.VideoSourceError(_MESSAGE)
        # Only public extractor metadata. Do not forward cookie/auth/proxy fields.
        headers={key:value for key,value in request.headers.items()
                 if key.lower() in ('content-type','user-agent','origin','referer','x-youtube-client-name','x-youtube-client-version')}
        headers.update({'Accept-Encoding':'identity','Connection':'close'})
        for redirect in range(5):
            self.requests+=1
            if self.requests>24 or time.monotonic()>=self.deadline:
                raise direct.VideoSourceError(_MESSAGE)
            parsed=_allowed_url(current)
            if method=='POST' and not (parsed.hostname.endswith('youtube.com') or parsed.hostname=='youtubei.googleapis.com'):
                raise direct.VideoSourceError(_MESSAGE)
            remaining=min(10,self.deadline-time.monotonic())
            addresses=direct._public_addresses(parsed.hostname,timeout=min(8,remaining))
            connection=direct._PinnedHTTPS(parsed.hostname,addresses[0],timeout=remaining)
            try:
                connection.request(method,parsed.path+('?' + parsed.query if parsed.query else ''),body=body,headers=headers)
                response=connection.getresponse()
                if response.status in (301,302,303,307,308):
                    location=response.getheader('Location')
                    if not location or redirect==4:
                        raise direct.VideoSourceError(_MESSAGE)
                    current=urljoin(current,location)
                    if response.status==303:
                        method,body='GET',None
                    continue
                if response.status!=200 or response.getheader('Content-Encoding','identity').lower() not in ('','identity'):
                    raise direct.VideoSourceError(_MESSAGE)
                data=bytearray()
                while True:
                    if time.monotonic()>=self.deadline:
                        raise direct.VideoSourceError(_MESSAGE)
                    read=getattr(response,'read1',response.read)
                    chunk=read(65536)
                    if not chunk:
                        break
                    data.extend(chunk);self.received+=len(chunk)
                    if len(data)>4*1024*1024 or self.received>16*1024*1024:
                        raise direct.VideoSourceError(_MESSAGE)
                from yt_dlp.networking import Response
                return Response(io.BytesIO(bytes(data)),url=current,headers=dict(response.getheaders()),status=200)
            finally:
                connection.close()
        raise direct.VideoSourceError(_MESSAGE)


def _select_progressive(info, max_bytes):
    if not isinstance(info,dict) or info.get('_type') in ('playlist','url','url_transparent') or info.get('is_live') or info.get('live_status') in ('is_live','is_upcoming'):
        raise direct.VideoSourceError(_MESSAGE)
    duration=info.get('duration')
    if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or not 15<=duration<=5400:
        raise direct.VideoSourceError(_MESSAGE)
    candidates=[]
    for fmt in info.get('formats',[]):
        if (not isinstance(fmt,dict) or fmt.get('protocol')!='https' or fmt.get('ext') not in ('mp4','webm')
                or not fmt.get('vcodec') or fmt['vcodec']=='none' or not fmt.get('acodec') or fmt['acodec']=='none'
                or fmt.get('has_drm') or fmt.get('fragments') or fmt.get('manifest_url')):
            continue
        try:
            _allowed_url(fmt.get('url'),media=True)
            length=fmt.get('filesize') or fmt.get('filesize_approx')
            if length is not None and (not isinstance(length,(int,float)) or length>max_bytes):
                continue
            height=fmt.get('height') or 0
            if not isinstance(height,(int,float)) or height>1080:
                continue
            candidates.append((height,fmt['url']))
        except direct.VideoSourceError:
            continue
    if not candidates:
        raise direct.VideoSourceError('This YouTube video has no supported direct audio-and-video file. Upload the original video instead.')
    candidates.sort(reverse=True)
    return {'url':candidates[0][1], 'duration':duration}


def _extract(url,max_bytes):
    # These settings apply only inside the isolated child, before yt-dlp import.
    os.environ['YTDLP_NO_PLUGINS']='1'
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.youtube import YoutubeIE
    transport=_MetadataTransport()
    class Quiet:
        def debug(self,*args,**kwargs):pass
        def warning(self,*args,**kwargs):pass
        def error(self,*args,**kwargs):pass
    class Confined(YoutubeDL):
        def urlopen(self,request):
            from yt_dlp.networking import Request
            if isinstance(request,str):request=Request(request)
            if not isinstance(request,Request):raise direct.VideoSourceError(_MESSAGE)
            return transport.request(request)
    options={'quiet':True,'no_warnings':True,'logger':Quiet(),'cachedir':False,
             'js_runtimes':{},'remote_components':[],'noplaylist':True,
             'skip_download':True,'extractor_retries':0,'retries':0,'socket_timeout':10,
             'extractor_args':{'youtube':{'player_client':['web']}},'no_color':True}
    with Confined(options,auto_init=False) as downloader:
        # No default/generic extractors, plugins, downloader or postprocessors.
        info=YoutubeIE(downloader).extract(url)
        return _select_progressive(info,max_bytes)


def import_youtube(url,destination,*,max_bytes=direct.MAX_BYTES,timeout=180):
    canonical=validate_youtube_url(url)
    if not enabled():
        raise direct.VideoSourceError('YouTube imports are not enabled on this service. Upload your permitted video file instead.')
    if isinstance(max_bytes,bool) or not isinstance(max_bytes,int) or not 1<=max_bytes<=direct.MAX_BYTES:
        raise direct.VideoSourceError(_MESSAGE)
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not 2<=timeout<=300:
        raise direct.VideoSourceError(_MESSAGE)
    started=time.monotonic()
    try:
        # The public extractor does not receive operator API credentials,
        # customer connection secrets or Python import/startup overrides.
        keep={'PATH','LANG','LC_ALL','TMPDIR','TMP','TEMP','SSL_CERT_FILE','SSL_CERT_DIR',
              'REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE','NO_PROXY','no_proxy',*direct._PROXIES}
        child_env={key:value for key,value in os.environ.items() if key in keep}
        child_env['YTDLP_NO_PLUGINS']='1'
        # A separate interpreter bounds extraction CPU/time and plugin state.
        result=subprocess.run([sys.executable,'-I',str(Path(__file__).resolve()),'--extract',canonical,str(max_bytes)],
            capture_output=True,text=True,timeout=min(65,timeout/2),check=False,env=child_env)
        if result.returncode or len(result.stdout)>20000:
            raise direct.VideoSourceError(_MESSAGE)
        selection=json.loads(result.stdout)
        _allowed_url(selection['url'],media=True)
        remaining=timeout-(time.monotonic()-started)
        if remaining<1:raise direct.VideoSourceError(_MESSAGE)
        return direct.import_video(selection['url'],destination,max_bytes=max_bytes,timeout=remaining,_allowed_hosts=('googlevideo.com',))
    except (subprocess.SubprocessError,OSError,ValueError,KeyError,TypeError):
        raise direct.VideoSourceError(_MESSAGE) from None


if __name__=='__main__':
    try:
        if len(sys.argv)!=4 or sys.argv[1]!='--extract' or direct._proxy_configured():
            raise ValueError
        print(json.dumps(_extract(validate_youtube_url(sys.argv[2]),int(sys.argv[3]))))
    except Exception:
        sys.exit(2)
