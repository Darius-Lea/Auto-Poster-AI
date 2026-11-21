import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from features.clip_studio import clip_studio as clips


class ClipDesignAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'source.mp4';self.source.write_bytes(b'fixture')
        self.engine=self.root/'engine.py'
        self.engine.write_text('''import json,pathlib,sys
out=pathlib.Path(sys.argv[sys.argv.index('--out')+1])
out.write_bytes(b'fixture-video')
out.with_suffix('.srt').write_text('1\\n00:00:00,000 --> 00:00:01,000\\nHello\\n')
out.with_suffix('.transcript.json').write_text('[{"w":"Hello","s":0,"e":1}]')
out.with_suffix('.build.json').write_text(json.dumps({'qa_gate':{'status':'pass'},'args':sys.argv[1:],'font_family':'inter','caption_size':'large','caption_position':'lower'}))
''')

    def render(self, options):
        with patch.object(clips,'ENGINE_PY',str(self.engine)):
            return clips.render_clip(str(self.source),options,out_dir=str(self.root/'output'),local_only=True)

    def test_typography_reaches_engine_and_summary(self):
        result=self.render({'font_family':'inter','caption_size':'large','caption_position':'lower'})
        args=result['build']['args']
        for flag,value in [('--font-family','inter'),('--caption-size','large'),('--caption-position','lower')]:
            self.assertEqual(args[args.index(flag)+1],value)
        self.assertEqual(result['summary']['font_family'],'inter')
        self.assertEqual(result['summary']['caption_size'],'large')

    def test_existing_jobs_do_not_gain_typography_arguments(self):
        args=self.render({})['build']['args']
        self.assertNotIn('--font-family',args)
        self.assertNotIn('--caption-size',args)

    def test_untrusted_font_paths_and_unknown_values_never_start_engine(self):
        with patch.object(clips.subprocess,'Popen',side_effect=AssertionError('started')):
            for values in ({'font_family':'/private/font.ttf'},{'caption_size':'900'},{'caption_position':'outside'}):
                with self.assertRaises(clips.ClipStudioError):
                    self.render(values)
