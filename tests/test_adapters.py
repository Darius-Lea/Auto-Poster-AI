import unittest
from datetime import date, timedelta
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

    def _fetch_rows(self, rows, *, max_age=0, limit=10, seen=None):
        adapter = AllenSheriffAdapter()
        if seen is not None:
            adapter.seen_check = seen
        response = MagicMock()
        response.text = "<p>Age: 25</p><p>Statute Description: Example charge</p>"
        with patch.object(adapter, "_load_list_html", return_value=["html"]), \
                patch("adapters.allen_sheriff.parse_rows", return_value=rows), \
                patch("adapters.allen_sheriff.MAX_AGE_DAYS", max_age), \
                patch("adapters.allen_sheriff.MAX_PER_RUN", limit), \
                patch("adapters.allen_sheriff.requests.get", return_value=response) as get, \
                patch("adapters.allen_sheriff.time.sleep"):
            records = adapter.fetch("https://source.example/roster")
        return records, get

    def _row(self, name, booked):
        return {"name": name, "booking_date": booked,
                "image_url": "https://source.example/photo.jpg",
                "detail_url": f"https://source.example/{name}"}

    def test_full_roster_includes_decades_old_bookings_oldest_first(self):
        rows = [self._row("New", date.today().strftime("%m/%d/%Y")),
                self._row("Old", "01/02/1999")]
        records, _ = self._fetch_rows(rows)
        self.assertEqual([r.name for r in records], ["Old", "New"])

    def test_positive_age_window_remains_optional(self):
        rows = [self._row("Old", "01/02/1999"),
                self._row("Recent", (date.today() - timedelta(days=1)).strftime("%m/%d/%Y"))]
        records, _ = self._fetch_rows(rows, max_age=3)
        self.assertEqual([r.name for r in records], ["Recent"])

    def test_seen_records_excluded_before_detail_limit(self):
        rows = [self._row("Seen", "01/01/1999"), self._row("Old", "01/02/1999"),
                self._row("New", date.today().strftime("%m/%d/%Y"))]
        records, get = self._fetch_rows(rows, limit=1, seen=lambda name, *_: name == "Seen")
        self.assertEqual([r.name for r in records], ["Old"])
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], "https://source.example/Old")

    def test_same_date_order_is_deterministic(self):
        rows = [self._row("Z", "01/01/2000"), self._row("A", "01/01/2000")]
        records, _ = self._fetch_rows(rows, limit=1)
        self.assertEqual([r.name for r in records], ["A"])

    def test_runtime_limit_skips_failed_details_without_starving_next_booking(self):
        adapter = AllenSheriffAdapter()
        adapter.max_per_run = 1
        response = MagicMock()
        response.text = "<p>Age: 25</p><p>Statute Description: Example</p>"
        rows = [self._row("Failed", "01/01/1999"), self._row("Available", "01/02/1999"),
                self._row("Later", "01/03/1999")]
        with patch.object(adapter, "_load_list_html", return_value=["html"]), \
                patch("adapters.allen_sheriff.parse_rows", return_value=rows), \
                patch("adapters.allen_sheriff.MAX_AGE_DAYS", 0), \
                patch("adapters.allen_sheriff.requests.get", side_effect=[requests.Timeout(), response]) as get, \
                patch("adapters.allen_sheriff.time.sleep"):
            records = adapter.fetch("https://source.example/roster")
        self.assertEqual([record.name for record in records], ["Available"])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(len(adapter.errors), 1)

    def _pagination_page(self, playwright, bodies, visible):
        browser = playwright.return_value.__enter__.return_value.chromium.launch.return_value
        page = browser.new_context.return_value.new_page.return_value
        tbody = MagicMock()
        tbody.first.inner_html.side_effect = bodies
        next_locator = MagicMock()
        button = next_locator.first
        button.is_visible.side_effect = visible
        button.get_attribute.side_effect = lambda name: "" if name == "class" else None
        other = MagicMock()
        other.first.locator.return_value.evaluate_all.return_value = []
        page.locator.side_effect = lambda selector: (
            tbody if selector == "table tbody" else
            next_locator if "paginate_button.next" in selector else other
        )
        page.content.side_effect = [f"<table><tbody>{body}</tbody></table>" for body in bodies]
        return browser, page, button

    @patch("adapters.allen_sheriff.sync_playwright")
    def test_pagination_collects_distinct_pages_and_waits_for_change(self, playwright):
        browser, page, button = self._pagination_page(playwright, ["A", "B"], [True, False])
        pages = AllenSheriffAdapter()._load_list_html("https://source.example/roster")
        self.assertEqual(len(pages), 2)
        self.assertIn("B", pages[1])
        button.click.assert_called_once()
        self.assertEqual(page.wait_for_function.call_args.kwargs["arg"], "A")
        browser.close.assert_called_once()

    @patch("adapters.allen_sheriff.sync_playwright")
    def test_pagination_repeated_page_is_not_silently_complete(self, playwright):
        browser, _, _ = self._pagination_page(playwright, ["A", "B", "A"], [True, True])
        with self.assertRaisesRegex(RuntimeError, "repeated an earlier page"):
            AllenSheriffAdapter()._load_list_html("https://source.example/roster")
        browser.close.assert_called_once()

    @patch("adapters.allen_sheriff.sync_playwright")
    def test_pagination_limit_is_reported_when_next_is_still_active(self, playwright):
        browser, _, _ = self._pagination_page(playwright, ["A", "B"], [True, True])
        with patch("adapters.allen_sheriff.MAX_PAGES", 1), \
                self.assertRaisesRegex(RuntimeError, "exceeds MAX_PAGES=1"):
            AllenSheriffAdapter()._load_list_html("https://source.example/roster")
        browser.close.assert_called_once()

    @patch("adapters.allen_sheriff.sync_playwright")
    def test_pagination_timeout_is_reported_as_incomplete(self, playwright):
        browser, page, _ = self._pagination_page(playwright, ["A"], [True])
        page.wait_for_function.side_effect = RuntimeError("timed out")
        with self.assertRaisesRegex(RuntimeError, "roster is incomplete"):
            AllenSheriffAdapter()._load_list_html("https://source.example/roster")
        browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
