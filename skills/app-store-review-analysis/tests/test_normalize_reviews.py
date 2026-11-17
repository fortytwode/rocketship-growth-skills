import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'normalize_reviews.py'
spec = importlib.util.spec_from_file_location('normalize_reviews', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NormalizeTests(unittest.TestCase):
    def test_sherlock_and_evidence(self):
        body = '  Bad experience. ' + 'é' * 320 + '\n'
        rows = [{'date': '2026-01-01', 'rating': 1, 'title': '  Oh! ', 'text': body, 'store': 'App Store', 'country': 'US', 'app_id': 'a', 'id': 'x'},
                {'date': '2026-01-02', 'rating': '5', 'text': body, 'store': 'App Store', 'country': 'US', 'app_id': 'a', 'id': 'x'},
                {'content': 'distinct', 'score': None}]
        doc, coverage = module.normalize(rows, 'in.json', 'abc', collection={'fetched': 'today'})
        self.assertEqual(doc['collection'], {'fetched': 'today'})
        self.assertEqual(doc['reviews'][0]['text'], body)
        self.assertEqual(doc['reviews'][0]['title'], '  Oh! ')
        self.assertEqual(doc['reviews'][0]['rating'], 1)
        self.assertIsNone(doc['reviews'][2]['rating'])
        self.assertEqual(coverage['counts']['rating']['unknown'], 1)
        self.assertEqual(coverage['exact_full_text_duplicate_groups'][0]['local_ids'], ['R000001', 'R000002'])
        self.assertEqual(coverage['repeated_native_identity_groups'][0]['review_id'], 'x')
        self.assertNotIn('author', doc['reviews'][0])

    def test_exclusions_conflicts_and_types(self):
        rows = [{'text': 'a', 'content': 'b'}, {'text': 'valid', 'rating': True}, {'text': 'valid', 'rating': []}, {'text': ' '}, 3, {'text': 'ok', 'rating': '1'}]
        doc, coverage = module.normalize(rows, 'x', 'x')
        self.assertEqual(coverage['excluded_count'], 5)
        self.assertIn('conflicting aliases', coverage['exclusions'][0]['reason'])
        self.assertEqual(doc['reviews'][0]['source_row'], 6)
        self.assertEqual(doc['reviews'][0]['local_id'], 'R000001')

    def test_csv_and_output_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'in.csv'
            source.write_text('text,title,rating\n"Café, ""yes""\nsecond line","  T  ",1\n', encoding='utf-8')
            out = root / 'out'
            module.main([str(source), '--out', str(out), '--country', 'US'])
            doc = json.loads((out / 'reviews.json').read_text())
            self.assertEqual(doc['reviews'][0]['text'], 'Café, "yes"\nsecond line')
            self.assertEqual(doc['reviews'][0]['title'], '  T  ')
            before = (out / 'reviews.json').read_bytes()
            with self.assertRaises(FileExistsError):
                module.main([str(source), '--out', str(out)])
            self.assertEqual((out / 'reviews.json').read_bytes(), before)
            bad = root / 'bad.json'
            bad.write_text('{"reviews": {}}')
            with self.assertRaises(ValueError):
                module.main([str(bad), '--out', str(root / 'new')])
            self.assertFalse((root / 'new').exists())

    def test_duplicate_headers_and_json_keys_fail_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename, body in [('headers.csv', 'text,text,rating\noriginal,replacement,5\n'),
                                   ('keys.json', '[{"text":"original","text":"replacement"}]')]:
                source = root / filename
                source.write_text(body, encoding='utf-8')
                output = root / (filename + '-out')
                with self.assertRaises(ValueError):
                    module.main([str(source), '--out', str(output)])
                self.assertFalse(output.exists())
                self.assertEqual(source.read_text(), body)

    def test_collector_provenance_survives_normalization(self):
        row = {'text': 'Exact body', 'version': '1.2', 'fetched_at': '2026-11-17T12:00:00Z',
               'source_page': 2, 'source_url': 'https://itunes.apple.com/us/rss/customerreviews'}
        doc, coverage = module.normalize([row], 'export.json', 'sha')
        self.assertEqual(coverage['retained_count'], 1)
        for key, value in row.items():
            self.assertEqual(doc['reviews'][0][key], value)


if __name__ == '__main__':
    unittest.main()
