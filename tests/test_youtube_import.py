"""No real YouTube/network request: exercise confinement and dispatch contracts."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import video_sources as sources
import youtube_import as youtube


VIDEO='AbCdEfGh_12'
WATCH='https://www.youtube.com/watch?v='+VIDEO
MEDIA='https://rr1---sn-example.googlevideo.com/videoplayback?signed=private'


def info(**changes):
    return {'duration':30,'is_live':False,'formats':[
        {'protocol':'https','ext':'mp4','vcodec':'avc1','acodec':'mp4a','height':360,'filesize':1024,'url':MEDIA}],**changes}


class YouTubeImportTests(unittest.TestCase):
    def test_only_single_supported_video_shapes_are_normalized(self):
        for url in (WATCH,'https://youtu.be/'+VIDEO+'?t=12','https://youtube.com/shorts/'+VIDEO):
            self.assertEqual(youtube.validate_youtube_url(url),WATCH)
            with patch.object(youtube,'enabled',return_value=True):
                self.assertEqual(sources.validate_import_url(url),WATCH)
        for url in ('http://youtube.com/watch?v='+VIDEO,WATCH+'&list=other',WATCH+'#t=2',
                    'https://secret:token@youtube.com/watch?v='+VIDEO,
                    'https://youtube.com/live/'+VIDEO,'https://youtu.be/no',
                    'https://www.youtube.com.evil.org/watch?v='+VIDEO):
            with self.subTest(url=url),self.assertRaises(sources.VideoSourceError):
                youtube.validate_youtube_url(url)

    def test_progressive_selection_rejects_live_playlists_fragments_and_foreign_hosts(self):
        self.assertEqual(youtube._select_progressive(info(),2048)['url'],MEDIA)
        for change in ({'is_live':True},{'_type':'playlist'},{'duration':float('nan')},{'duration':2}):
            with self.assertRaises(sources.VideoSourceError):youtube._select_progressive(info(**change),2048)
        for change in ({'url':'https://evil.org/a.mp4'},{'url':'http://rr1.googlevideo.com/a'},
                       {'protocol':'m3u8_native'},{'fragments':[{}]},{'manifest_url':'https://youtube.com/manifest'},
                       {'acodec':'none'},{'has_drm':True},{'filesize':4096},{'height':2160}):
            candidate=info();candidate['formats'][0].update(change)
            with self.assertRaises(sources.VideoSourceError):youtube._select_progressive(candidate,2048)

    def test_every_extractor_url_is_official_https_without_credentials(self):
        for url in (WATCH,'https://youtubei.googleapis.com/youtubei/v1/player',MEDIA):
            self.assertTrue(youtube._allowed_url(url).hostname)
        for url in ('file:///etc/passwd','https://127.0.0.1/a','https://youtube.com.evil.org/a',
                    'http://youtube.com/a','https://user:secret@youtube.com/a','https://youtube.com:444/a',None):
            with self.assertRaises(sources.VideoSourceError):youtube._allowed_url(url)
        with self.assertRaises(sources.VideoSourceError):youtube._allowed_url(WATCH,media=True)

    def test_default_and_proxy_paths_never_start_extractor(self):
        with patch.dict(os.environ,{'AUTOPOST_YOUTUBE_IMPORT_ENABLED':'false'}),patch.object(youtube.subprocess,'run',side_effect=AssertionError('child started')):
            with self.assertRaises(sources.VideoSourceError):youtube.import_youtube(WATCH,'ignored')
            with self.assertRaises(sources.VideoSourceError):sources.validate_import_url(WATCH)
        with patch.dict(os.environ,{'AUTOPOST_YOUTUBE_IMPORT_ENABLED':'true'}),patch.object(sources,'_proxy_configured',return_value=True):
            self.assertFalse(youtube.enabled())

    def test_dispatch_preserves_direct_import_and_uses_confined_youtube(self):
        with patch.object(youtube,'import_youtube',return_value={'extension':'.mp4'}) as call:
            self.assertEqual(sources.import_video_dispatch(WATCH,'target')['extension'],'.mp4')
            self.assertEqual(call.call_args.args,(WATCH,'target'))
        with patch.object(sources,'import_video',return_value={'extension':'.webm'}) as call:
            self.assertEqual(sources.import_video_dispatch('https://cdn.example.org/file.webm','target')['extension'],'.webm')
            call.assert_called_once()

    def test_child_process_is_isolated_bounded_and_media_redirects_stay_confined(self):
        completed=Mock(returncode=0,stdout=json.dumps({'url':MEDIA,'duration':30}))
        with patch.dict(os.environ,{'META_APP_SECRET':'must-not-reach-child','OPENAI_API_KEY':'must-not-reach-child'}),patch.object(youtube,'enabled',return_value=True),patch.object(youtube.subprocess,'run',return_value=completed) as child,patch.object(sources,'import_video',return_value={'extension':'.mp4','host':'rr1.googlevideo.com'}) as download:
            result=youtube.import_youtube(WATCH,'private-target',max_bytes=2048,timeout=120)
        self.assertEqual(result['extension'],'.mp4')
        self.assertIn('-I',child.call_args.args[0])
        self.assertEqual(child.call_args.kwargs['env']['YTDLP_NO_PLUGINS'],'1')
        self.assertNotIn('META_APP_SECRET',child.call_args.kwargs['env'])
        self.assertNotIn('OPENAI_API_KEY',child.call_args.kwargs['env'])
        self.assertLessEqual(child.call_args.kwargs['timeout'],65)
        self.assertEqual(download.call_args.kwargs['_allowed_hosts'],('googlevideo.com',))
        self.assertNotIn('signed',json.dumps(result))

    def test_child_failure_is_sanitized_and_no_download_occurs(self):
        with patch.object(youtube,'enabled',return_value=True),patch.object(youtube.subprocess,'run',side_effect=subprocess.TimeoutExpired('private-source-url',1)),patch.object(sources,'import_video') as download:
            with self.assertRaises(sources.VideoSourceError) as error:youtube.import_youtube(WATCH,'private-target')
        self.assertNotIn('private-source-url',str(error.exception));download.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('yt_dlp'),'optional official extractor package absent')
    def test_real_official_extractor_construction_has_no_plugins_default_extractors_or_js(self):
        from yt_dlp import YoutubeDL
        from yt_dlp.extractor.youtube import YoutubeIE
        with patch.dict(os.environ,{},clear=False),patch.object(YoutubeDL,'add_default_info_extractors',side_effect=AssertionError('generic extractors enabled')),patch.object(YoutubeIE,'extract',return_value=info()) as extract,patch.object(youtube._MetadataTransport,'request',side_effect=AssertionError('unexpected live network')):
            result=youtube._extract(WATCH,2048)
            self.assertEqual(os.environ['YTDLP_NO_PLUGINS'],'1')
        self.assertEqual(result['url'],MEDIA);extract.assert_called_once_with(WATCH)

    @unittest.skipUnless(importlib.util.find_spec('yt_dlp'),'optional official extractor package absent')
    def test_pinned_metadata_requests_strip_auth_cookie_and_revalidate_redirect(self):
        from yt_dlp.networking import Request
        class Response:
            status=200
            def __init__(self):self.fp=io.BytesIO(b'{"ok":true}')
            def getheader(self,key,default=None):return default
            def getheaders(self):return [('Content-Type','application/json')]
            def read1(self,n):return self.fp.read(n)
            read=read1
        connection=Mock();connection.getresponse.return_value=Response()
        with patch.object(sources,'_proxy_configured',return_value=False),patch.object(sources,'_public_addresses',return_value=['8.8.8.8']) as dns,patch.object(sources,'_PinnedHTTPS',return_value=connection):
            response=youtube._MetadataTransport().request(Request(WATCH,headers={'Cookie':'private','Authorization':'secret'}))
        self.assertEqual(response.read(),b'{"ok":true}')
        self.assertNotIn('Cookie',connection.request.call_args.kwargs['headers'])
        self.assertNotIn('Authorization',connection.request.call_args.kwargs['headers'])
        dns.assert_called_once();connection.close.assert_called_once()

    @unittest.skipUnless(importlib.util.find_spec('yt_dlp'),'optional official extractor package absent')
    def test_metadata_foreign_redirect_is_rejected_before_second_connection(self):
        from yt_dlp.networking import Request
        response=Mock(status=302)
        response.getheader.side_effect=lambda key,default=None:'https://metadata.internal/private' if key=='Location' else default
        connection=Mock();connection.getresponse.return_value=response
        with patch.object(sources,'_proxy_configured',return_value=False),patch.object(sources,'_public_addresses',return_value=['8.8.8.8']) as dns,patch.object(sources,'_PinnedHTTPS',return_value=connection) as factory:
            with self.assertRaises(sources.VideoSourceError):youtube._MetadataTransport().request(Request(WATCH))
        dns.assert_called_once();factory.assert_called_once();connection.close.assert_called_once()


if __name__=='__main__':unittest.main()
