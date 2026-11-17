#!/usr/bin/env python3
"""Collect bounded, recent Apple RSS reviews with explicit coverage evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://itunes.apple.com/{country}/rss/customerreviews"
MAX_BYTES = 5 * 1024 * 1024
ISO2 = set("ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bl bm bn bo bq br bs bt bv bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg eh er es et fi fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sj sk sl sm sn so sr ss st sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug um us uy uz va vc ve vg vi vn vu wf ws ye yt za zm zw".split())


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_field')
        result[key] = value
    return result


def fetch(url):
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("response_too_large")
    try:
        data = json.loads(body, object_pairs_hook=unique_fields)
        entries = data["feed"].get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list) or any(not isinstance(x, dict) for x in entries):
            raise ValueError("invalid_entries")
        return entries
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("malformed_json") from exc


def label(entry, key):
    if key not in entry:
        return ""
    value = entry[key]
    if not isinstance(value, dict) or not isinstance(value.get('label'), str):
        raise ValueError('invalid_' + key + '_label')
    return value['label']


def collect(app_id, countries, limit, fetched_at):
    reviews, reports = [], []
    for country in countries:
        report = {"country": country, "count": 0, "page_count": 0,
                  "status": "", "used_fallback": False, "error_type": None,
                  "http_status": None, "invalid_review_count": 0, "row_errors": []}
        reports.append(report)
        seen_pages = set()
        for page in range(1, 11):
            root = BASE.format(country=country)
            url = f"{root}/page={page}/id={app_id}/sortBy=mostRecent/json"
            try:
                entries = fetch(url)
                report["page_count"] += 1
                # The feed may include an app metadata entry without a rating.
                entries = [e for e in entries if "im:rating" in e]
                if page == 1 and not entries:
                    report["used_fallback"] = True
                    url = f"{root}/id={app_id}/sortBy=mostRecent/json"
                    entries = fetch(url)
                    report["page_count"] += 1
                    entries = [e for e in entries if "im:rating" in e]
                if not entries:
                    report["status"] = "empty_feed" if not report["count"] else "end_of_feed"
                    break
                fingerprint = hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if fingerprint in seen_pages:
                    report["status"] = "repeated_page"
                    break
                seen_pages.add(fingerprint)
                for entry_index, entry in enumerate(entries, 1):
                    try:
                        rating_text = label(entry, "im:rating")
                        if rating_text not in ('1', '2', '3', '4', '5'):
                            raise ValueError('invalid_rating')
                        text = label(entry, 'content')
                        if not text.strip():
                            raise ValueError('empty_content')
                        review = {"review_id": label(entry, "id"), "date": label(entry, "updated"),
                                  "rating": int(rating_text), "title": label(entry, "title"),
                                  "text": text, "version": label(entry, "im:version"),
                                  "store": "App Store", "country": country, "app_id": app_id,
                                  "source_url": url, "source_page": page, "fetched_at": fetched_at}
                    except ValueError as exc:
                        report['invalid_review_count'] += 1
                        report['row_errors'].append({'page': page, 'entry': entry_index, 'reason': str(exc)})
                        continue
                    reviews.append(review)
                    report["count"] += 1
                    if report["count"] >= limit:
                        break
                if report["count"] >= limit:
                    report["status"] = "limit_reached"
                    break
                if report["used_fallback"]:
                    report["status"] = "fallback_only"
                    break
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                report["status"] = "partial_error" if report["count"] else "provider_error"
                report["error_type"] = "http_error" if isinstance(exc, HTTPError) else (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
                if isinstance(exc, HTTPError):
                    report["http_status"] = exc.code
                break
        else:
            report["status"] = "page_cap"
    return {"reviews": reviews, "collection": {"app_id": app_id, "store": "App Store", "sort": "mostRecent",
            "fetched_at": fetched_at, "requested_limit_per_country": limit, "countries": reports,
            "limitations": "Recent RSS results are a bounded, potentially incomplete sample, not the full review population. Empty or failed feeds do not establish absence of customer demand. Repeated pages and fallback feeds do not establish complete coverage."}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--countries", required=True, nargs="+", help="ISO 3166-1 alpha-2 country codes")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]+", args.app_id):
        parser.error("--app-id must be numeric")
    countries = [c.lower() for c in args.countries]
    if not 1 <= len(countries) <= 10 or any(c not in ISO2 for c in countries) or len(set(countries)) != len(countries):
        parser.error("--countries requires 1 to 10 distinct two-letter country codes")
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be between 1 and 500")
    # Reserve the destination before network requests, preventing overwrite or symlink following.
    try:
        output = open(args.out, "x", encoding="utf-8")
    except OSError as exc:
        print(f"Cannot create output: {exc.strerror}", file=sys.stderr)
        return 2
    with output:
        result = collect(args.app_id, countries, args.limit, datetime.now(timezone.utc).isoformat())
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    errors = [c for c in result["collection"]["countries"] if c["status"] in ("partial_error", "provider_error")]
    print(f"{args.out}: {len(result['reviews'])} reviews")
    if errors:
        print(f"Warning: {len(errors)} countries had provider errors; see collection evidence.", file=sys.stderr)
    malformed = sum(c['invalid_review_count'] for c in result['collection']['countries'])
    if malformed:
        print(f"Warning: {malformed} malformed reviews were excluded; see collection row_errors.", file=sys.stderr)
    return 1 if errors or malformed else 0


if __name__ == "__main__":
    sys.exit(main())
