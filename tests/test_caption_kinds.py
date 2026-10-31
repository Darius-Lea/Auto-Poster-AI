import unittest
from unittest.mock import patch
from adapters.base import Record
from core.caption import DISCLAIMER, make_caption


class CaptionKindTests(unittest.TestCase):
    def test_warrant_caption_preserves_listing_context_without_ai(self):
        record = Record('Synthetic Fixture', 'Example allegation', 'listed',
                        'https://source.example/wanted/123', 'https://source.example/photo',
                        age=30, kind='warrant')
        with patch('core.caption.genai.GenerativeModel') as ai:
            text = make_caption(record, 'Fort Wayne')
        ai.assert_not_called()
        self.assertIn('as wanted', text)
        self.assertNotIn(record.source_url, text)
        self.assertIn('Listing status can change', text)
        self.assertIn(DISCLAIMER, text)
        self.assertIn('MOST WANTED FUGITIVE OF THE DAY', text)
        self.assertIn('#MostWanted', text)
        self.assertTrue(text.endswith('#FortWayne #FortWayneIndiana #AllenCounty #Indiana #MostWanted'))
        self.assertNotIn('was booked', text)
        self.assertNotIn('convicted', text)

    def test_booking_record_remains_default(self):
        record = Record('Synthetic Fixture', 'Example allegation', '01/01/2000',
                        'https://source.example/booking/123', 'https://source.example/photo')
        with patch.dict('os.environ', {'GEMINI_API_KEY': ''}):
            text = make_caption(record, 'Fort Wayne')
        self.assertEqual(record.kind, 'booking')
        self.assertIn('01/01/2000', text)
        self.assertNotIn('as wanted', text)
