"""Offline fixtures for real Canva OAuth/design/export protocol and tenant isolation."""
import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import requests
import editor_connectors as editors


class Response:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self.payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode()

    def json(self):
        return self.payload


class EditorConnectorTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, {
            'AUTOPOST_WEB_DB': self.folder.name+'/editors.db',
            'AUTOPOST_CONNECTOR_SECRET': 'synthetic-editor-encryption-secret-at-least-32',
            'CANVA_CLIENT_ID': 'synthetic-client',
            'CANVA_CLIENT_SECRET': 'synthetic-confidential-secret',
            'CANVA_REDIRECT_URI': 'https://app.example.test/creative/canva/callback',
        }, clear=True)
        env.start(); self.addCleanup(env.stop)
        editors.init()
        self.token = {'access_token':'synthetic-access-token', 'refresh_token':'synthetic-refresh-token', 'expires_in':14400}
        self.design = {'design': {'id':'DA-Test-123', 'urls':{'edit_url':'https://www.canva.com/design/DA-Test-123/edit','view_url':'https://www.canva.com/design/DA-Test-123/view'}}}

    def connect(self, workspace='customer-a'):
        flow = editors.canva_authorize(workspace)
        with patch.object(editors.requests, 'request', return_value=Response(self.token)):
            editors.canva_complete(workspace,'fixture-code',flow['verifier'],flow['state'],flow['state'],flow['expires_at'])
        return flow

    def create_design(self, workspace='customer-a'):
        self.connect(workspace)
        with patch.object(editors.requests,'request',return_value=Response(self.design)):
            return editors.create_canva_design(workspace,'Weekend promotion')

    def test_pkce_and_confidential_token_exchange_encrypts_customer_tokens(self):
        flow = editors.canva_authorize('customer-a')
        query = parse_qs(urlparse(flow['url']).query)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(flow['verifier'].encode()).digest()).decode().rstrip('=')
        self.assertEqual(query['code_challenge'],[challenge])
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertEqual(urlparse(flow['url']).netloc,'www.canva.com')
        self.assertNotIn(os.environ['CANVA_CLIENT_SECRET'],flow['url'])
        with patch.object(editors.requests,'request',return_value=Response(self.token)) as call:
            result=editors.canva_complete('customer-a','fixture-code',flow['verifier'],flow['state'],flow['state'])
        self.assertTrue(result['connected'])
        self.assertEqual(call.call_args.args,('POST',editors.API+'/oauth/token'))
        sent=call.call_args.kwargs
        self.assertEqual(sent['data']['code_verifier'],flow['verifier'])
        self.assertTrue(sent['headers']['Authorization'].startswith('Basic '))
        self.assertFalse(sent['allow_redirects'])
        with editors._db() as c:
            blob=c.execute('SELECT credentials FROM editor_connections').fetchone()[0]
        self.assertNotIn(self.token['access_token'].encode(),blob)
        self.assertNotIn(self.token['refresh_token'].encode(),blob)
        self.assertTrue(editors.status('customer-a')['canva']['connected'])
        self.assertFalse(editors.status('customer-b')['canva']['connected'])
        self.assertNotIn('synthetic-',json.dumps(editors.status('customer-a')))

    def test_state_expiry_workspace_mismatch_and_replay_do_not_exchange(self):
        flow=editors.canva_authorize('customer-a',now=1000)
        with patch.object(editors.requests,'request') as call:
            for workspace,state,now in [('customer-a','wrong-state',1001),('customer-b',flow['state'],1001),('customer-a',flow['state'],1600)]:
                with self.subTest(workspace=workspace,state=state,now=now):
                    with self.assertRaises(editors.ConnectorError):
                        editors.canva_complete(workspace,'code',flow['verifier'],flow['state'],state,now=now)
            call.assert_not_called()
        flow=self.connect()
        with patch.object(editors.requests,'request') as call:
            with self.assertRaisesRegex(editors.ConnectorError,'already used'):
                editors.canva_complete('customer-a','code',flow['verifier'],flow['state'],flow['state'])
            call.assert_not_called()

    def test_expired_access_token_refreshes_and_rotates_only_same_workspace(self):
        self.connect()
        refreshed={'access_token':'fresh-access','refresh_token':'fresh-refresh','expires_in':14400}
        with editors._db() as c:
            old=json.loads(editors._cipher().decrypt(c.execute('SELECT credentials FROM editor_connections').fetchone()[0]))
        with patch.object(editors.requests,'request',return_value=Response(refreshed)) as call:
            access=editors._token('customer-a',now=old['expires_at']+1)
        self.assertEqual(access,'fresh-access')
        self.assertEqual(call.call_args.kwargs['data'],{'grant_type':'refresh_token','refresh_token':self.token['refresh_token']})
        self.assertFalse(editors.status('customer-b')['canva']['connected'])

    def test_create_design_uses_documented_custom_size_and_owned_design_record(self):
        self.connect()
        with patch.object(editors.requests,'request',return_value=Response(self.design)) as call:
            design=editors.create_canva_design('customer-a','Weekend promotion',1080,1350)
        self.assertEqual(call.call_args.kwargs['json'],{'type':'type_and_asset','title':'Weekend promotion','design_type':{'type':'custom','width':1080,'height':1350}})
        self.assertEqual(design['edit_url'],self.design['design']['urls']['edit_url'])
        self.assertEqual(len(editors.list_canva_designs('customer-a')),1)
        self.assertEqual(editors.list_canva_designs('customer-b'),[])
        with patch.object(editors.requests,'request') as call:
            for dimensions in [(True,1080),(39,100),(8000,8000)]:
                with self.assertRaises(editors.ConnectorError):
                    editors.create_canva_design('customer-a','Bad size',*dimensions)
            call.assert_not_called()

    def test_export_checks_design_and_job_ownership_and_safe_download_urls(self):
        self.create_design()
        job={'job':{'id':'export-123','status':'in_progress'}}
        with patch.object(editors.requests,'request',return_value=Response(job)) as call:
            result=editors.create_canva_export('customer-a','DA-Test-123','png')
        self.assertEqual(result['status'],'in_progress')
        self.assertEqual(call.call_args.kwargs['json'],{'design_id':'DA-Test-123','format':{'type':'png'}})
        with patch.object(editors.requests,'request') as call:
            with self.assertRaises(editors.ConnectorError):editors.create_canva_export('customer-b','DA-Test-123')
            with self.assertRaises(editors.ConnectorError):editors.get_canva_export('customer-b','export-123')
            call.assert_not_called()
        ready={'job':{'id':'export-123','status':'success','urls':['https://export-download.canva.com/fixture.png','http://localhost/internal','https://attacker.test/a.png']}}
        with patch.object(editors.requests,'request',return_value=Response(ready)):
            result=editors.get_canva_export('customer-a','export-123')
        self.assertEqual(result['urls'],['https://export-download.canva.com/fixture.png'])

    def test_ambiguous_mutation_is_not_retried(self):
        self.connect()
        with patch.object(editors.requests,'request',side_effect=requests.Timeout('synthetic-access-token')) as call:
            with self.assertRaises(editors.ConnectorError) as error:
                editors.create_canva_design('customer-a','Review before retry')
        self.assertEqual(call.call_count,1)
        self.assertTrue(error.exception.outcome_unknown)
        self.assertNotIn('synthetic-access-token',str(error.exception))

    def test_disconnect_removes_local_credentials_even_if_revoke_is_unreachable(self):
        self.connect()
        with patch.object(editors.requests,'request',side_effect=requests.Timeout()):
            result=editors.canva_disconnect('customer-a')
        self.assertFalse(result['connected']);self.assertFalse(result['revoked'])
        self.assertFalse(editors.status('customer-a')['canva']['connected'])

    def test_handoffs_never_claim_canva_muse_or_capcut_login_without_support(self):
        with patch.dict(os.environ,{'CANVA_CLIENT_SECRET':''}):
            state=editors.status('customer-a')
        self.assertFalse(state['canva']['configured'])
        self.assertFalse(state['muse']['connected'])
        self.assertFalse(state['capcut']['connected'])
        self.assertEqual(state['capcut']['capability'],'export_handoff')
        self.assertNotIn('key',json.dumps(state).lower())


if __name__=='__main__':unittest.main()
