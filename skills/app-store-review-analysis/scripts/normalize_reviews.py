#!/usr/bin/env python3
"""Normalize an existing review export without fetching or deduplicating evidence."""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ALIASES = {
    'text': ('text', 'content', 'review'), 'rating': ('rating', 'score'),
    'date': ('date', 'at'), 'review_id': ('review_id', 'reviewId', 'id'),
    'app_id': ('app_id', 'appId'), 'title': ('title',), 'store': ('store',),
    'country': ('country',), 'source_url': ('source_url',),
    'version': ('version',), 'source_page': ('source_page',), 'fetched_at': ('fetched_at',),
}


def blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON field: {key}')
        result[key] = value
    return result


def normalize(rows, source_filename, source_sha256, defaults=None, collection=None):
    """Return deterministic review and coverage documents for a parsed export."""
    if not isinstance(rows, list):
        raise ValueError('reviews must be a list')
    if collection is not None and not isinstance(collection, dict):
        raise ValueError('collection must be an object')
    defaults = dict(defaults or {})
    retained, exclusions = [], []
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ValueError('row must be an object')
            if None in row:
                raise ValueError('row has extra CSV fields')
            values = {}
            for field, names in ALIASES.items():
                populated = [(name, row[name]) for name in names if name in row and not blank(row[name])]
                if any(not isinstance(v, (str, int, float, bool)) for _, v in populated):
                    raise ValueError(f'{field} must be scalar')
                if len(populated) > 1 and any(v != populated[0][1] for _, v in populated[1:]):
                    raise ValueError(f'conflicting aliases for {field}: ' + ', '.join(n for n, _ in populated))
                values[field] = populated[0][1] if populated else None
            if values['text'] is None or not isinstance(values['text'], str):
                raise ValueError('text must be a nonblank string')
            for field in ('title', 'date', 'review_id', 'app_id', 'store', 'country', 'source_url', 'version', 'fetched_at'):
                if values[field] is not None and not isinstance(values[field], str):
                    raise ValueError(f'{field} must be a string')
            page = values['source_page']
            if page is not None and (isinstance(page, bool) or not isinstance(page, (int, str))):
                raise ValueError('source_page must be an integer or string')
            rating = values['rating']
            if rating is not None:
                if isinstance(rating, bool) or not (isinstance(rating, int) or (isinstance(rating, str) and rating in ('1', '2', '3', '4', '5'))):
                    raise ValueError('rating must be an integer from 1 to 5')
                rating = int(rating)
                if not 1 <= rating <= 5:
                    raise ValueError('rating must be an integer from 1 to 5')
            values['rating'] = rating
            for field in ('store', 'app_id', 'country'):
                if values[field] is None:
                    values[field] = defaults.get(field)
            retained.append({'local_id': f'R{len(retained)+1:06d}', 'source_row': index, **values})
        except ValueError as exc:
            exclusions.append({'source_row': index, 'reason': str(exc)})
    counts = {}
    for field in ('store', 'country', 'rating'):
        c = Counter(str(r[field]) if r[field] is not None else 'unknown' for r in retained)
        counts[field] = dict(sorted(c.items()))
    texts, identities = defaultdict(list), defaultdict(list)
    for row in retained:
        texts[row['text']].append(row['local_id'])
        key = tuple(row[x] for x in ('store', 'app_id', 'review_id'))
        if all(not blank(x) for x in key):
            identities[key].append(row['local_id'])
    duplicates = [{'local_ids': ids} for ids in texts.values() if len(ids) > 1]
    repeated = [{'store': k[0], 'app_id': k[1], 'review_id': k[2], 'local_ids': ids} for k, ids in identities.items() if len(ids) > 1]
    doc = {'schema_version': 1, 'source': {'filename': source_filename, 'sha256': source_sha256}, 'defaults': defaults, 'collection': collection, 'reviews': retained}
    coverage = {'input_count': len(rows), 'retained_count': len(retained), 'excluded_count': len(exclusions), 'exclusions': exclusions, 'counts': counts, 'exact_full_text_duplicate_groups': duplicates, 'repeated_native_identity_groups': repeated}
    return doc, coverage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--store', choices=('App Store', 'Google Play'))
    parser.add_argument('--app-id')
    parser.add_argument('--country')
    args = parser.parse_args(argv)
    raw = args.input.read_bytes()
    if args.input.suffix.lower() == '.csv':
        import io
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
        headers = reader.fieldnames
        if not headers or any(not h for h in headers) or len(headers) != len(set(headers)):
            raise ValueError('CSV headers must be nonempty and unique')
        rows = list(reader)
        collection = None
    elif args.input.suffix.lower() == '.json':
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_fields)
        if isinstance(data, list):
            rows, collection = data, None
        elif isinstance(data, dict) and isinstance(data.get('reviews'), list):
            rows, collection = data['reviews'], data.get('collection')
        else:
            raise ValueError('JSON must be a list or an object containing a reviews list')
    else:
        raise ValueError('input must be CSV or JSON')
    defaults = {k: v for k, v in {'store': args.store, 'app_id': args.app_id, 'country': args.country}.items() if v is not None}
    doc, coverage = normalize(rows, args.input.name, hashlib.sha256(raw).hexdigest(), defaults, collection)
    args.out.mkdir(parents=False, exist_ok=False)
    for name, value in [('reviews.json', doc), ('coverage.json', coverage)]:
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"{args.out} input={coverage['input_count']} retained={coverage['retained_count']} excluded={coverage['excluded_count']}")


if __name__ == '__main__':
    main()
