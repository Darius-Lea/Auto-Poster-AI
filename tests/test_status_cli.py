import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import main


class StatusCliTests(unittest.TestCase):
    def test_status_modes_never_enter_publishing_or_send_new_post_summaries(self):
        city = {'city': 'Fort Wayne', 'workspace_key': 'fort-wayne', 'logo': 'unused',
                'sources': [{'adapter': 'allen_sheriff', 'url': 'https://source.example/roster'}]}
        client = Mock()
        client.check_page.return_value = {'id': '123', 'name': 'Fixture Page'}
        for preview, errors in [(False, []), (True, []), (False, ['Incomplete roster'])]:
            with self.subTest(preview=preview, errors=errors), tempfile.TemporaryDirectory() as folder:
                environment = {'PUBLISH_BACKEND': 'meta', 'META_PAGE_ID': '123',
                               'META_PAGE_ACCESS_TOKEN': 'fixture',
                               'AUTOPOST_DB': str(Path(folder) / 'state.db')}
                args = ['main.py', '--check-status'] + (['--dry-run'] if preview else [])
                with patch.dict(os.environ, environment, clear=True), \
                        patch.object(main, 'load_dotenv'), \
                        patch('sys.argv', args), \
                        patch.object(main, 'load_config', return_value=[city]), \
                        patch.object(main.FacebookClient, 'from_env', return_value=client), \
                        patch.object(main.roster_monitor, 'reconcile_city', return_value={
                            'updated': 0, 'planned': 0, 'errors': errors}) as reconcile, \
                        patch.object(main, 'process_city') as publish_pipeline, \
                        patch.object(main.notify, 'send_telegram_summary') as summary:
                    self.assertEqual(main.main(), 1 if errors else 0)
                self.assertEqual(reconcile.call_args.kwargs['dry_run'], preview)
                publish_pipeline.assert_not_called()
                summary.assert_not_called()
