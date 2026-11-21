"""Exercise the clip process boundary and quality/export guards without model downloads."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from features.clip_studio import clip_studio as clips
from features.clip_studio.engine import clip_engine as engine


class ClipStudioAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'episode.mp4'
        self.source.write_bytes(b'synthetic media fixture')
        self.fake = self.root / 'fixture_engine.py'

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, status='pass', empty=False, delay=0):
        self.fake.write_text('''import json,os,pathlib,sys,time
if os.environ.get('CLIP_STUDIO_WORK_ROOT'):
    pathlib.Path(os.environ['CLIP_STUDIO_WORK_ROOT'],'private-audio.wav').write_bytes(b'private fixture audio')
time.sleep(%r)
out=pathlib.Path(sys.argv[sys.argv.index('--out')+1])
assert pathlib.Path(sys.argv[1]).is_file()
out.write_bytes(%r)
out.with_suffix('.srt').write_text('1\\n00:00:00,000 --> 00:00:01,000\\nHello\\n')
out.with_suffix('.build.json').write_text(json.dumps({'qa_gate':{'status':%r,'checked':3},'hook':'Hello'}))
out.with_suffix('.transcript.json').write_text('[{"w":"Hello","s":0,"e":1}]')
''' % (delay, b'' if empty else b'MP4 fixture', status))

    def render(self, **kwargs):
        with patch.object(clips, 'ENGINE_PY', str(self.fake)):
            return clips.render_clip(str(self.source), out_dir=str(self.root / 'output'), **kwargs)

    def test_local_render_returns_complete_artifacts_and_reusable_transcript(self):
        self.fixture()
        result = self.render(local_only=True)
        self.assertEqual(result['summary']['qa_status'], 'pass')
        for name in ('clip', 'srt', 'build_json', 'transcript'):
            self.assertTrue(Path(result[name]).is_file(), name)

    def test_relative_source_is_resolved_before_changing_process_directory(self):
        self.fixture()
        with patch.object(clips, 'ENGINE_PY', str(self.fake)):
            result = clips.render_clip(os.path.relpath(self.source), out_dir=str(self.root / 'elsewhere'))
        self.assertEqual(result['summary']['qa_status'], 'pass')

    def test_failed_or_skipped_quality_check_is_never_success(self):
        for status in ('fail', 'skipped', None):
            self.fixture(status=status)
            with self.assertRaises(clips.ClipStudioError):
                self.render()

    def test_missing_media_is_not_success_even_when_build_claims_pass(self):
        self.fixture(empty=True)
        with self.assertRaises(clips.ClipStudioError):
            self.render()

    def test_timeout_terminates_render_process(self):
        self.fixture(delay=30)
        with self.assertRaisesRegex(clips.ClipStudioError, 'time limit'):
            self.render(timeout=1,local_only=True)
        self.assertFalse((self.root/'output'/'work').exists())

    def test_hosted_calls_reject_remote_video_and_overlay_before_start(self):
        with patch.object(clips.subprocess, 'Popen', side_effect=AssertionError('engine started')):
            with self.assertRaises(clips.ClipStudioError):
                clips.render_clip('https://127.0.0.1/private', local_only=True)
            with self.assertRaises(clips.ClipStudioError):
                self.render(options={'overlay':'https://example.test/private'}, local_only=True)

    def test_paths_and_nonfinite_or_unbounded_times_are_rejected(self):
        self.fixture()
        for options in ({'start':float('nan')}, {'duration':121}, {'start':9,'end':2}, {'start':0,'end':999}):
            with self.assertRaises(clips.ClipStudioError):
                self.render(options=options)
        with self.assertRaises(clips.ClipStudioError):
            self.render(out_name='../elsewhere.mp4')

    def test_quality_gate_and_workspace_path_guard_the_legacy_outbox(self):
        result={'clip':str(self.source),'summary':{'qa_status':'skipped'}}
        with self.assertRaises(clips.ClipStudioError):
            clips.queue_clip_for_publishing(result, outbox_dir=str(self.root/'outbox'))
        result['summary']['qa_status']='pass'
        for key in ('../owner', '/absolute', 'a/b', '..', ''):
            with self.assertRaises(clips.ClipStudioError):
                clips.queue_clip_for_publishing(result, workspace_key=key, outbox_dir=str(self.root/'outbox'))
        one=clips.queue_clip_for_publishing(result,workspace_key='ws_customer',outbox_dir=str(self.root/'outbox'))
        two=clips.queue_clip_for_publishing(result,workspace_key='ws_customer',outbox_dir=str(self.root/'outbox'))
        self.assertNotEqual(one,two)
        payload=json.loads(Path(one).read_text())
        self.assertEqual(payload['media_type'],'video')
        self.assertEqual((Path(one).parent/payload['image']).read_bytes(),self.source.read_bytes())

    def test_saved_transcript_preserves_words_and_rejects_invalid_timing(self):
        path=self.root/'words.json'
        path.write_text('[{"w":"Hello","s":0,"e":1}]')
        self.assertEqual(engine.load_transcript(str(path)),[{'w':'Hello','s':0.0,'e':1.0}])
        for data in ([{'w':'Hello','s':1,'e':0}], [{'w':'Hello','s':float('nan'),'e':2}], []):
            path.write_text(json.dumps(data))
            with self.assertRaises(SystemExit):engine.load_transcript(str(path))

    def test_customer_brand_can_render_without_another_business_logo(self):
        from PIL import Image
        kit=self.root/'brand.json'
        kit.write_text(json.dumps({'name':'My Bread Business','accent':'#217A51','text_only':True}))
        brand=engine.load_brand(str(kit))
        self.assertIsNone(brand['logo'])
        self.assertEqual(brand['name'],'My Bread Business')
        with patch.object(engine,'run'):
            engine.render_card(str(self.root),brand)
        with Image.open(self.root/'card.png') as card:
            self.assertEqual(card.size,(1080,1920))
            self.assertEqual(card.getpixel((450,700)),(33,122,81))

    def test_hosted_decoder_cannot_follow_network_playlists(self):
        with patch.dict(os.environ,{'CLIP_STUDIO_LOCAL_ONLY':'true'}), patch.object(engine.subprocess,'run') as run:
            run.return_value.returncode=0
            engine.run(['ffmpeg','-i','upload.mp4','out.mp4'])
            self.assertEqual(run.call_args.args[0][:3],['ffmpeg','-protocol_whitelist','file,pipe'])
            with self.assertRaises(SystemExit):engine.ingest('https://example.test/video',str(self.root))
            with self.assertRaises(SystemExit):engine.fetch_overlay_image('https://example.test/image.png',str(self.root))

    def test_hosted_source_decoder_has_a_container_allowlist(self):
        with patch.dict(os.environ,{'CLIP_STUDIO_LOCAL_ONLY':'true'}), patch.object(engine,'_HOSTED_SOURCE',str(self.source)), patch.object(engine.subprocess,'run') as run:
            run.return_value.returncode=0
            engine.run(['ffmpeg','-i',str(self.source),'out.mp4'])
            command=run.call_args.args[0]
            self.assertLess(command.index('-format_whitelist'),command.index('-i'))
            self.assertEqual(command[command.index('-format_whitelist')+1],'mov,matroska,webm')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg toolchain unavailable')
    def test_real_dash_manifest_disguised_as_mp4_cannot_reference_other_local_files(self):
        manifest=self.root/'private.mpd'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=32x32:r=1:d=2',
                        '-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','2','-c:v','libx264','-c:a','aac','-f','dash',str(manifest)],check=True)
        self.source.write_bytes(manifest.read_bytes())
        # Without the demuxer allowlist FFprobe follows the referenced
        # local init/segments and reports this as a playable video.
        with patch.dict(os.environ,{'CLIP_STUDIO_LOCAL_ONLY':'true'}):
            with self.assertRaises(SystemExit):engine.probe(str(self.source))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg toolchain unavailable')
    def test_real_ffmpeg_trimmed_pieces_join_with_constant_frame_rate(self):
        paths=[]
        for index,duration in enumerate((.5,.5,1)):
            path=self.root/('piece%d.mp4'%index)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=navy:s=160x90:r=24',
                            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000',
                            '-t',str(duration),'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)
            paths.append(str(path))
        with patch.dict(os.environ,{'CLIP_STUDIO_LOCAL_ONLY':'true'}):
            joined=engine.join_pieces(paths[:2],paths[2],1,str(self.root),counts=[12,12])
        result=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,width,height,avg_frame_rate',
                               '-of','json',joined],capture_output=True,text=True,check=True)
        streams=json.loads(result.stdout)['streams']
        self.assertEqual({s['codec_type'] for s in streams},{'video','audio'})
        self.assertEqual(next(s['avg_frame_rate'] for s in streams if s['codec_type']=='video'),'24/1')


if __name__=='__main__':unittest.main()
