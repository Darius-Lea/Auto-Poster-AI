import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import requests

from adapters.allen_sheriff import AllenSheriffAdapter
from adapters.indiana_most_wanted import IndianaMostWantedAdapter


class AdapterFailureTests(unittest.TestCase):
    def test_allen_source_load_failure_is_reported(self):
        adapter = AllenSheriffAdapter()
        with patch.object(adapter, "_load_list_html", side_effect=RuntimeError("browser failed")):
            with self.assertRaisesRegex(RuntimeError, "Could not load Allen Sheriff roster.*browser failed"):
                adapter.fetch("https://source.example/roster")

    def test_indiana_source_load_failure_is_reported(self):
        with patch("adapters.indiana_most_wanted.load_page_html", side_effect=RuntimeError("network failed")):
            with self.assertRaisesRegex(RuntimeError, "Could not load Indiana most-wanted source.*network failed"):
                IndianaMostWantedAdapter().fetch("https://source.example/wanted")

    def test_empty_allen_roster_is_valid(self):
        adapter = AllenSheriffAdapter()
        with patch.object(adapter, "_load_list_html", return_value=["<table><tbody></tbody></table>"]):
            self.assertEqual(adapter.fetch("https://source.example/roster"), [])
        self.assertEqual(adapter.errors, [])

    def test_allen_detail_failure_preserves_other_records_and_reports_error(self):
        adapter = AllenSheriffAdapter()
        rows = [
            {"name": name, "booking_date": date.today().strftime("%m/%d/%Y"),
             "image_url": "https://source.example/photo.jpg",
             "detail_url": f"https://source.example/{name}"}
            for name in ("Failed", "Successful")
        ]
        response = MagicMock()
        response.text = "<p>Age: 25</p><p>Statute Description: Example charge</p>"
        with patch.object(adapter, "_load_list_html", return_value=["html"]), \
                patch("adapters.allen_sheriff.parse_rows", return_value=rows), \
                patch("adapters.allen_sheriff.requests.get", side_effect=[requests.Timeout("timed out"), response]), \
                patch("adapters.allen_sheriff.time.sleep"):
            records = adapter.fetch("https://source.example/roster")
        self.assertEqual([r.name for r in records], ["Successful"])
        self.assertEqual(len(adapter.errors), 1)
        self.assertIn("Failed", adapter.errors[0])
        self.assertIn("timed out", adapter.errors[0])
        with patch.object(adapter, "_load_list_html", return_value=["<table></table>"]):
            adapter.fetch("https://source.example/roster")
        self.assertEqual(adapter.errors, [])

    def _browser_page(self, playwright):
        browser = playwright.return_value.__enter__.return_value.chromium.launch.return_value
        page = browser.new_context.return_value.new_page.return_value
        page.wait_for_selector.side_effect = RuntimeError("no image rows")
        page.get_by_role.return_value.click.side_effect = RuntimeError("no Search button")
        page.locator.return_value.first.is_visible.return_value = False
        page.locator.return_value.all_inner_texts.return_value = []
        page.content.return_value = "<table><tbody></tbody></table>"
        return browser, page

    @patch("adapters.allen_sheriff.sync_playwright")
    def test_missing_roster_on_block_page_fails(self, playwright):
        browser, page = self._browser_page(playwright)
        page.locator.return_value.count.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "Roster table did not appear"):
            AllenSheriffAdapter()._load_list_html("https://source.example/roster")
        browser.close.assert_called_once()

    @patch("adapters.allen_sheriff.sync_playwright")
    def test_browser_can_return_valid_empty_roster(self, playwright):
        browser, page = self._browser_page(playwright)
        page.locator.return_value.count.return_value = 1
        self.assertEqual(AllenSheriffAdapter()._load_list_html("https://source.example/roster"),
                         ["<table><tbody></tbody></table>"])
        browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
