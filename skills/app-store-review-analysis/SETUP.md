# Setup and use

Use this folder with your existing assistant, or place it in your agent's supported skills directory as `app-store-review-analysis`. Ask it to read `SKILL.md`. Give it an App Store app ID, the country stores to inspect and verified product facts. Python 3.10+ runs the bundled review downloader and evidence preparation using only the standard library. Analysis is done by your own assistant. There is no RocketShip login, cloud sync, hosted model, provider key or paid scraping service in this package.

## Download recent App Store reviews

The collector adapts the public RSS mechanism used in RocketShip's Sherlock workflow, without its accounts, cache or connected services:

```bash
python3 scripts/collect_app_store.py --app-id 123456789 --countries us gb --limit 200 --out /tmp/apple-reviews.json
python3 scripts/normalize_reviews.py /tmp/apple-reviews.json --out /tmp/apple-review-evidence
```

Replace the illustrative app ID. Choose up to ten country stores explicitly. The example requests 200 reviews per store; `--limit` supports up to 500. Apple's feed returns recent reviews only, and the requested limit is not a guaranteed count. Apple may cap, repeat, block or return an empty feed. The script records actual coverage, fallback use and partial errors. It retains full text and native IDs when the feed supplies them; the source URL is the retrieved feed, not an invented review permalink. No author names are exported. Existing output files are preserved.

This is a recent-feed sample, not a complete historical archive or a representative customer survey. Review dates differ from collection time. An empty/failed feed does not establish that the app has no reviews. Provider errors and malformed review rows produce a nonzero exit and leave the collected file available for inspection; read its `collection` metadata and `row_errors` before continuing. Do not retry an unchanged blocked request in a loop.

## Use existing review exports instead

Supply a CSV or JSON export plus your approved product facts. Google Play collection is not bundled; use an export you already have. Supported JSON is either a review list or an object with a `reviews` list, including Sherlock's cached review response shape. If collection metadata is available, put it under `collection`. Sherlock exports may lack native IDs and full collection history; missing data remains unknown.

Supported fields are `text` (aliases `content`, `review`), `title`, `rating` (`score`), `date` (`at`), `review_id` (`reviewId`, `id`), `app_id` (`appId`), `store`, `country`, `source_url`, `version`, `source_page` and `fetched_at`. Conflicting populated aliases exclude the affected row with a reason. Duplicate CSV headers or JSON keys reject the file before output is created. Other fields are omitted. Preserve your original export. Dates/languages already missing from an export cannot be reconstructed by the normalizer.

From this skill folder, prepare a new output directory:

```bash
python3 scripts/normalize_reviews.py examples/reviews.json --out /tmp/review-analysis-pilot
```

The directory must not already exist. It contains full-text `reviews.json` and `coverage.json`, with source SHA256, row references, rejected-row reasons, rating/country/store counts and duplicate warnings. Identical text and repeated native IDs are flagged, never silently removed. Supply `--store`, `--app-id` or `--country` only as known defaults for missing fields in your own export. Those defaults are recorded; they are not discoveries.

## Ask your assistant for the analysis

Give your assistant the prepared ledger, coverage report, product facts and any ads you want compared:

> Use app-store-review-analysis for App Store app [app ID]. Download its recent reviews from [country stores], or use the review exports supplied for [target app]. Our verified product facts are [facts]. The attached ads are the creative sample to compare. Find supported customer situations, keep motivations and friction separate, and propose ranked opportunities with exact quote references, proof to show and test questions. Explain coverage limits and unresolved product assumptions.

Keep real exports and output briefs in your own project, outside the public skill repository. If you use an online assistant, its normal file handling and usage charges apply. New examples bundled here are fictional. Editor-specific installation flows and the quality of a particular assistant's research are not certified by the local script tests.
