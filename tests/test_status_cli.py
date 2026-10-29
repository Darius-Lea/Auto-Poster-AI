import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import main
from core import catchup_control


class StatusCliTests(unittest.TestCase):
    def test_catchup_blocks_new_individual_posts_but_keeps_status_checks_available(self):
        city = {'city': 'Fort Wayne', 'workspace_key': 'fort-wayne', 'logo': 'unused',
                'sources': [{'adapter': 'allen_sheriff', 'url': 'https://source.example/roster'}]}
        client = Mock()
        client.page_id = '123'
        client.check_page.return_value = {'id': '123', 'name': 'Fixture Page'}
        for status in ('active', 'paused', 'completed'):
            for check_status in (False, True):
                with self.subTest(status=status, check_status=check_status), \
                        tempfile.TemporaryDirectory() as folder:
                    database = str(Path(folder) / 'state.db')
                    with sqlite3.connect(database) as conn:
                        catchup_control.initialize(conn)
                        catchup_control.begin(conn, 'meta:123:fort-wayne', 'fixture-session')
                        if status != 'active':
                            catchup_control.finish_batch(conn, 'meta:123:fort-wayne',
                                                         'fixture-session', {
                                'paused': status == 'paused', 'complete': status == 'completed'})
                    environment = {'PUBLISH_BACKEND': 'meta', 'META_PAGE_ID': '123',
                                   'META_PAGE_ACCESS_TOKEN': 'fixture', 'AUTOPOST_DB': database}
                    args = ['main.py'] + (['--check-status'] if check_status else [])
                    with patch.dict(os.environ, environment, clear=True), \
                            patch.object(main, 'load_dotenv'), patch('sys.argv', args), \
                            patch.object(main, 'load_config', return_value=[city]), \
                            patch.object(main.FacebookClient, 'from_env', return_value=client), \
                            patch.object(main.roster_monitor, 'reconcile_city', return_value={
                                'updated': 0, 'planned': 0, 'errors': []}) as reconcile, \
                            patch.object(main, 'process_city') as publish_pipeline, \
                            patch.object(main.notify, 'send_telegram_summary'):
                        blocked = status != 'completed' and not check_status
                        self.assertEqual(main.main(), 1 if blocked else 0)
                    if check_status:
                        reconcile.assert_called_once()
                        publish_pipeline.assert_not_called()
                    elif blocked:
                        reconcile.assert_not_called()
                        publish_pipeline.assert_not_called()
                    else:
                        reconcile.assert_not_called()
                        publish_pipeline.assert_called_once()

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
