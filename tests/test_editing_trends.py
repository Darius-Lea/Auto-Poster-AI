import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import editing_trends as trends


def fixture():
    return {'items': [{'id': 'abcDEF_12-3', 'snippet': {
        'title': 'A useful example', 'channelTitle': 'Example Studio',
        'publishedAt': '2026-01-02T03:04:05Z'},
        'contentDetails': {'duration': 'PT1M12S'},
        'statistics': {'viewCount': '10500', 'likeCount': '85'}}]}


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload=None, body=None, status=200, headers=None):
        self.body = json.dumps(fixture() if payload is None else payload).encode() if body is None else body
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class EditingTrendsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'AUTOPOST_WEB_DB': self.tmp.name+'/app.db',
            'YOUTUBE_DATA_API_KEY': 'synthetic-provider-key-not-real',
            'AUTOPOST_EDITING_TRENDS_ENABLED': 'true', 'AUTOPOST_TRENDS_REGION': 'US',
            'AUTOPOST_TRENDS_CACHE_SECONDS': '1800'})
        self.env.start(); self.addCleanup(self.env.stop)
        trends.init()
        self.now = int(time.time())

    def test_parse_preserves_actual_metrics_and_unknowns(self):
        item = trends.parse_videos(fixture(), self.now)[0]
        self.assertEqual(item['views'], 10500)
        self.assertEqual(item['likes'], 85)
        self.assertIsNone(item['comments'])
        self.assertTrue(item['short_video'])
        self.assertEqual(item['duration_seconds'], 72)
        self.assertEqual(item['url'], 'https://www.youtube.com/watch?v=abcDEF_12-3')

    def test_invalid_ids_dates_counts_and_duplicate_items_do_not_become_claims(self):
        good = fixture()['items'][0]
        payload = {'items': [good, good, {**good, 'id': 'javascript:bad'},
            {**good, 'id': '123456789ab', 'snippet': {**good['snippet'], 'publishedAt': 'in the future'}}]}
        self.assertEqual(len(trends.parse_videos(payload, self.now)), 1)
        for bad in (True, '-5', 'unknown', {}, float('nan')):
            self.assertIsNone(trends._count(bad))
        with self.assertRaises(ValueError):
            trends.parse_videos({'error': 'bad'}, self.now)

    def test_official_fixed_endpoint_bounded_request_no_redirects(self):
        response = FakeResponse()
        with patch.object(trends.requests, 'get', return_value=response) as get:
            data = trends.fetch_videos('US', now=self.now)
        args, kwargs = get.call_args
        self.assertEqual(args, ('https://www.googleapis.com/youtube/v3/videos',))
        self.assertEqual(kwargs['params']['chart'], 'mostPopular')
        self.assertEqual(kwargs['params']['maxResults'], 20)
        self.assertEqual(kwargs['params']['regionCode'], 'US')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], (3.05, 8))
        self.assertEqual(len(data), 1)
        self.assertTrue(response.closed)

    def test_response_failures_never_expose_token_or_upstream_body(self):
        secret = os.environ['YOUTUBE_DATA_API_KEY']
        for response in (FakeResponse(body=secret.encode(), status=302),
                         FakeResponse(body=secret.encode(), status=403),
                         FakeResponse(body=secret.encode()),
                         FakeResponse(headers={'Content-Length': str(trends.MAX_RESPONSE_BYTES+1)}),
                         FakeResponse(body=b'x'*(trends.MAX_RESPONSE_BYTES+1))):
            with patch.object(trends.requests, 'get', return_value=response):
                with self.assertRaises(ValueError) as raised:
                    trends.fetch_videos('US')
            self.assertNotIn(secret, str(raised.exception))
            self.assertTrue(response.closed)
        with patch.object(trends.requests, 'get', side_effect=trends.requests.ConnectionError(secret)):
            snapshot = trends.get_snapshot(refresh=True, now=self.now)
        self.assertEqual(snapshot['status'], 'unavailable')
        self.assertNotIn(secret, json.dumps(snapshot))
        with trends.connect() as c:
            self.assertNotIn(secret, c.execute('SELECT error FROM editing_trend_cache').fetchone()[0])

    def test_current_cached_then_stale_error_retains_attribution_and_observations(self):
        with patch.object(trends, 'fetch_videos', return_value=trends.parse_videos(fixture(), self.now)) as fetch:
            first = trends.get_snapshot(refresh=True, now=self.now)
            second = trends.get_snapshot(refresh=True, now=self.now+30)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first['status'], 'current')
        self.assertFalse(first['stale'])
        self.assertEqual(first['fetched_at'], self.now)
        self.assertEqual(first['videos'], second['videos'])
        self.assertIn('YouTube', first['source'])
        with patch.object(trends, 'fetch_videos', side_effect=ValueError('Popular-video inspiration could not update. Try again later.')):
            stale = trends.get_snapshot(refresh=True, now=self.now+1801)
        self.assertEqual(stale['status'], 'stale')
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['fetched_at'], self.now)
        self.assertEqual(stale['videos'], first['videos'])

    def test_cooldown_global_lease_and_regions_prevent_quota_loops(self):
        with patch.object(trends, 'fetch_videos', side_effect=ValueError('untrusted secret')) as fetch:
            for offset in (0, 2, 60, 299):
                trends.get_snapshot(refresh=True, now=self.now+offset)
        self.assertEqual(fetch.call_count, 1)
        with trends.connect() as c:
            c.execute('UPDATE editing_trend_cache SET lease_until=?,attempted_at=0 WHERE region=?', (self.now+1000, 'US'))
        with patch.object(trends, 'fetch_videos', side_effect=AssertionError('lease bypass')):
            trends.get_snapshot(refresh=True, now=self.now+500)
        for region in ('localhost', 'US&key=secret', 'U1', '../US', 'ZZ'):
            with self.assertRaises(ValueError): trends.get_snapshot(region, now=self.now)
        with patch.dict(os.environ, {'AUTOPOST_TRENDS_REGION':'DE'}):
            self.assertEqual(trends.get_snapshot('DE', now=self.now)['region'], 'DE')

    def test_no_config_no_network_no_invented_trend_list(self):
        with patch.dict(os.environ, {'YOUTUBE_DATA_API_KEY': ''}), patch.object(trends.requests, 'get', side_effect=AssertionError('network')):
            data = trends.get_snapshot(refresh=True, now=self.now)
        self.assertEqual(data['status'], 'unconfigured')
        self.assertEqual(data['videos'], [])
        self.assertIsNone(data['fetched_at'])
        self.assertEqual([p['id'] for p in data['presets']], ['standard','bar','minimal'])
        self.assertIn('not measured live trends', data['notice'])

    def test_empty_success_is_not_fabricated_popularity(self):
        with patch.object(trends, 'fetch_videos', return_value=[]):
            data = trends.get_snapshot(refresh=True, now=self.now)
        self.assertEqual(data['status'], 'empty')
        self.assertEqual(data['videos'], [])
        self.assertEqual(data['fetched_at'], self.now)

    def test_private_route_and_preview_no_live_call(self):
        from flask import Flask
        import app_mvp
        app = Flask(__name__)
        app.secret_key = 'synthetic-private-route-secret-long-enough'
        app.register_blueprint(trends.trends_bp)
        client = app.test_client()
        self.assertEqual(client.get('/clips/trends').status_code, 401)
        with patch.object(app_mvp, 'require_login'), patch.object(app_mvp, 'preview_mode', return_value=True), patch.object(trends, 'fetch_videos', side_effect=AssertionError('preview network')):
            response = client.get('/clips/trends')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.json['videos'], [])


if __name__ == '__main__':
    unittest.main()
