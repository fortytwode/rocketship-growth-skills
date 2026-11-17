"""Standalone, anonymous Meta Ad Library capture engine for skill use.

This is a local capture utility copied from the team's Selenium poller with
repository configuration, credentials, persistence, and uploads removed.
"""

import json
import logging
import os
import re
import signal
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_WEB_COUNTRY = "ALL"
CHROMIUM_NO_KEYCHAIN_ARGS = ("--password-store=basic", "--use-mock-keychain")
SCROLL_PAUSE_SECONDS = 3
MAX_SCROLLS = 60
STALE_SCROLL_LIMIT = 8
PAGE_LOAD_TIMEOUT_SECONDS = 90
SCRIPT_TIMEOUT_SECONDS = 60
COMMAND_TIMEOUT_SECONDS = 90

def _parse_graphql_ad(raw: dict) -> dict:
    """
    Parse a single ad from the GraphQL collated_results into our
    normalized format (same shape as poller.parse_ad).
    """
    snap = raw.get("snapshot", {})

    ad_id = str(raw.get("ad_archive_id", ""))
    page_id = str(raw.get("page_id", ""))
    page_name = raw.get("page_name", "")

    # Timestamps are Unix seconds
    start_ts = raw.get("start_date")
    end_ts = raw.get("end_date")

    start_date = ""
    if start_ts:
        try:
            start_date = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            pass

    stop_date = ""
    if end_ts:
        try:
            stop_date = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            pass

    is_active = bool(raw.get("is_active", False))

    # Creative text fields
    body_raw = snap.get("body", {})
    if isinstance(body_raw, dict):
        body_text = body_raw.get("text", "")
    elif isinstance(body_raw, str):
        body_text = body_raw
    else:
        body_text = ""

    title = snap.get("title", "") or ""
    caption = snap.get("caption", "") or ""
    link_url = snap.get("link_url", "") or ""
    link_description = snap.get("link_description", "") or ""
    cta_text = snap.get("cta_text", "") or ""

    # Bodies list (to match official API format which returns a list)
    bodies = [body_text] if body_text else []
    link_titles = [title] if title else []
    link_captions = [caption] if caption else []
    link_descriptions = [link_description] if link_description else []

    # Publisher platforms
    platforms = raw.get("publisher_platform", [])
    if isinstance(platforms, str):
        platforms = [platforms]
    # Normalize to uppercase to match official API format
    platforms = [p.upper() if isinstance(p, str) else str(p) for p in platforms]

    # Days active
    days_active = 0
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
            if is_active:
                days_active = (datetime.now(timezone.utc).date() - start_dt).days
            elif stop_date:
                stop_dt = datetime.strptime(stop_date, "%Y-%m-%d").date()
                days_active = (stop_dt - start_dt).days
        except ValueError:
            pass

    # Snapshot URL (construct from ad_archive_id)
    snapshot_url = ""
    if ad_id:
        snapshot_url = f"https://www.facebook.com/ads/library/?id={ad_id}"

    # Media type detection from snapshot
    has_videos = bool(snap.get("videos"))
    has_images = bool(snap.get("images"))
    has_cards = bool(snap.get("cards"))
    display_format = snap.get("display_format", "")
    cards = snap.get("cards", [])

    if display_format == "DCO" and has_cards:
        # DCO = Dynamic Creative Optimization. Facebook tests multiple variants.
        # Classify based on what the cards actually contain, not as carousel.
        card_has_video = any(
            (c.get("video_sd_url") or c.get("video_hd_url"))
            for c in cards if isinstance(c, dict)
        )
        card_has_image = any(
            (c.get("original_image_url") or c.get("resized_image_url"))
            for c in cards if isinstance(c, dict)
        )
        if card_has_video:
            media_type = "VIDEO"
        elif card_has_image:
            media_type = "IMAGE"
        else:
            media_type = "IMAGE"  # Fallback: preview images exist
    elif has_cards and display_format != "DCO":
        # Actual carousel (user swipes through cards)
        media_type = "CAROUSEL"
    elif has_videos:
        media_type = "VIDEO"
    elif has_images:
        media_type = "IMAGE"
    else:
        media_type = "UNKNOWN"

    # Extract best video/image URL for direct playback
    # Priority: top-level videos > card videos > card images
    extracted_video_url = ""
    extracted_image_url = ""

    videos_list = snap.get("videos", [])
    images_list = snap.get("images", [])

    if videos_list:
        for vid in videos_list:
            if isinstance(vid, dict):
                url = vid.get("video_hd_url") or vid.get("video_sd_url") or ""
                if url:
                    extracted_video_url = url
                    break
    elif cards:
        # Extract from first card with video
        for card in cards:
            if isinstance(card, dict):
                url = card.get("video_hd_url") or card.get("video_sd_url") or ""
                if url:
                    extracted_video_url = url
                    break

    if images_list:
        for img in images_list:
            if isinstance(img, dict):
                url = img.get("original_image_url") or img.get("resized_image_url") or ""
                if url:
                    extracted_image_url = url
                    break

    # Extract thumbnail (preview image)
    extracted_thumbnail = ""
    if videos_list:
        for vid in videos_list:
            if isinstance(vid, dict):
                url = vid.get("video_preview_image_url", "")
                if url:
                    extracted_thumbnail = url
                    break
    if not extracted_thumbnail and images_list:
        for img in images_list:
            if isinstance(img, dict):
                url = img.get("original_image_url") or img.get("resized_image_url") or ""
                if url:
                    extracted_thumbnail = url
                    break
    if not extracted_thumbnail and cards:
        for card in cards:
            if isinstance(card, dict):
                url = (
                    card.get("video_preview_image_url")
                    or card.get("original_image_url")
                    or card.get("resized_image_url")
                    or ""
                )
                if url:
                    extracted_thumbnail = url
                    break

    return {
        "ad_id": ad_id,
        "page_id": page_id,
        "page_name": page_name,
        "ad_delivery_start_date": start_date,
        "ad_delivery_stop_date": stop_date,
        "ad_active_status": "ACTIVE" if is_active else "INACTIVE",
        "ad_creative_bodies": bodies,
        "ad_creative_link_titles": link_titles,
        "ad_creative_link_captions": link_captions,
        "ad_creative_link_descriptions": link_descriptions,
        "ad_snapshot_url": snapshot_url,
        "media_type": media_type,
        "publisher_platforms": platforms,
        "languages": [],  # Not available in GraphQL response
        "ad_creation_time": start_date,  # Best approximation
        "days_active": days_active,
        # Extracted media URLs for direct playback
        "_extracted_video_url": extracted_video_url,
        "_extracted_image_url": extracted_image_url,
        "_extracted_thumbnail": extracted_thumbnail,
        # Extra fields from GraphQL not in official API
        "_graphql_extra": {
            "display_format": display_format,
            "cta_text": cta_text,
            "link_url": link_url,
            "collation_count": raw.get("collation_count", 1),
            "images": snap.get("images", []),
            "videos": snap.get("videos", []),
            "cards": cards,
        },
    }

class BrowserStepTimeout(TimeoutError):
    """One bounded Selenium/parse step exceeded its wall-clock budget."""

def _terminate_driver_process(driver) -> None:
    """Stop chromedriver from a watchdog thread to unblock a wedged command."""
    service = getattr(driver, "service", None)
    process = getattr(service, "process", None)
    if process is None or process.poll() is not None:
        return
    logger.error("  Browser watchdog terminating an unresponsive chromedriver")
    process.terminate()
    try:
        process.wait(timeout=5)
    except Exception:
        process.kill()

@contextmanager
def _browser_step_deadline(
    label: str,
    seconds: float = COMMAND_TIMEOUT_SECONDS,
    on_timeout=None,
):
    """Hard wall-clock guard for Cloud Run's main-thread poll path.

    Selenium's HTTP timeout does not cover every Chrome/driver wedge, and the
    post-SSR step also includes local parsing. POSIX's interval timer interrupts
    either kind of stall. Non-main-thread callers cannot install signal
    handlers, so they retain Selenium's page/script/HTTP boundaries.
    """
    can_arm = (
        hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    watchdog = None
    if on_timeout is not None:
        def _watchdog_timeout():
            logger.error("  %s exceeded %gs; forcing browser shutdown", label, seconds)
            try:
                on_timeout()
            except Exception as exc:
                logger.error("  Browser watchdog cleanup failed: %s", exc)

        watchdog = threading.Timer(seconds, _watchdog_timeout)
        watchdog.daemon = True
        watchdog.start()

    if not can_arm:
        try:
            yield
        finally:
            if watchdog is not None:
                watchdog.cancel()
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _raise_timeout(_signum, _frame):
        raise BrowserStepTimeout(
            f"{label} exceeded {seconds:g}s wall-clock deadline"
        )

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        if watchdog is not None:
            watchdog.cancel()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)

def _extract_ssr_signals(page_source: str) -> dict:
    """Extract a few page-level Meta signals from SSR HTML."""
    search_count_match = re.search(
        r'"search_results_connection"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        page_source,
    )
    return {
        "ssr_connection_count": int(search_count_match.group(1)) if search_count_match else None,
        "captcha_required": bool(
            re.search(r'"xfb_ad_library_is_captcha_required"\s*:\s*true', page_source)
        ),
        "had_ssr_error": bool(
            re.search(r'"hadSSRError"\s*(?::|,)\s*1', page_source)
        ),
        "login_visible": "Log in" in page_source and "Search by keyword or advertiser" in page_source,
    }

def _collect_dom_counts(driver) -> dict:
    """Return lightweight DOM counts for page-state classification."""
    try:
        counts = driver.execute_script(
            """
            return {
                ad_preview_count: document.querySelectorAll('[data-ad-preview-id]').length,
                video_count: document.getElementsByTagName('video').length,
                img_count: document.getElementsByTagName('img').length,
                iframe_count: document.getElementsByTagName('iframe').length,
                progressbar_count: document.querySelectorAll('[role="progressbar"]').length,
                button_count: document.getElementsByTagName('button').length,
            };
            """
        )
    except BrowserStepTimeout:
        raise
    except Exception:
        counts = {}

    return {
        "ad_preview_count": int(counts.get("ad_preview_count", 0) or 0),
        "video_count": int(counts.get("video_count", 0) or 0),
        "img_count": int(counts.get("img_count", 0) or 0),
        "iframe_count": int(counts.get("iframe_count", 0) or 0),
        "progressbar_count": int(counts.get("progressbar_count", 0) or 0),
        "button_count": int(counts.get("button_count", 0) or 0),
    }

def _extract_body_excerpt(driver, limit: int = 1200) -> str:
    """Capture a short text excerpt for diagnostics."""
    try:
        excerpt = driver.execute_script(
            "return document.body ? document.body.innerText.slice(0, arguments[0]) : '';",
            limit,
        )
    except BrowserStepTimeout:
        raise
    except Exception:
        excerpt = ""
    return excerpt or ""

def _classify_page_state(
    *,
    page_source: str,
    ssr_ads_count: int,
    intercepted_count: int,
    dom_counts: dict,
) -> tuple[str, str]:
    """Classify whether Meta rendered ad content, a shell-only page, or neither."""
    ssr_signals = _extract_ssr_signals(page_source)
    dom_has_ad_content = (
        dom_counts.get("ad_preview_count", 0) > 0
        or dom_counts.get("video_count", 0) > 0
        or dom_counts.get("img_count", 0) > 0
    )
    has_any_ad_signal = ssr_ads_count > 0 or intercepted_count > 0 or dom_has_ad_content
    if has_any_ad_signal:
        return "success", "ad_content_detected"

    if ssr_signals["captcha_required"]:
        return "blocked", "captcha_required"

    if (
        ssr_signals["ssr_connection_count"] == 0
        and ssr_signals["had_ssr_error"]
        and ssr_signals["login_visible"]
        and not dom_has_ad_content
    ):
        return "blocked", "meta_shell_only_no_ads_rendered"

    if dom_counts.get("progressbar_count", 0) > 0:
        return "partial", "page_still_loading"

    if ssr_signals["login_visible"] and not dom_has_ad_content:
        return "blocked", "meta_shell_only_no_ads_rendered"

    return "empty", "no_ad_signals_detected"

def _empty_dom_counts() -> dict:
    return {
        "ad_preview_count": 0,
        "video_count": 0,
        "img_count": 0,
        "iframe_count": 0,
        "progressbar_count": 0,
        "button_count": 0,
    }

def _write_failure_artifacts(
    page_id: str,
    page_name: str,
    metadata: dict,
    page_source: str,
    driver=None,
) -> str:
    """Persist screenshot + HTML for ambiguous/blocked Selenium runs."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", page_name or page_id).strip("_") or page_id
    artifact_dir = Path(tempfile.gettempdir()) / "ad_library_capture_failures" / f"{timestamp}_{slug}_{page_id}"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    (artifact_dir / "page.html").write_text(page_source or "", encoding="utf-8")
    (artifact_dir / "meta.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    if driver:
        try:
            driver.save_screenshot(str(artifact_dir / "page.png"))
        except Exception:
            pass

    return str(artifact_dir)

def _resolve_chromedriver_path(installed_path: str) -> str:
    """Return the executable chromedriver path from webdriver-manager output.

    Recent webdriver-manager/Chrome-for-Testing downloads can return an
    adjacent metadata file such as THIRD_PARTY_NOTICES.chromedriver. Selenium
    then fails with Exec format error before Chrome launches. Resolve that
    package directory back to the real `chromedriver` binary.
    """
    path = Path(installed_path)
    candidates = []
    if path.name == "chromedriver" or path.name == "chromedriver.exe":
        candidates.append(path)
    if path.parent.exists():
        candidates.extend(path.parent.rglob("chromedriver"))
        candidates.extend(path.parent.rglob("chromedriver.exe"))

    seen = set()
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            candidate.chmod(candidate.stat().st_mode | 0o111)
        except OSError:
            pass
        if os.access(candidate, os.X_OK):
            return str(candidate)

    return installed_path

def _find_chrome_binary() -> str:
    """Return an installed Chrome/Chromium binary without consulting env."""
    candidates = [
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((path for path in candidates if path and Path(path).exists()), "")


def _configure_chrome_options(options) -> None:
    """Configure anonymous headless Chrome."""
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--autoplay-policy=no-user-gesture-required")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-features=MacAppCodeSignClone")
    for arg in CHROMIUM_NO_KEYCHAIN_ARGS:
        options.add_argument(arg)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    chrome_binary = _find_chrome_binary()
    if chrome_binary:
        options.binary_location = chrome_binary


def _configure_driver_timeouts(driver) -> None:
    """Bound every browser interaction without capping a full poll.

    A large advertiser can legitimately need many minutes of short scroll
    commands, so the Cloud Run task timeout remains the outer safety net.  The
    Selenium HTTP command timeout is the important inner boundary: if Chrome
    or Meta wedges during a single ``execute_script``/DOM call, control returns
    to ``poll_and_capture_detailed`` and the competitor is recorded as a
    partial provider capture instead of consuming the entire task lease.
    """
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
    driver.set_script_timeout(SCRIPT_TIMEOUT_SECONDS)
    client_config = getattr(driver.command_executor, "_client_config", None)
    if client_config is not None:
        client_config.timeout = COMMAND_TIMEOUT_SECONDS
        return
    # Production pins Selenium 4.17, before per-instance ClientConfig existed.
    # Its supported timeout hook is the RemoteConnection class method.
    set_timeout = getattr(driver.command_executor, "set_timeout", None)
    if not callable(set_timeout):
        raise RuntimeError("Selenium command executor has no timeout hook")
    set_timeout(COMMAND_TIMEOUT_SECONDS)
    # With keep-alive enabled, 4.17 creates urllib3's PoolManager before this
    # hook runs. Update that live manager too; changing only the class default
    # would affect future managers but leave the current Chrome connection
    # unbounded.
    connection_manager = getattr(driver.command_executor, "_conn", None)
    pool_config = getattr(connection_manager, "connection_pool_kw", None)
    if isinstance(pool_config, dict):
        clear_connections = getattr(connection_manager, "clear", None)
        if callable(clear_connections):
            clear_connections()
        pool_config["timeout"] = COMMAND_TIMEOUT_SECONDS
        # Selenium 4.17's RemoteConnection omits an explicit timeout when it
        # calls PoolManager.request(). Supply it at that exact boundary rather
        # than relying on a pool default that a pre-existing keep-alive pool
        # can retain or override.
        original_request = getattr(connection_manager, "request", None)
        if callable(original_request) and not getattr(
            connection_manager,
            "_sherlock_timeout_wrapped",
            False,
        ):
            def _bounded_request(*args, **kwargs):
                kwargs.setdefault("timeout", COMMAND_TIMEOUT_SECONDS)
                return original_request(*args, **kwargs)

            connection_manager.request = _bounded_request
            connection_manager._sherlock_timeout_wrapped = True

def _inject_xhr_interceptor(driver, *, before_navigation=False):
    """Inject JavaScript that captures GraphQL responses containing ad data."""
    source = '''
        if (window.__sherlock_interceptor_installed) return;
        window.__sherlock_interceptor_installed = true;
        window.__sherlock_ads = [];
        window.__sherlock_captured_count = 0;

        // Intercept fetch()
        const origFetch = window.fetch;
        window.fetch = async function(...args) {
            const resp = await origFetch.apply(this, args);
            try {
                const clone = resp.clone();
                const text = await clone.text();
                if (text.includes('ad_archive_id')) {
                    window.__sherlock_ads.push(text);
                    window.__sherlock_captured_count++;
                }
            } catch(e) {}
            return resp;
        };

        // Intercept XMLHttpRequest
        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url, ...rest) {
            this.addEventListener('load', function() {
                try {
                    if (this.responseText && this.responseText.includes('ad_archive_id')) {
                        window.__sherlock_ads.push(this.responseText);
                        window.__sherlock_captured_count++;
                    }
                } catch(e) {}
            });
            return origOpen.apply(this, [method, url, ...rest]);
        };
    '''
    if before_navigation:
        # Relay can retain fetch before late injection. Install in each fresh
        # anonymous document before page scripts capture their transport.
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': '(function(){' + source + '})();'})
    else:
        driver.execute_script(source)

def _click_see_more(driver) -> bool:
    """Click a visible "See more" pagination button, if one is present.

    Meta's monthly-grouped Ad Library view gates additional results behind this
    button rather than infinite scroll. Returns True if a button was clicked.
    """
    try:
        return bool(driver.execute_script("""
            // The observed result paginator is a full-width anchor button.
            // Card-copy expanders are narrow spans and must never be clicked.
            const els = document.querySelectorAll('a[role="button"]');
            for (const e of els) {
                const r = e.getBoundingClientRect();
                if (e.textContent.trim() === 'See more' && e.offsetParent !== null
                    && r.width >= innerWidth * .5) {
                    e.scrollIntoView({block: 'center'});
                    e.click();
                    return true;
                }
            }
            return false;
        """))
    except BrowserStepTimeout:
        raise
    except Exception as e:
        logger.debug(f"  See-more click failed: {str(e)[:100]}")
        return False

def _make_ad_library_url(page_id: str, sort_mode: str = "default", active_only: bool = False) -> str:
    """Build the Ad Library search URL for a page.

    sort_mode="default" produces the standard URL (Meta's default ordering).
    sort_mode="impressions" appends Meta's total-impressions sort params so
    the page loads ads in descending impressions order.
    sort_mode="recent" appends Meta's "Most recent" sort. This is the ONLY
    way to reach an advertiser's newest ads: high-impression ads are old
    because they accumulated impressions, so on a large page the impressions
    sort holds nothing recent at all. Nebula's impressions pull captured
    5,067 ads whose newest delivery date was nearly four months old.
    """
    url = (
        "https://www.facebook.com/ads/library/"
        f"?active_status={'active' if active_only else 'all'}&ad_type=all&country={DEFAULT_WEB_COUNTRY}"
        f"&view_all_page_id={page_id}&search_type=page&media_type=all"
    )
    if sort_mode == "impressions":
        url += "&sort_data[direction]=desc&sort_data[mode]=total_impressions"
    elif sort_mode == "recent":
        url += ("&sort_data[direction]=desc"
                "&sort_data[mode]=relevancy_monthly_grouped")
    return url

def _stamp_impressions_ranks(ads_in_order: list[dict], sort_mode: str) -> list[dict]:
    """Stamp 1-based impressions ranks onto ads captured in first-seen order.

    Only acts when sort_mode == "impressions": each ad gets
    `impressions_rank` (1-based first-seen capture order, which mirrors
    Meta's impressions-descending ordering) and
    `impressions_rank_captured_at` (today, YYYY-MM-DD). Default-mode polls
    stamp nothing, so previously stored ranks survive Firestore refreshes.
    """
    if sort_mode != "impressions":
        return ads_in_order
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for rank, ad in enumerate(ads_in_order, start=1):
        ad["impressions_rank"] = rank
        ad["impressions_rank_captured_at"] = today
    return ads_in_order

def _json_loads_relaxed(raw: str):
    """Best-effort JSON decode for raw or HTML-escaped GraphQL fragments."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(raw.encode("utf-8").decode("unicode_escape"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

def _walk_json(value):
    """Yield every dict/list node in a decoded JSON value."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        yield value
        for child in value:
            yield from _walk_json(child)

def _extract_balanced_json_object(text: str, start: int) -> str:
    """Return the JSON object enclosing start, or empty string if incomplete."""
    depth = 0
    in_string = False
    escape = False

    for pos in range(start, len(text)):
        char = text[pos]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]

    return ""

def _extract_ad_objects_from_text(text: str) -> list[dict]:
    """Extract object-scoped ad payloads from SSR or intercepted GraphQL text."""
    if not text or "ad_archive_id" not in text:
        return []

    decoded = _json_loads_relaxed(text)
    candidates = []
    if decoded is not None:
        candidates.extend(
            node for node in _walk_json(decoded)
            if isinstance(node, dict) and node.get("ad_archive_id")
        )

    seen_raw_ids = {str(node.get("ad_archive_id")) for node in candidates}
    for match in re.finditer(r'"ad_archive_id"\s*:\s*"?\d+"?', text):
        start = text.rfind("{", 0, match.start())
        while start != -1:
            raw_obj = _extract_balanced_json_object(text, start)
            parsed = _json_loads_relaxed(raw_obj)
            if isinstance(parsed, dict) and parsed.get("ad_archive_id"):
                raw_id = str(parsed.get("ad_archive_id"))
                if raw_id not in seen_raw_ids:
                    seen_raw_ids.add(raw_id)
                    candidates.append(parsed)
                break
            start = text.rfind("{", 0, start)

    return candidates

def _parse_ads_from_text(text: str, page_id: str, page_name: str) -> list[dict]:
    """Extract ads from a GraphQL response or SSR HTML text.

    Each ad gets: ad_id, video_hd_url (or video_sd_url), resized_image_url.
    Returned as a list of dicts in poller.parse_ad() compatible format.
    """
    ads = []
    seen_ids = set()
    for raw_ad in _extract_ad_objects_from_text(text):
        try:
            parsed = _parse_graphql_ad(raw_ad)
        except Exception as exc:
            logger.debug(f"  Skipping malformed ad object: {exc}")
            continue

        ad_id = parsed.get("ad_id")
        if not ad_id or ad_id in seen_ids:
            continue
        seen_ids.add(ad_id)
        if not parsed.get("page_id"):
            parsed["page_id"] = page_id
        if not parsed.get("page_name"):
            parsed["page_name"] = page_name
        parsed["_video_url"] = parsed.get("_extracted_video_url", "")
        parsed["_image_url"] = (
            parsed.get("_extracted_thumbnail")
            or parsed.get("_extracted_image_url", "")
        )
        ads.append(parsed)

    return ads

def _finalize_empty_poll(
    page_id: str,
    page_name: str,
    poll_meta: dict,
    page_source: str,
    driver=None,
) -> tuple[list[dict], dict]:
    """Classify and persist diagnostics while the browser is still available."""
    final_dom_counts = _collect_dom_counts(driver) if driver else _empty_dom_counts()
    if driver and not page_source:
        try:
            page_source = driver.page_source
        except Exception:
            page_source = ""

    if page_source:
        final_status, final_reason = _classify_page_state(
            page_source=page_source,
            ssr_ads_count=poll_meta["ssr_ads_count"],
            intercepted_count=poll_meta["intercepted_response_count"],
            dom_counts=final_dom_counts,
        )
        ssr_signals = _extract_ssr_signals(page_source)
        poll_meta.update({
            "status": final_status,
            "reason": final_reason,
            "ssr_connection_count": ssr_signals["ssr_connection_count"],
            "dom_ad_preview_count": final_dom_counts["ad_preview_count"],
            "video_count": final_dom_counts["video_count"],
            "img_count": final_dom_counts["img_count"],
            "iframe_count": final_dom_counts["iframe_count"],
            "progressbar_count": final_dom_counts["progressbar_count"],
            "button_count": final_dom_counts["button_count"],
            "page_source_len": len(page_source),
        })
        if not poll_meta["body_text_excerpt"] and driver:
            poll_meta["body_text_excerpt"] = _extract_body_excerpt(driver)
    else:
        poll_meta.update({
            "status": "error",
            "reason": poll_meta["reason"] or "no_page_source_available",
        })

    poll_meta["diagnostic_dir"] = _write_failure_artifacts(
        page_id,
        page_name,
        poll_meta,
        page_source,
        driver=driver,
    )
    logger.warning(
        f"  0 ads found for {page_name or page_id} "
        f"({poll_meta['status']}: {poll_meta['reason']}). "
        f"Artifacts: {poll_meta['diagnostic_dir']}"
    )
    return [], poll_meta

def poll_and_capture_detailed(
    page_id: str,
    page_name: str = "",
    sort_mode: str = "impressions",
    max_scrolls: int = 12,
    target_ads: int = 0,
    query: str = "",
    active_only: bool = False,
) -> tuple[list[dict], dict]:
    """Capture a bounded public Ad Library page in headless Chrome.

    Returns normalized ad records (including transient media URLs) and page
    diagnostics. No proxy, profile, storage, or provider credentials are used.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    if sort_mode not in ("impressions", "recent"):
        raise ValueError("sort_mode must be 'impressions' or 'recent'")
    if not 1 <= max_scrolls <= MAX_SCROLLS:
        raise ValueError(f"max_scrolls must be between 1 and {MAX_SCROLLS}")

    if target_ads < 0:
        raise ValueError("target_ads must be nonnegative")

    options = webdriver.ChromeOptions()
    _configure_chrome_options(options)
    logger.info("  No proxy (direct connection)")

    driver = None
    all_ads = {}  # ad_id -> ad dict (deduped)
    page_source = ""
    had_exception = False
    exception_reason = ""
    poll_meta = {
        "status": "error",
        "reason": "uninitialized",
        "capture_mode": "none",
        "ssr_ads_count": 0,
        "ssr_connection_count": None,
        "intercepted_response_count": 0,
        "dom_ad_preview_count": 0,
        "video_count": 0,
        "img_count": 0,
        "iframe_count": 0,
        "progressbar_count": 0,
        "button_count": 0,
        "page_source_len": 0,
        "body_text_excerpt": "",
        "navigator_webdriver": None,
        "user_agent": "",
        "scrolls_used": 0,
        "diagnostic_dir": "",
        "inactive_marking_safe": False,
        "active_only_requested": active_only,
        "active_only_verified": False,
        "stop_reason": "",
        "exception_reason": "",
        "final_url": "",
    }

    try:
        service = Service(_resolve_chromedriver_path(ChromeDriverManager().install()))
        logger.info("  Launching headless Chrome...")
        driver = webdriver.Chrome(service=service, options=options)
        _configure_driver_timeouts(driver)

        # Inject XHR interceptor BEFORE navigating
        driver.get("about:blank")
        _inject_xhr_interceptor(driver, before_navigation=True)

        url = _make_ad_library_url(page_id, sort_mode=sort_mode, active_only=active_only)
        if query:
            from urllib.parse import urlencode
            url = "https://www.facebook.com/ads/library/?" + urlencode({
                "active_status": "active" if active_only else "all", "ad_type": "all", "country": DEFAULT_WEB_COUNTRY,
                "q": query, "search_type": "keyword_exact_phrase",
                "sort_data[direction]": "desc",
                "sort_data[mode]": "total_impressions" if sort_mode == "impressions" else "relevancy_monthly_grouped",
            })
        logger.info(f"  Loading Ad Library for {page_name or page_id}")
        driver.get(url)
        time.sleep(8)

        # Record the URL Meta actually served after any redirects. Consumers
        # (e.g. roastagentv2 ad_pull) verify sort params survived on THIS
        # value, not on the reconstructed request URL — if Meta silently drops
        # the impressions-sort param, final_url is where that shows up.
        try:
            poll_meta["final_url"] = driver.current_url
        except Exception:
            poll_meta["final_url"] = ""

        # Guarded check: retain the early interceptor and its initial responses.
        _inject_xhr_interceptor(driver)
        time.sleep(3)

        # Parse SSR content (first ~30 ads embedded in HTML)
        page_source = driver.page_source
        ssr_ads = _parse_ads_from_text(page_source, page_id, page_name)
        for ad in ssr_ads:
            all_ads[ad["ad_id"]] = ad
        logger.info(f"  SSR: {len(ssr_ads)} ads")

        logger.info("  Starting post-SSR diagnostics")
        with _browser_step_deadline(
            "post-SSR diagnostics",
            on_timeout=lambda: _terminate_driver_process(driver),
        ):
            dom_counts = _collect_dom_counts(driver)
            ssr_signals = _extract_ssr_signals(page_source)
            poll_meta.update({
                "ssr_ads_count": len(ssr_ads),
                "ssr_connection_count": ssr_signals["ssr_connection_count"],
                "dom_ad_preview_count": dom_counts["ad_preview_count"],
                "video_count": dom_counts["video_count"],
                "img_count": dom_counts["img_count"],
                "iframe_count": dom_counts["iframe_count"],
                "progressbar_count": dom_counts["progressbar_count"],
                "button_count": dom_counts["button_count"],
                "page_source_len": len(page_source),
                "body_text_excerpt": _extract_body_excerpt(driver),
                "navigator_webdriver": driver.execute_script("return navigator.webdriver"),
                "user_agent": driver.execute_script("return navigator.userAgent"),
            })
        logger.info("  Post-SSR diagnostics complete")

        initial_status, initial_reason = _classify_page_state(
            page_source=page_source,
            ssr_ads_count=len(ssr_ads),
            intercepted_count=0,
            dom_counts=dom_counts,
        )
        if initial_status == "blocked":
            poll_meta.update({
                "status": initial_status,
                "reason": initial_reason,
                "capture_mode": "none",
            })
            poll_meta["diagnostic_dir"] = _write_failure_artifacts(
                page_id,
                page_name,
                poll_meta,
                page_source,
                driver=driver,
            )
            logger.warning(
                f"  Meta rendered a shell-only page for {page_name or page_id} "
                f"({initial_reason}). Artifacts: {poll_meta['diagnostic_dir']}"
            )
            return [], poll_meta

        # Scroll to load more ads
        intercepted_total = 0
        stale_scrolls = 0
        see_more_clicks = 0
        last_count = len(all_ads)

        scroll_cap = max_scrolls or MAX_SCROLLS
        for scroll_num in range(1, scroll_cap + 1):
            with _browser_step_deadline(
                f"scroll {scroll_num} browser commands",
                on_timeout=lambda: _terminate_driver_process(driver),
            ):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(SCROLL_PAUSE_SECONDS)
                poll_meta["scrolls_used"] = scroll_num

                # Read intercepted GraphQL responses
                intercepted = driver.execute_script(
                    "var ads = window.__sherlock_ads; window.__sherlock_ads = []; return ads;"
                )
            intercepted_total += len(intercepted or [])

            if scroll_num == 1:
                logger.info(
                    "  First scroll captured %d intercepted response(s); parsing",
                    len(intercepted or []),
                )
            with _browser_step_deadline(f"scroll {scroll_num} response parsing"):
                for resp_text in (intercepted or []):
                    new_ads = _parse_ads_from_text(resp_text, page_id, page_name)
                    for ad in new_ads:
                        if ad["ad_id"] not in all_ads:
                            all_ads[ad["ad_id"]] = ad
            if scroll_num == 1:
                logger.info("  First scroll response parsing complete")

            current_count = len(all_ads)
            logger.info("  Scroll %d: %d unique ad IDs", scroll_num, current_count)
            if target_ads and current_count >= target_ads:
                poll_meta["stop_reason"] = "target_ads_reached"
                break

            if current_count == last_count:
                # Meta's monthly-grouped view (sort_data[mode]=
                # relevancy_monthly_grouped, used by the recency pull) paginates
                # behind a "See more" button instead of infinite scroll. Without
                # clicking it the poller sees ad content but extracts nothing
                # past the first page, so recency silently truncates on any
                # large advertiser. Only try it once the scroll itself has gone
                # stale, so the normal infinite-scroll path is unaffected.
                #
                # Only the "recent" view has that pagination button. In the
                # default/impressions views every ad card with truncated body
                # text has its own "See more" text expander; clicking those
                # one per iteration loads no new ads but bypasses the stale
                # counter, so a large advertiser ran to MAX_SCROLLS (~6s each)
                # and blew the 1200s task timeout.
                # Do not click generic "See more" controls: ad-copy expanders
                # are indistinguishable from pagination without DOM evidence.
                # Retain an explicitly incomplete sample when scrolling stalls.
                if sort_mode == "recent" and _click_see_more(driver):
                    see_more_clicks += 1
                    poll_meta["see_more_clicks"] = see_more_clicks
                    time.sleep(SCROLL_PAUSE_SECONDS)
                    last_count = current_count
                    continue
                stale_scrolls += 1
                if stale_scrolls >= STALE_SCROLL_LIMIT:
                    logger.info(f"  No new ads after {STALE_SCROLL_LIMIT} scrolls, stopping at scroll {scroll_num}")
                    poll_meta["stop_reason"] = "stale_scroll_limit"
                    break
            else:
                stale_scrolls = 0

            last_count = current_count

            if scroll_num % 20 == 0:
                logger.info(f"  Scroll {scroll_num}: {current_count} unique ads")

        logger.info(f"  Scroll complete: {len(all_ads)} unique ads in {scroll_num} scrolls")
        if not poll_meta.get("stop_reason"):
            poll_meta["stop_reason"] = "max_scrolls"
        poll_meta["intercepted_response_count"] = intercepted_total
        if not all_ads:
            return _finalize_empty_poll(page_id, page_name, poll_meta, page_source, driver)

    except Exception as e:
        had_exception = True
        exception_reason = f"selenium_exception: {type(e).__name__}: {e}"
        logger.error(f"  Selenium session failed for {page_name or page_id}: {e}")
        poll_meta.update({
            "status": "partial" if all_ads else "error",
            "reason": exception_reason,
            "exception_reason": exception_reason,
            "capture_mode": "intercept" if all_ads else "none",
        })
        if driver:
            try:
                with _browser_step_deadline(
                    "failure diagnostics",
                    on_timeout=lambda: _terminate_driver_process(driver),
                ):
                    page_source = driver.page_source
                    poll_meta.update({
                        "page_source_len": len(page_source),
                        "body_text_excerpt": _extract_body_excerpt(driver),
                    })
            except Exception:
                pass
        if not all_ads:
            empty_ads, empty_meta = _finalize_empty_poll(
                page_id, page_name, poll_meta, page_source, driver
            )
            if had_exception:
                empty_meta.update({
                    "status": "error",
                    "reason": exception_reason,
                    "exception_reason": exception_reason,
                })
            return empty_ads, empty_meta
    finally:
        if driver:
            try:
                with _browser_step_deadline(
                    "driver shutdown",
                    on_timeout=lambda: _terminate_driver_process(driver),
                ):
                    driver.quit()
            except Exception:
                pass

    # all_ads is a py3.7+ dict keyed by ad_id, deduped on insert: iteration
    # order is first-seen capture order (SSR parse first, then scroll
    # interception). In impressions mode that order mirrors Meta's
    # impressions-descending ranking, so stamp it before anything reorders.
    ads_list = list(all_ads.values())
    from urllib.parse import urlparse, parse_qs
    actual = parse_qs(urlparse(poll_meta.get("final_url", "")).query)
    mode = "total_impressions" if sort_mode == "impressions" else "relevancy_monthly_grouped"
    sort_verified = (actual.get("sort_data[mode]") == [mode]
                     and actual.get("sort_data[direction]") == ["desc"]
                     and (actual.get("q") == [query] if query else actual.get("view_all_page_id") == [str(page_id)]))
    poll_meta["sort_verified"] = sort_verified
    # Meta can drop the filter on redirect, so record what the final page used.
    poll_meta["active_only_requested"] = active_only
    poll_meta["active_only_verified"] = bool(active_only and actual.get("active_status") == ["active"])
    if sort_verified:
        _stamp_impressions_ranks(ads_list, sort_mode)

    # Preserve CDN URLs as captured; callers can download selected media locally.
    with_video = sum(1 for a in ads_list if a.get("_extracted_video_url"))
    with_thumb = sum(1 for a in ads_list if a.get("_extracted_thumbnail") or a.get("_extracted_image_url"))
    logger.info(f"  Final: {len(ads_list)} ads, {with_video} with video URL, {with_thumb} with thumbnail/image URL")

    status = "partial" if had_exception else "success"
    reason = exception_reason if had_exception else "ad_content_detected"
    inactive_marking_safe = False
    completeness = (
        "partial_after_browser_error" if had_exception
        else "target_sample" if poll_meta.get("stop_reason") == "target_ads_reached"
        else "bounded_sample" if poll_meta.get("stop_reason") == "max_scrolls"
        else "unverified_exhaustion"
    )

    poll_meta.update({
        "status": status,
        "reason": reason,
        "capture_mode": "intercept",
        "ads_returned": len(ads_list),
        "ads_with_video": with_video,
        "ads_with_thumbnail": with_thumb,
        "completeness": completeness,
        "inactive_marking_safe": inactive_marking_safe,
    })
    return ads_list, poll_meta

