#!/usr/bin/env python3
"""Bounded local Meta Ad Library capture CLI."""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from capture_engine import MAX_SCROLLS, poll_and_capture_detailed


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture public Meta Ad Library records to a local JSON receipt."
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--page-id", help="Meta advertiser page ID")
    scope.add_argument("--query", help="Exact product/domain phrase across advertiser pages")
    parser.add_argument("--brand", default="", help="Display name for the advertiser")
    parser.add_argument(
        "--sort", choices=("impressions", "recent"), default="impressions",
        help="Ad Library ordering to capture (default: impressions)",
    )
    parser.add_argument(
        "--active-only", action="store_true",
        help="Capture only ads that are running now (default: active and inactive)",
    )
    parser.add_argument("--target-ads", type=int, default=0, help="Stop after at least this many captured ad IDs (0 uses scroll bound)")
    parser.add_argument("--out", required=True, type=Path, help="Receipt JSON path")
    parser.add_argument(
        "--max-scrolls", type=int, default=12,
        help=f"Scroll cap (1-{MAX_SCROLLS}; default: 12)",
    )
    return parser


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 1 <= args.max_scrolls <= MAX_SCROLLS:
        parser.error(f"--max-scrolls must be between 1 and {MAX_SCROLLS}")

    if args.target_ads < 0:
        parser.error("--target-ads must be nonnegative")

    try:
        ads, poll_meta = poll_and_capture_detailed(
            args.page_id or "",
            page_name=args.brand,
            sort_mode=args.sort,
            max_scrolls=args.max_scrolls,
            target_ads=args.target_ads,
            query=args.query or "",
            active_only=args.active_only,
        )
    except Exception as exc:
        poll_meta = {
            "status": "error",
            "reason": f"capture_exception: {type(exc).__name__}: {exc}",
            "capture_mode": "none",
            "ads_returned": 0,
            "active_only_requested": args.active_only,
            "active_only_verified": False,
        }
        ads = []

    receipt = {
        "page_id": args.page_id or "",
        "query": args.query or "",
        "brand_name": args.brand,
        "sort_mode": args.sort,
        "active_only": args.active_only,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "poll_meta": poll_meta,
        "ads": ads,
    }
    try:
        _atomic_write_json(args.out, receipt)
    except OSError as exc:
        print(f"Could not write receipt {args.out}: {exc}", file=sys.stderr)
        return 1

    print(
        f"Capture receipt: {args.out} | status={poll_meta.get('status')} | "
        f"ads={len(ads)}"
    )
    # Partial, blocked, and empty captures are useful evidence. Only an actual
    # capture error exits nonzero; all outcomes have a durable receipt.
    return 1 if poll_meta.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
