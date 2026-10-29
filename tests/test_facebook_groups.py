import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from core.facebook import FacebookClient, FacebookDeliveryUnknown, FacebookError


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


class FacebookGroupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.client = FacebookClient('123', 'secret-page-token')
        self.items = []
        for index in range(2):
            path = Path(self.folder.name) / f'{index}.jpg'
            path.write_bytes(b'fixture')
            self.items.append({'image_path': str(path), 'caption': f'Booking {index}'})

    @patch('core.facebook.requests.post')
    def test_group_uploads_unpublished_photos_then_one_feed_post(self, post):
        post.side_effect = [response({'id': '400'}), response({'id': '401'}), response({'id': '123_500'})]
        result = self.client.publish_group(self.items, 'Group of two')
        self.assertEqual(result, {'post_id': '123_500', 'photo_ids': ['400', '401']})
        calls = post.call_args_list
        self.assertEqual(len(calls), 3)
        for index, call in enumerate(calls[:2]):
            self.assertEqual(call.args[0], 'https://graph.facebook.com/v26.0/123/photos')
            self.assertEqual(call.kwargs['data'], {'caption': f'Booking {index}', 'published': 'false'})
            self.assertIn('source', call.kwargs['files'])
        self.assertEqual(calls[2].args[0], 'https://graph.facebook.com/v26.0/123/feed')
        self.assertEqual(calls[2].kwargs['data']['message'], 'Group of two')
        self.assertEqual(json.loads(calls[2].kwargs['data']['attached_media']),
                         [{'media_fbid': '400'}, {'media_fbid': '401'}])
        self.assertNotIn('files', calls[2].kwargs)
        for call in calls:
            self.assertEqual(call.kwargs['headers'], {'Authorization': 'Bearer secret-page-token'})
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertNotIn('access_token', call.kwargs['data'])

    @patch('core.facebook.requests.post')
    def test_one_and_ten_items_are_allowed(self, post):
        for count in [1, 10]:
            with self.subTest(count=count):
                post.reset_mock()
                post.side_effect = [response({'id': str(400 + i)}) for i in range(count)] + [response({'id': '123_500'})]
                result = self.client.publish_group([self.items[0]] * count, 'Group')
                self.assertEqual(len(result['photo_ids']), count)
                self.assertEqual(post.call_count, count + 1)

    @patch('core.facebook.requests.post')
    def test_invalid_inputs_make_no_requests(self, post):
        for items, caption in [([], 'Group'), ([self.items[0]] * 11, 'Group'),
                               (None, 'Group'), ([{}], 'Group'),
                               ([{'image_path': self.items[0]['image_path'], 'caption': None}], 'Group'),
                               (self.items, None)]:
            with self.subTest(items=items, caption=caption), self.assertRaises(FacebookError):
                self.client.publish_group(items, caption)
        post.assert_not_called()

    @patch('core.facebook.requests.post')
    def test_all_image_files_open_before_first_upload(self, post):
        self.items[1]['image_path'] = str(Path(self.folder.name) / 'missing.jpg')
        with self.assertRaises(FacebookError) as caught:
            self.client.publish_group(self.items, 'Group')
        self.assertEqual(caught.exception.uploaded_photo_ids, [])
        self.assertEqual(caught.exception.stage, 'upload')
        post.assert_not_called()

    @patch('core.facebook.requests.post')
    def test_rejected_photo_stops_before_feed_and_retains_known_ids(self, post):
        post.side_effect = [response({'id': '400'}),
                            response({'error': {'code': 200, 'message': 'Rejected secret-page-token'}}, 400)]
        with self.assertRaises(FacebookError) as caught:
            self.client.publish_group(self.items, 'Group')
        self.assertNotIsInstance(caught.exception, FacebookDeliveryUnknown)
        self.assertEqual(caught.exception.uploaded_photo_ids, ['400'])
        self.assertEqual(caught.exception.stage, 'upload')
        self.assertNotIn('secret-page-token', str(caught.exception))
        self.assertEqual(post.call_count, 2)
        self.assertTrue(all(call.args[0].endswith('/photos') for call in post.call_args_list))

    @patch('core.facebook.requests.post')
    def test_lost_upload_response_is_unknown_and_never_retried(self, post):
        post.side_effect = [response({'id': '400'}), requests.Timeout('secret-page-token')]
        with self.assertRaises(FacebookDeliveryUnknown) as caught:
            self.client.publish_group(self.items, 'Group')
        self.assertEqual(caught.exception.stage, 'upload')
        self.assertEqual(caught.exception.uploaded_photo_ids, ['400'])
        self.assertNotIn('secret-page-token', str(caught.exception))
        self.assertEqual(post.call_count, 2)

    @patch('core.facebook.requests.post')
    def test_feed_rejection_and_ambiguity_retain_all_uploaded_ids(self, post):
        outcomes = [response({'error': {'message': 'Denied', 'code': 200}}, 400),
                    response({'error': {'message': 'Internal'}}, 500),
                    requests.ConnectionError('Bearer secret-page-token'),
                    response({}), response({'id': '999_500'}), response({'id': '500'})]
        for outcome in outcomes:
            with self.subTest(outcome=outcome):
                post.reset_mock()
                post.side_effect = [response({'id': '400'}), response({'id': '401'}), outcome]
                with self.assertRaises(FacebookError) as caught:
                    self.client.publish_group(self.items, 'Group')
                self.assertEqual(caught.exception.stage, 'feed')
                self.assertEqual(caught.exception.uploaded_photo_ids, ['400', '401'])
                self.assertEqual(post.call_count, 3)
                self.assertNotIn('secret-page-token', str(caught.exception))

    @patch('core.facebook.requests.post')
    def test_malformed_or_duplicate_photo_id_stops_before_feed(self, post):
        for second in [response({}), response({'id': '400'}), response({'id': '123_401'})]:
            with self.subTest(second=second):
                post.reset_mock()
                post.side_effect = [response({'id': '400'}), second]
                with self.assertRaises(FacebookDeliveryUnknown) as caught:
                    self.client.publish_group(self.items, 'Group')
                self.assertEqual(caught.exception.uploaded_photo_ids, ['400'])
                self.assertEqual(caught.exception.stage, 'upload')
                self.assertEqual(post.call_count, 2)


if __name__ == '__main__':
    unittest.main()
