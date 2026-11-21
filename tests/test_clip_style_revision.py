import json
import unittest
from pathlib import Path
import test_clip_web

class ClipStyleRevisionTests(unittest.TestCase):
 def setUp(self):
  self.fixture=test_clip_web.ClipWebTests('test_worker_success_private_download_and_brand')
  self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
 def test_style_revision_preserves_source_and_original(self):
  f=self.fixture;original=f.upload().json['job']['id'];f.finish()
  response=f.a.post('/clips/jobs/'+original+'/style',data={'_csrf':f.csrf,'confirmed':'yes','style':'minimal','font_family':'manrope','caption_size':'large','caption_position':'center','accent':'#226688'})
  self.assertEqual(response.status_code,202,response.data)
  child=response.json['job'];self.assertNotEqual(child['id'],original)
  self.assertEqual(child['options']['font_family'],'manrope');self.assertEqual(child['options']['caption_size'],'large')
  self.assertEqual(child['options']['start'],10);self.assertEqual(child['options']['end'],40)
  from clip_studio_web import job_dir
  self.assertTrue((job_dir(child['id'])/'transcript.json').is_file())
  self.assertEqual(json.loads((job_dir(child['id'])/'brand'/'kit.json').read_text())['accent'],'#226688')
  jobs=f.a.get('/clips/jobs').json['jobs'];self.assertEqual(next(job for job in jobs if job['id']==original)['status'],'ready')
 def test_invalid_or_unconfirmed_design_does_not_create_revision(self):
  f=self.fixture;identifier=f.upload().json['job']['id'];f.finish()
  for values in [{'confirmed':'no'},{'confirmed':'yes','font_family':'../../secret.ttf'}]:
   response=f.a.post('/clips/jobs/'+identifier+'/style',data={'_csrf':f.csrf,**values});self.assertEqual(response.status_code,400)
  self.assertEqual(len(f.a.get('/clips/jobs').json['jobs']),1)
