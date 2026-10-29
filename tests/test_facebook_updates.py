import unittest
from unittest.mock import Mock, patch

import requests

from core.facebook import FacebookClient, FacebookDeliveryUnknown, FacebookError


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class FacebookCaptionTests(unittest.TestCase):
    def setUp(self):
        self.client = FacebookClient('123', 'secret-page-token')

    @patch('core.facebook.requests.get')
    def test_read_post_caption_uses_message_and_checks_page(self, get):
        get.return_value = response({'id': '123_456', 'from': {'id': '123'}, 'message': 'Original'})
        self.assertEqual(self.client.get_caption('meta:123_456'), 'Original')
        self.assertEqual(get.call_args.args[0], 'https://graph.facebook.com/v26.0/123_456')
        self.assertEqual(get.call_args.kwargs['params'], {'fields': 'id,message,from{id}'})
        self.assertEqual(get.call_args.kwargs['headers'], {'Authorization': 'Bearer secret-page-token'})
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    @patch('core.facebook.requests.get')
    def test_read_photo_caption_uses_name(self, get):
        get.return_value = response({'id': '456', 'from': {'id': '123'}, 'name': 'Photo caption'})
        self.assertEqual(self.client.get_caption('456'), 'Photo caption')
        self.assertEqual(get.call_args.kwargs['params'], {'fields': 'id,name,from{id}'})

    @patch('core.facebook.requests.get')
    def test_missing_caption_is_empty_only_for_owned_object(self, get):
        get.return_value = response({'id': '456', 'from': {'id': '123'}})
        self.assertEqual(self.client.get_caption('meta:456'), '')

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_foreign_missing_owner_and_wrong_identity_never_edited(self, get, post):
        for payload in [
            {'id': '456', 'from': {'id': '999'}, 'name': 'Foreign'},
            {'id': '456', 'name': 'No ownership'},
            {'id': '987', 'from': {'id': '123'}},
        ]:
            with self.subTest(payload=payload):
                get.return_value = response(payload)
                with self.assertRaises(FacebookError):
                    self.client.update_caption('456', 'Changed')
        post.assert_not_called()

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_invalid_refs_and_different_page_posts_never_requested(self, get, post):
        for reference in ['999_456', 'https://example/456', '../456', 'meta:meta:456', '', None]:
            with self.subTest(reference=reference), self.assertRaises(FacebookError):
                self.client.update_caption(reference, 'Changed')
        get.assert_not_called()
        post.assert_not_called()

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_update_post_preserves_id_and_uses_message(self, get, post):
        get.return_value = response({'id': '123_456', 'from': {'id': '123'}, 'message': 'Original'})
        post.return_value = response({'success': True})
        self.assertIsNone(self.client.update_caption('meta:123_456', 'Original\nUPDATE'))
        self.assertEqual(post.call_args.args[0], 'https://graph.facebook.com/v26.0/123_456')
        self.assertEqual(post.call_args.kwargs['data'], {'message': 'Original\nUPDATE'})
        self.assertEqual(post.call_args.kwargs['headers'], {'Authorization': 'Bearer secret-page-token'})
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        post.assert_called_once()

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_update_photo_preserves_id_and_uses_name(self, get, post):
        get.return_value = response({'id': '456', 'from': {'id': '123'}, 'name': 'Original'})
        post.return_value = response({'success': True})
        self.client.update_caption('meta:456', 'Changed')
        self.assertEqual(post.call_args.args[0], 'https://graph.facebook.com/v26.0/456')
        self.assertEqual(post.call_args.kwargs['data'], {'name': 'Changed'})
        post.assert_called_once()

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_manual_edit_since_initial_read_is_not_overwritten(self, get, post):
        get.return_value = response({'id': '456', 'from': {'id': '123'}, 'name': 'Editor changed it'})
        with self.assertRaisesRegex(FacebookError, 'caption changed'):
            self.client.update_caption('456', 'Old text\nUPDATE', expected_caption='Old text')
        post.assert_not_called()

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_graph_rejection_is_redacted_and_not_retried(self, get, post):
        get.return_value = response({'id': '456', 'from': {'id': '123'}})
        post.return_value = response({'error': {'message': 'Invalid secret-page-token', 'code': 190}}, 400)
        with self.assertRaises(FacebookError) as caught:
            self.client.update_caption('456', 'Changed')
        self.assertNotIsInstance(caught.exception, FacebookDeliveryUnknown)
        self.assertIn('code=190', str(caught.exception))
        self.assertNotIn('secret-page-token', str(caught.exception))
        post.assert_called_once()

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_edit_response_loss_and_unconfirmed_success_are_uncertain(self, get, post):
        get.return_value = response({'id': '456', 'from': {'id': '123'}})
        for result in [response({}), response({'success': False}), response({'success': 'true'}),
                       response({'error': {'message': 'Internal'}}, 500)]:
            with self.subTest(result=result):
                post.reset_mock()
                post.return_value = result
                with self.assertRaises(FacebookDeliveryUnknown):
                    self.client.update_caption('456', 'Changed')
                post.assert_called_once()
        post.reset_mock()
        post.side_effect = requests.Timeout('secret-page-token')
        with self.assertRaises(FacebookDeliveryUnknown) as caught:
            self.client.update_caption('456', 'Changed')
        self.assertNotIn('secret-page-token', str(caught.exception))
        post.assert_called_once()

    @patch('core.facebook.requests.post')
    @patch('core.facebook.requests.get')
    def test_read_failure_does_not_edit_or_expose_token(self, get, post):
        get.side_effect = requests.ConnectionError('Bearer secret-page-token')
        with self.assertRaises(FacebookError) as caught:
            self.client.update_caption('456', 'Changed')
        self.assertNotIn('secret-page-token', str(caught.exception))
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
