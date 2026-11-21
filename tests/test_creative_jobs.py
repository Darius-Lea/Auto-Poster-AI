"""Offline customer automation regressions: no live collection, AI or publication."""
from __future__ import annotations

import io
import json
import os
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import creative_jobs as jobs


RSS = b'''<rss><channel><item><guid>new-1</guid><title>New item</title><description>A verified new item.</description><link>https://source.example/items/1</link></item><item><guid>old-1</guid><title>Older item</title><description>An older listing.</description><link>https://source.example/items/old</link></item></channel></rss>'''
NEXT_RSS = b'''<rss><channel><item><guid>new-2</guid><title>Second item</title><description>The next verified item.</description><link>https://source.example/items/2</link></item><item><guid>new-1</guid><title>New item</title><description>A verified new item.</description><link>https://source.example/items/1</link></item><item><guid>old-1</guid><title>Older item</title></item></channel></rss>'''


class CreativeJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = patch.dict(os.environ, {"AUTOPOST_WEB_DB": self.temp.name + '/jobs.db',
            "AUTOPOST_PREVIEW_MODE": "false", "AUTOPOST_REQUIRE_PAYMENT": "true"}, clear=True)
        self.settings.start(); self.addCleanup(self.settings.stop)
        jobs.init()
        with jobs.connect() as c:
            c.execute('''CREATE TABLE app_posts(id INTEGER PRIMARY KEY,content TEXT,platform TEXT,due_at TEXT,status TEXT,page_id TEXT,workspace_id TEXT)''')
        self.pages = {'customer-a': ['101', '102'], 'customer-b': ['201']}
        self.patches = [
            patch('meta_accounts.list_pages', side_effect=lambda ws: [{"page_id": p, "can_publish": True} for p in self.pages.get(ws, [])]),
            patch('app_control.allowed', return_value=True),
            patch('plan_limits.validate_cadence', return_value=None),
            patch('plan_limits.check_schedule', return_value=None),
            patch('ai_providers.provider_status', return_value={"providers": []}),
            patch('ai_providers.generate_text', side_effect=AssertionError('Unconfigured AI must not run')),
        ]
        self.started = [p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)

    def save(self, workspace='customer-a', **overrides):
        values = {"name": "Local updates", "source_kind": "rss", "source_url": "https://source.example/feed.xml",
                  "page_ids": self.pages[workspace], "cadence_seconds": 86400,
                  "approval": "review", "permission_confirmed": True}
        values.update(overrides)
        return jobs.save_automation(workspace, values)

    def active(self, workspace='customer-a', **overrides):
        automation = self.save(workspace, **overrides)
        jobs.set_status(workspace, automation, True)
        return automation

    def rows(self):
        with jobs.connect() as c:
            return [dict(x) for x in c.execute('SELECT * FROM app_posts ORDER BY id')]

    def test_profile_and_automation_are_scoped_and_destinations_owned(self):
        jobs.save_profile('customer-a', {"name": "City updates", "location": "Fort Wayne", "hashtags": "#FortWayne #Local #FortWayne"})
        self.assertEqual(jobs.get_profile('customer-a')['hashtags'], '#FortWayne #Local')
        self.assertEqual(jobs.get_profile('customer-b')['location'], '')
        automation = self.save()
        self.assertEqual(len(jobs.list_automations('customer-a')), 1)
        self.assertEqual(jobs.list_automations('customer-b'), [])
        with self.assertRaisesRegex(ValueError, 'not found'):jobs.set_status('customer-b', automation, True)
        with self.assertRaisesRegex(ValueError, 'no longer connected'):self.save(page_ids=['201'])
        with self.assertRaisesRegex(ValueError, 'Confirm'):self.save(permission_confirmed=False)

    def test_first_feed_check_only_latest_and_fans_out_identical_drafts_once(self):
        automation = self.active()
        jobs.save_profile('customer-a', {"location": "Fort Wayne", "hashtags": "#FortWayne #Local"})
        with patch.object(jobs, 'fetch_source', return_value=RSS) as fetch:
            first = jobs.run_now('customer-a', automation)
            second = jobs.run_now('customer-a', automation)
        self.assertEqual(first['posts_created'], 2)
        self.assertEqual(second['posts_created'], 0)
        self.assertEqual(fetch.call_count, 2)
        rows = self.rows()
        self.assertEqual({r['page_id'] for r in rows}, {'101', '102'})
        self.assertEqual({r['status'] for r in rows}, {'setup_only'})
        self.assertEqual({r['workspace_id'] for r in rows}, {'customer-a'})
        self.assertEqual(len({r['content'] for r in rows}), 1)
        self.assertIn('Source: https://source.example/items/1', rows[0]['content'])
        self.assertIn('#FortWayne #Local', rows[0]['content'])
        self.assertNotIn('Older item', rows[0]['content'])
        self.assertEqual(jobs.history('customer-b'), [])
        self.assertEqual(jobs.history('customer-a')[0]['posts_created'], 0)

    def test_new_feed_items_get_new_posts_and_auto_requires_explicit_activation(self):
        automation = self.save(approval='auto')
        with self.assertRaisesRegex(ValueError, 'Activate this automation'):
            jobs.run_now('customer-a', automation)
        jobs.set_status('customer-a', automation, True)
        with patch.object(jobs, 'fetch_source', return_value=RSS):jobs.run_now('customer-a', automation)
        with patch.object(jobs, 'fetch_source', return_value=NEXT_RSS):
            value = jobs.run_now('customer-a', automation)
        self.assertEqual(value['posts_created'], 2)
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual({r['status'] for r in self.rows()}, {'scheduled'})

    def test_preview_payment_and_reconnected_page_are_rechecked_before_collection(self):
        automation = self.active()
        with patch.dict(os.environ, {'AUTOPOST_PREVIEW_MODE': 'true'}), patch.object(jobs, 'fetch_source') as fetch:
            self.assertIsNone(jobs.tick())
            with self.assertRaisesRegex(ValueError, 'preview'):jobs.run_now('customer-a', automation)
            fetch.assert_not_called()
        with patch('app_control.allowed', return_value=False), patch.object(jobs, 'fetch_source') as fetch:
            with self.assertRaisesRegex(ValueError, 'subscription'):jobs.run_now('customer-a', automation)
            fetch.assert_not_called()
        self.pages['customer-a'] = ['101']
        with patch.object(jobs, 'fetch_source') as fetch:
            with self.assertRaisesRegex(ValueError, 'no longer connected'):jobs.run_now('customer-a', automation)
            fetch.assert_not_called()

    def test_pause_while_generating_and_ai_failure_never_queue_posts(self):
        automation = self.active()
        def paused(*args):
            jobs.set_status('customer-a', automation, False)
            return 'fixture caption', 'fixture'
        with patch.object(jobs, 'fetch_source', return_value=RSS), patch.object(jobs, '_caption', side_effect=paused):
            value = jobs.run_now('customer-a', automation)
        self.assertEqual(value['status'], 'failed')
        self.assertEqual(self.rows(), [])
        jobs.set_status('customer-a', automation, True)
        with patch.object(jobs, 'fetch_source', return_value=RSS), patch('ai_providers.provider_status', return_value={'providers': [{'configured': True}]}), patch('ai_providers.generate_text', side_effect=RuntimeError('secret-error-from-provider')):
            value = jobs.run_now('customer-a', automation)
        self.assertEqual(value['status'], 'failed')
        self.assertNotIn('secret-error', value['error'])
        self.assertEqual(self.rows(), [])

    def test_manual_content_repeats_once_per_cadence_window_not_on_every_check(self):
        with patch.object(jobs.time, 'time', return_value=1000000000):
            automation = self.active(source_kind='manual', source_url='', source_text='Our special today is ready.', page_ids=['101'])
            first = jobs.run_now('customer-a', automation)
            second = jobs.run_now('customer-a', automation)
        with patch.object(jobs.time, 'time', return_value=1000086401):
            third = jobs.run_now('customer-a', automation)
        self.assertEqual((first['posts_created'], second['posts_created'], third['posts_created']), (1, 0, 1))

    def test_legacy_owner_token_never_authorizes_customer_destinations(self):
        with patch.dict(os.environ, {'META_PAGE_ID': '999', 'META_PAGE_ACCESS_TOKEN': 'synthetic-owner-token'}):
            with self.assertRaisesRegex(ValueError, 'no longer connected'):
                self.save(page_ids=['999'])
            identifier = jobs.save_automation('owner', {'name': 'Legacy owner setup', 'source_kind': 'manual', 'source_text': 'Owner original text', 'page_ids': ['999']})
            self.assertEqual(jobs.list_automations('owner')[0]['id'], identifier)

    def test_activation_applies_basic_cadence_limits_and_worker_does_not_double_claim(self):
        automation = self.save(page_ids=['101'])
        with patch('plan_limits.validate_cadence', side_effect=ValueError('Basic permits one post per day')):
            with self.assertRaisesRegex(ValueError, 'one post per day'):
                jobs.set_status('customer-a', automation, True)
        self.assertEqual(jobs.list_automations('customer-a')[0]['status'], 'paused')
        jobs.set_status('customer-a', automation, True)
        with jobs.connect() as c:
            c.execute('UPDATE creative_automations SET lease_until=? WHERE id=?', (int(jobs.time.time()) + 300, automation))
        with patch.object(jobs, 'fetch_source') as fetch:
            with self.assertRaisesRegex(ValueError, 'current check'):
                jobs.run_now('customer-a', automation)
            fetch.assert_not_called()

    def test_configured_ai_caption_keeps_source_attribution_and_actual_provider(self):
        automation = self.active(page_ids=['101'])
        with patch.object(jobs, 'fetch_source', return_value=RSS), patch('ai_providers.provider_status', return_value={'providers': [{'configured': True}]}), patch('ai_providers.generate_text', return_value={'text': 'A customer-tailored factual caption.', 'provider': 'openai'}):
            value = jobs.run_now('customer-a', automation)
        self.assertEqual(value['provider'], 'openai')
        self.assertIn('Source: https://source.example/items/1', self.rows()[0]['content'])
        self.assertEqual(jobs.history('customer-a')[0]['provider'], 'openai')

    def test_tick_is_bounded_and_edits_pause_automation(self):
        a = self.active(page_ids=['101'])
        b = self.active('customer-b', page_ids=['201'])
        with patch.object(jobs, 'fetch_source', return_value=RSS):
            jobs.tick()
        self.assertEqual(len(jobs.history('customer-a')) + len(jobs.history('customer-b')), 1)
        jobs.save_automation('customer-a', {"name": "Changed rules", "source_kind": "manual", "source_text": "Updated original text", "page_ids": ['101']}, a)
        self.assertEqual(jobs.list_automations('customer-a')[0]['status'], 'paused')


class FeedSecurityTests(unittest.TestCase):
    def test_json_atom_html_and_entity_handling(self):
        items = jobs.parse_source(json.dumps({'items': [{'id': '1', 'title': 'Title', 'content_html': '<p>Hello</p><script>ignore me</script>', 'url': 'javascript:alert(1)'}]}).encode(), 'jsonfeed', 'https://source.example/feed')
        self.assertEqual(items[0]['text'], 'Hello')
        self.assertEqual(items[0]['url'], 'https://source.example/feed')
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>1</id><title>Update</title><link href="https://source.example/1"/><summary>Facts.</summary></entry></feed>'
        self.assertEqual(jobs.parse_source(atom, 'atom', 'https://source.example/feed')[0]['url'], 'https://source.example/1')
        with self.assertRaises(ValueError):jobs.parse_source(b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss/>', 'rss', 'https://source.example/feed')
        with self.assertRaises(ValueError):jobs.parse_source(b'<html><h1>No feed</h1></html>', 'rss', 'https://source.example/feed')

    def test_private_addresses_dns_rebinding_and_proxy_bypass_are_rejected(self):
        for url in ['http://source.example/feed', 'https://127.0.0.1/feed', 'https://[::1]/feed', 'https://user:pass@source.example/feed', 'https://service.internal/feed', 'https://source.example:8443/feed']:
            with self.subTest(url=url), self.assertRaises(ValueError):jobs._url_shape(url)
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]), self.assertRaisesRegex(ValueError, 'Private'):
            jobs._public_addresses('rebinding.example')
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://proxy.invalid:8080'}, clear=True), patch.object(socket, 'create_connection') as direct:
            with self.assertRaisesRegex(ValueError, 'proxy-only'):jobs.fetch_source('https://source.example/feed')
            direct.assert_not_called()

    def test_redirects_are_revalidated_and_body_size_bounded(self):
        class Response(io.BytesIO):
            status = 302
            def getheader(self, name, default=None):return 'https://127.0.0.1/feed' if name == 'Location' else default
        class Connection:
            def __init__(self, *args):pass
            def request(self, *args, **kwargs):pass
            def getresponse(self):return Response()
            def close(self):pass
        with patch.dict(os.environ, {}, clear=True), patch.object(jobs, '_public_addresses', return_value=['8.8.8.8']), patch.object(jobs, '_PinnedHTTPS', Connection), self.assertRaisesRegex(ValueError, 'Private'):
            jobs.fetch_source('https://source.example/feed')
        class Large(Response):
            status = 200
            def getheader(self, name, default=None):return str(jobs.MAX_BYTES + 1) if name == 'Content-Length' else default
        Connection.getresponse = lambda self: Large()
        with patch.dict(os.environ, {}, clear=True), patch.object(jobs, '_public_addresses', return_value=['8.8.8.8']), patch.object(jobs, '_PinnedHTTPS', Connection), self.assertRaisesRegex(ValueError, 'too large'):
            jobs.fetch_source('https://source.example/feed')


if __name__ == '__main__':unittest.main()
