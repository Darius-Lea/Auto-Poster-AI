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
        # Only the two Page hashtags, at the very end. No extra hashtags.
        self.assertNotIn('#MostWanted', text)
        self.assertNotIn('#FortWayneIndiana', text)
        self.assertTrue(text.endswith('#FortWayne #AllenCounty'))
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

    def test_booking_fallback_hook_is_name_first_cities_last_two_tags_only(self):
        record = Record('Allen Harvey',
                        'unlawful carrying of a handgun with a prior conviction '
                        'within 15 years, theft of a firearm, parole revocation',
                        'Nov 11, 2026', 'https://source.example/booking/123',
                        'https://source.example/photo', age=20)
        with patch.dict('os.environ', {'GEMINI_API_KEY': ''}):
            text = make_caption(record, 'Fort Wayne')
        # Hook first: name, age, booking date, charges.
        self.assertTrue(text.startswith(
            'Allen Harvey, 20, booked Nov 11, 2026 - unlawful carrying of a handgun'))
        self.assertIn(DISCLAIMER, text)
        # Cities last, after the follow line; never up top.
        self.assertLess(text.index('Follow this Page'),
                        text.index('Allen County booking update for Fort Wayne'))
        self.assertLess(text.index('Allen County booking update for Fort Wayne'),
                        text.index('#FortWayne #AllenCounty'))
        # Only the two Page hashtags, at the very end. No extra hashtags.
        self.assertTrue(text.endswith('#FortWayne #AllenCounty'))
        self.assertNotIn('#FortWayneIndiana', text)
        self.assertNotIn('#Indiana', text)
