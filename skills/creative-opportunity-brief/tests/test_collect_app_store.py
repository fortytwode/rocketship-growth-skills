import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

PATH = Path(__file__).parents[1] / "scripts" / "collect_app_store.py"
spec = importlib.util.spec_from_file_location("collector", PATH)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def entry(i):
    return {"id": {"label": f"native-{i}"}, "im:rating": {"label": str(i)},
            "title": {"label": "Title"}, "content": {"label": "Full text " * 200},
            "updated": {"label": "2026-01-01"}, "im:version": {"label": "1.0"},
            "author": {"name": {"label": "private"}}}


class CollectorTests(unittest.TestCase):
    def collect(self):
        return c.collect("123", ["us"], 200, "now")

    def test_all_ratings_and_native_fields(self):
        with patch.object(c, "fetch", side_effect=[[entry(i) for i in range(1, 6)], []]):
            result = self.collect()
        self.assertEqual([r["rating"] for r in result["reviews"]], list(range(1, 6)))
        self.assertEqual(result["reviews"][0]["review_id"], "native-1")
        self.assertEqual(result["reviews"][0]["text"], "Full text " * 200)
        self.assertNotIn("author", result["reviews"][0])

    def test_fallback_stops_and_records_url(self):
        with patch.object(c, "fetch", side_effect=[[], [entry(1)]]) as fetch:
            result = self.collect()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["collection"]["countries"][0]["status"], "fallback_only")
        self.assertNotIn("/page=", result["reviews"][0]["source_url"])

    def test_partial_error_keeps_rows(self):
        err = HTTPError("url", 503, "failure", {}, None)
        with patch.object(c, "fetch", side_effect=[[entry(1)], err]):
            result = self.collect()
        self.assertEqual(len(result["reviews"]), 1)
        self.assertEqual(result["collection"]["countries"][0]["status"], "partial_error")
        self.assertEqual(result["collection"]["countries"][0]["http_status"], 503)

    def test_repeated_page_does_not_duplicate(self):
        with patch.object(c, "fetch", side_effect=[[entry(1)], [entry(1)]]):
            result = self.collect()
        self.assertEqual(len(result["reviews"]), 1)
        self.assertEqual(result["collection"]["countries"][0]["status"], "repeated_page")

    def test_malformed_body(self):
        with patch.object(c, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(b"not json")
            with self.assertRaisesRegex(ValueError, "malformed_json"):
                c.fetch("https://example.invalid")

    def test_null_or_object_text_is_excluded_without_inventing_words(self):
        bad = entry(5)
        bad['content']['label'] = None
        other = entry(4)
        other['content']['label'] = {'unexpected': 'object'}
        with patch.object(c, 'fetch', side_effect=[[bad, other, entry(1)], []]):
            result = self.collect()
        self.assertEqual(len(result['reviews']), 1)
        self.assertEqual(result['reviews'][0]['text'], 'Full text ' * 200)
        country = result['collection']['countries'][0]
        self.assertEqual(country['invalid_review_count'], 2)
        self.assertEqual(country['row_errors'][0]['reason'], 'invalid_content_label')

    def test_duplicate_json_keys_are_malformed_provider_data(self):
        raw = b'{"feed":{"entry":[],"entry":[{}]}}'
        with patch.object(c, 'urlopen') as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(raw)
            with self.assertRaisesRegex(ValueError, 'malformed_json'):
                c.fetch('https://example.invalid')

    def test_validation_and_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.json"
            for args in (["--app-id", "x", "--countries", "us", "--out", str(path)],
                         ["--app-id", "1", "--countries", "usa", "--out", str(path)],
                         ["--app-id", "1", "--countries", "us", "--limit", "501", "--out", str(path)]):
                with self.assertRaises(SystemExit):
                    c.main(args)
            path.write_text("original")
            with patch.object(c, "fetch") as fetch:
                self.assertEqual(c.main(["--app-id", "1", "--countries", "us", "--out", str(path)]), 2)
                fetch.assert_not_called()
            self.assertEqual(path.read_text(), "original")
            link = Path(tmp) / "link"
            link.symlink_to(path)
            self.assertEqual(c.main(["--app-id", "1", "--countries", "us", "--out", str(link)]), 2)
            self.assertEqual(path.read_text(), "original")

    def test_error_output_written_and_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            with patch.object(c, "fetch", side_effect=ValueError("malformed_json")):
                self.assertEqual(c.main(["--app-id", "1", "--countries", "us", "--out", str(path)]), 1)
            self.assertEqual(json.loads(path.read_text())["collection"]["countries"][0]["error_type"], "malformed_json")


if __name__ == "__main__":
    unittest.main()
