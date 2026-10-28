import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from core import delivery


class SourceRoutingTests(unittest.TestCase):
    def setUp(self):
        self.city = {
            'city': 'Fort Wayne', 'workspace_key': 'fort-wayne', 'logo': 'logo.png',
            'sources': [
                {'adapter': 'allen_sheriff', 'url': 'https://source.invalid/roster'},
                {'adapter': 'indiana_most_wanted', 'url': 'https://source.invalid/wanted'},
            ],
        }

    def test_legacy_source_kinds_and_explicit_override(self):
        self.assertEqual(main.source_kind(self.city['sources'][0]), 'booking')
        self.assertEqual(main.source_kind(self.city['sources'][1]), 'warrant')
        self.assertEqual(main.source_kind({'adapter': 'custom', 'kind': 'warrant'}), 'warrant')

    def test_process_fetches_only_requested_source_kind(self):
        for selected, expected in [('booking', ['allen_sheriff']),
                                   ('warrant', ['indiana_most_wanted']),
                                   ('all', ['allen_sheriff', 'indiana_most_wanted'])]:
            with self.subTest(selected=selected), sqlite3.connect(':memory:') as conn:
                args = SimpleNamespace(source_type=selected, dump_html=False,
                                       dry_run=True, limit=1, attempted=0)
                adapter = Mock(errors=[])
                adapter.fetch.return_value = []
                with patch.object(main, 'get_adapter', return_value=adapter) as get_adapter, \
                        patch.object(main.publisher, 'destination', return_value='queue:fort-wayne'):
                    main.process_city(self.city, conn, args, {}, {})
                self.assertEqual([call.args[0] for call in get_adapter.call_args_list], expected)

    def test_process_without_new_argument_keeps_all_legacy_sources(self):
        args = SimpleNamespace(dump_html=False, dry_run=True, limit=1, attempted=0)
        adapter = Mock(errors=[])
        adapter.fetch.return_value = []
        with sqlite3.connect(':memory:') as conn, \
                patch.object(main, 'get_adapter', return_value=adapter) as get_adapter, \
                patch.object(main.publisher, 'destination', return_value='queue:fort-wayne'):
            main.process_city(self.city, conn, args, {}, {})
        self.assertEqual(get_adapter.call_count, 2)

    def test_invalid_explicit_kind_is_rejected_but_valid_kinds_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cities.json'
            for kind in ['booking', 'warrant', 'unknown', None]:
                self.city['sources'][0]['kind'] = kind
                path.write_text(json.dumps([self.city]))
                if kind in ('booking', 'warrant'):
                    self.assertEqual(main.load_config(str(path))[0]['sources'][0]['kind'], kind)
                else:
                    with self.assertRaisesRegex(ValueError, 'Source kind must be booking or warrant'):
                        main.load_config(str(path))

    def test_missing_requested_source_fails_before_scrape_or_database(self):
        self.city['sources'] = self.city['sources'][:1]
        with patch.dict(os.environ, {'PUBLISH_BACKEND': 'queue'}, clear=True), \
                patch.object(main, 'load_dotenv'), \
                patch.object(main, 'load_config', return_value=[self.city]), \
                patch.object(main, 'get_adapter') as get_adapter, \
                patch.object(main, 'init_db') as init_db, \
                patch('sys.argv', ['main.py', '--source-type', 'warrant']), \
                self.assertLogs('main', level='ERROR') as logs:
            self.assertEqual(main.main(), 1)
        self.assertIn('No warrant source configured', '\n'.join(logs.output))
        get_adapter.assert_not_called()
        init_db.assert_not_called()

    def test_city_filter_is_applied_before_source_availability_check(self):
        other = {**self.city, 'city': 'Other', 'workspace_key': 'other',
                 'sources': self.city['sources'][1:]}
        self.city['sources'] = self.city['sources'][:1]
        with patch.dict(os.environ, {'PUBLISH_BACKEND': 'queue'}, clear=True), \
                patch.object(main, 'load_dotenv'), \
                patch.object(main, 'load_config', return_value=[self.city, other]), \
                patch.object(main, 'process_city') as process_city, \
                patch('sys.argv', ['main.py', '--city', 'Fort Wayne', '--source-type', 'warrant']), \
                self.assertLogs('main', level='ERROR'):
            self.assertEqual(main.main(), 1)
        process_city.assert_not_called()

    def test_read_only_facebook_check_bypasses_source_configuration(self):
        client = Mock()
        client.check_page.return_value = {'id': '123', 'name': 'Fixture Page'}
        with patch.object(main, 'load_dotenv'), \
                patch.object(main.FacebookClient, 'from_env', return_value=client), \
                patch.object(main, 'load_config') as load_config, \
                patch.object(main, 'get_adapter') as get_adapter, \
                patch('sys.argv', ['main.py', '--source-type', 'warrant', '--check-facebook']):
            self.assertEqual(main.main(), 0)
        load_config.assert_not_called()
        get_adapter.assert_not_called()

    def test_delivery_inspection_bypasses_source_configuration(self):
        conn = sqlite3.connect(':memory:')
        delivery.initialize(conn)
        with patch.object(main, 'load_dotenv'), \
                patch.object(main, 'init_db', return_value=conn), \
                patch.object(main, 'load_config') as load_config, \
                patch('builtins.print') as output, \
                patch('sys.argv', ['main.py', '--source-type', 'warrant', '--deliveries']):
            self.assertEqual(main.main(), 0)
        load_config.assert_not_called()
        output.assert_called_once_with('[]')


if __name__ == '__main__':
    unittest.main()
