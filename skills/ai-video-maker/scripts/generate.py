#!/usr/bin/env python3
"""Make one image, video clip or narration with your own API keys, inside a spend ceiling.

    python generate.py init --ceiling 40
    python generate.py image --prompt-file p.txt --ref cast.jpg --ref room.jpg --out storyboard/01.jpg
    python generate.py video --route grok-video --prompt-file p.txt --start-image storyboard/01.jpg \
        --seconds 5 --label scene-01 --out clips/scene-01.mp4
    python generate.py narration --voice-id VOICE --text-file narration.txt --out assets/narration.mp3
    python generate.py status

Standard library only. Keys are read from the environment (KIE_API_KEY,
REPLICATE_API_TOKEN, ELEVENLABS_API_KEY) and are never printed or saved.

Every job is written to a ledger file before it is sent. A job whose estimated
cost would take the project past the ceiling in that ledger is refused. Add
--dry-run to any job to print the request without sending it.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as _dt
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MODELS = HERE.parent / "data" / "models.json"
DEFAULT_LEDGER = "ledger.json"

REPLICATE_BASE = "https://api.replicate.com/v1"
KIE_BASE = "https://api.kie.ai/api/v1"
ELEVENLABS_BASE = "https://api.elevenlabs.io"

KEY_VARS = {"replicate": "REPLICATE_API_TOKEN", "kie": "KIE_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}

# One entry per route this script can call. `price_id` names the row in
# data/models.json used for the estimate. A route without one needs --rate.
ROUTES = {
    "nano-banana-pro": dict(
        kind="image", provider="replicate", model="google/nano-banana-pro",
        price_id="nano-banana-pro-replicate", refs_param="image_input", max_refs=14,
    ),
    "grok-video": dict(
        kind="video", provider="replicate", model="xai/grok-imagine-video",
        price_id="grok-video", start_param="image", min_seconds=1, max_seconds=15,
    ),
    "kling-3.0": dict(
        kind="video", provider="replicate", model="kwaivgi/kling-v3-video",
        price_id=None, start_param="start_image", end_param="end_image",
        fixed_seconds=(5, 10), defaults={"mode": "standard"},
    ),
    "seedance-2.5-kie": dict(
        kind="video", provider="kie", model="bytedance/seedance-2-5",
        price_id="seedance-2.5-kie", start_param="first_frame_url", end_param="last_frame_url",
        refs_param="reference_image_urls", voices_param="reference_audio_urls",
        max_refs=30, max_voices=10, min_seconds=4, max_seconds=30,
        defaults={"generate_audio": True, "nsfw_checker": True}, seedance=True,
    ),
    "seedance-2.5-replicate": dict(
        kind="video", provider="replicate", model="bytedance/seedance-2.5",
        price_id="seedance-2.5-replicate", start_param="image", end_param="last_frame_image",
        refs_param="reference_images", voices_param="reference_audios",
        max_refs=30, max_voices=10, min_seconds=4, max_seconds=30,
        defaults={"generate_audio": True}, seedance=True,
    ),
    "elevenlabs": dict(
        kind="narration", provider="elevenlabs", model="eleven_v4",
        price_id="elevenlabs-tts", max_characters=10_000,
    ),
}

# A job in one of these states counts toward the ceiling.
COUNTED = {"submitting", "submitted", "unknown", "done"}
INLINE_WARN_BYTES = 5 * 1024 * 1024


class GenerateError(Exception):
    """A problem the person can fix. `code` is the process exit code."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------- http

def http_request(method, url, headers=None, body=None, params=None, timeout=60):
    """Send one request and return (status, bytes). The only network call in this file."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    request.add_header("User-Agent", "ai-video-maker/1.0")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _scrub(text) -> str:
    """Remove any key value from text before it is printed or saved."""
    text = str(text)
    for name in KEY_VARS.values():
        value = os.environ.get(name) or ""
        if len(value) >= 6:
            text = text.replace(value, "[key removed]")
    return text


def _key(provider: str) -> str:
    name = KEY_VARS[provider]
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise GenerateError(f"{name} is not set. See SETUP.md. Nothing was sent.")
    return value


def _auth_headers(provider: str, key: str) -> dict:
    if provider == "elevenlabs":
        return {"xi-api-key": key, "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _json_body(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


# -------------------------------------------------------------------- pricing

def load_table(path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def estimate(route_name: str, table: dict, *, seconds=None, resolution="720p", audio=False,
             characters=None, rate=None) -> dict:
    """Return {'cost', 'rate', 'unit', 'source'} for one job. Never guesses a price."""
    route = ROUTES[route_name]
    row = next((m for m in table.get("models", []) if m.get("id") == route.get("price_id")), None)
    source = "--rate"
    unit = {"image": "per image", "video": "per second", "narration": "per 1,000 characters"}[route["kind"]]
    if rate is None:
        if row is None:
            raise GenerateError(
                f"No price for route {route_name!r} in the price table. Read the provider's pricing page "
                f"and pass --rate ({unit})."
            )
        source = f"{row['id']} in the price table dated {table.get('as_of')}"
        if not row.get("verified"):
            source += " (not re-checked)"
        if route["kind"] == "image":
            rate = row.get("price_per_image")
        elif route["kind"] == "narration":
            rate = row.get("price_per_1k_characters")
        else:
            prices = row.get("price_per_second", {})
            if audio and not row.get("audio_included", False):
                prices = row.get("audio_price_per_second", {})
            rate = prices.get(resolution)
        if rate is None:
            raise GenerateError(
                f"The price table has no {unit} price for {route_name!r}"
                + (f" at {resolution}" if route["kind"] == "video" else "")
                + ". Pass --rate after reading the provider's pricing page."
            )
    rate = float(rate)
    if rate < 0:
        raise GenerateError("--rate cannot be negative")
    if route["kind"] == "image":
        cost = rate
    elif route["kind"] == "narration":
        cost = rate * int(characters) / 1000.0
    else:
        cost = rate * float(seconds)
    return {"cost": round(cost, 4), "rate": rate, "unit": unit, "source": source}


# --------------------------------------------------------------------- ledger

@contextlib.contextmanager
def _locked(ledger_path: Path, wait_seconds: float = 15.0):
    """Hold a lock file so two jobs cannot both slip under the ceiling."""
    lock = ledger_path.with_name(ledger_path.name + ".lock")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(handle)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise GenerateError(
                    f"The ledger is locked by another job ({lock}). If nothing else is running, delete that file."
                )
            time.sleep(0.2)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(lock)


def load_ledger(path: Path) -> dict:
    if not path.is_file():
        raise GenerateError(
            f"No ledger at {path}. Set the spend ceiling first: generate.py init --ceiling AMOUNT"
        )
    with open(path, encoding="utf-8") as handle:
        ledger = json.load(handle)
    if not isinstance(ledger, dict) or "ceiling" not in ledger or not isinstance(ledger.get("jobs"), list):
        raise GenerateError(f"{path} is not a ledger written by this script")
    return ledger


def save_ledger(path: Path, ledger: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle, indent=1)
        handle.write("\n")
    os.replace(temporary, path)


def committed(ledger: dict) -> float:
    return round(sum(float(j.get("estimated_cost") or 0) for j in ledger["jobs"] if j.get("status") in COUNTED), 4)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_job(path: Path, job_id: str, **changes) -> dict:
    with _locked(path):
        ledger = load_ledger(path)
        job = next(j for j in ledger["jobs"] if j["id"] == job_id)
        job.update(changes)
        job["updated"] = _now()
        save_ledger(path, ledger)
        return job


def reserve(path: Path, job: dict, reason: str | None) -> dict:
    """Add the job to the ledger if it fits under the ceiling. Raises if it does not."""
    with _locked(path):
        ledger = load_ledger(path)
        label = job.get("label")
        earlier = [j for j in ledger["jobs"] if label and j.get("label") == label and j.get("status") in COUNTED]
        if earlier and not (reason or "").strip():
            raise GenerateError(
                f"{label!r} already has {len(earlier)} take(s) in the ledger. Say what was wrong with the "
                "last one with --reason before making another. Nothing was sent."
            )
        spent = committed(ledger)
        ceiling = float(ledger["ceiling"])
        if round(spent + job["estimated_cost"], 4) > ceiling + 1e-9:
            raise GenerateError(
                f"Refused: this job is estimated at ${job['estimated_cost']:.2f}. The ledger already holds "
                f"${spent:.2f} against a ceiling of ${ceiling:.2f}, so it would end at "
                f"${spent + job['estimated_cost']:.2f}. Nothing was sent. A person can raise the ceiling with: "
                "generate.py ceiling AMOUNT --reason WHY",
                code=3,
            )
        job = dict(job, id="job-%04d" % (len(ledger["jobs"]) + 1), status="submitting",
                   created=_now(), updated=_now(), reason=(reason or "").strip() or None)
        ledger["jobs"].append(job)
        save_ledger(path, ledger)
        return job


# ------------------------------------------------------------------- requests

def _is_url(value: str) -> bool:
    return value.lower().startswith("https://")


def _media(value: str, provider: str, what: str) -> str:
    """Turn a local file or https link into something the provider accepts."""
    if _is_url(value):
        return value
    if value.lower().startswith("http://"):
        raise GenerateError(f"{what}: use an https link, not http")
    path = Path(value).expanduser()
    if not path.is_file():
        raise GenerateError(f"{what}: file not found: {value}")
    if provider == "kie":
        raise GenerateError(
            f"{what}: Kie downloads inputs itself, so it needs an https link it can open, not a local file. "
            "Upload the file somewhere it can reach and pass the link."
        )
    data = path.read_bytes()
    if len(data) > INLINE_WARN_BYTES:
        print(f"WARNING: {path.name} is {len(data) / 1_000_000:.1f} MB and is sent inline. "
              "If the provider rejects it, shrink it or pass an https link.", file=sys.stderr)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _read_text(args) -> str:
    inline = getattr(args, "prompt", None) or getattr(args, "text", None)
    file_name = getattr(args, "prompt_file", None) or getattr(args, "text_file", None)
    if inline and file_name:
        raise GenerateError("Give the words inline or in a file, not both")
    if file_name:
        path = Path(file_name).expanduser()
        if not path.is_file():
            raise GenerateError(f"File not found: {file_name}")
        inline = path.read_text(encoding="utf-8")
    inline = (inline or "").strip()
    if not inline:
        raise GenerateError("The prompt or text is empty")
    return inline


def _check_seconds(route_name: str, seconds) -> int:
    route = ROUTES[route_name]
    if seconds is None or int(seconds) != seconds:
        raise GenerateError("--seconds must be a whole number")
    seconds = int(seconds)
    fixed = route.get("fixed_seconds")
    if fixed and seconds not in fixed:
        raise GenerateError(f"{route_name} makes clips of {' or '.join(str(f) for f in fixed)} seconds")
    if not fixed and not route["min_seconds"] <= seconds <= route["max_seconds"]:
        raise GenerateError(
            f"{route_name} makes clips of {route['min_seconds']} to {route['max_seconds']} seconds"
        )
    return seconds


def build_request(args) -> dict:
    """Return the request for one job, without a key in it."""
    route_name = args.route
    route = ROUTES[route_name]
    provider = route["provider"]
    if route["kind"] != args.kind:
        raise GenerateError(f"{route_name} is a {route['kind']} route, not {args.kind}")
    text = _read_text(args)

    if route["kind"] == "narration":
        if len(text) > route["max_characters"]:
            raise GenerateError(
                f"The narration is {len(text):,} characters. The limit is {route['max_characters']:,} per request."
            )
        return {
            "provider": provider, "model": args.model_id or route["model"], "characters": len(text),
            "method": "POST", "url": f"{ELEVENLABS_BASE}/v1/text-to-speech/{args.voice_id}",
            "params": {"output_format": args.output_format},
            "body": {"text": text, "model_id": args.model_id or route["model"]},
        }

    arguments: dict = dict(route.get("defaults") or {})
    arguments["prompt"] = text
    refs = [_media(v, provider, "--ref") for v in (getattr(args, "ref", None) or [])]
    if len(refs) > route.get("max_refs", 0):
        raise GenerateError(f"{route_name} takes at most {route.get('max_refs', 0)} reference image(s)")

    if route["kind"] == "image":
        if refs:
            arguments[route["refs_param"]] = refs
        if args.aspect_ratio:
            arguments["aspect_ratio"] = args.aspect_ratio
        if args.resolution:
            arguments["resolution"] = args.resolution
    else:
        seconds = _check_seconds(route_name, args.seconds)
        arguments["duration"] = seconds
        voices = [_media(v, provider, "--voice") for v in (args.voice or [])]
        if len(voices) > route.get("max_voices", 0):
            raise GenerateError(f"{route_name} takes at most {route.get('max_voices', 0)} voice sample(s)")
        frames = bool(args.start_image or args.end_image)
        if frames and (refs or voices):
            raise GenerateError(
                f"{route_name} cannot combine a start or end frame with reference images or voice samples. "
                "Put the opening picture first in the reference list and name it in the prompt."
            )
        if args.end_image and not route.get("end_param"):
            raise GenerateError(f"{route_name} does not take an end frame")
        if args.start_image:
            arguments[route["start_param"]] = _media(args.start_image, provider, "--start-image")
        if args.end_image:
            arguments[route["end_param"]] = _media(args.end_image, provider, "--end-image")
        if refs:
            arguments[route["refs_param"]] = refs
        if voices:
            arguments[route["voices_param"]] = voices
        if route.get("seedance"):
            arguments["resolution"] = args.resolution or "720p"
            if args.no_audio:
                arguments["generate_audio"] = False
            # A start or end frame fixes the shape of the picture on this model.
            arguments["aspect_ratio"] = "adaptive" if frames else (args.aspect_ratio or "9:16")
        else:
            if args.aspect_ratio:
                arguments["aspect_ratio"] = args.aspect_ratio
            if args.resolution:
                arguments["resolution"] = args.resolution

    if provider == "replicate":
        return {"provider": provider, "model": route["model"], "method": "POST",
                "url": f"{REPLICATE_BASE}/models/{route['model']}/predictions",
                "params": None, "body": {"input": arguments}}
    return {"provider": provider, "model": route["model"], "method": "POST",
            "url": f"{KIE_BASE}/jobs/createTask", "params": None,
            "body": {"model": route["model"], "input": arguments}}


def _abbreviate(value):
    """Copy a request body with inline file data shortened for printing."""
    if isinstance(value, dict):
        return {k: _abbreviate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_abbreviate(v) for v in value]
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        head, data = value.split(";base64,", 1)
        return f"{head};base64,<{len(data)} characters of file data>"
    return value


def describe_request(request: dict) -> dict:
    """The request as it will be sent, with the key shown as its variable name."""
    variable = KEY_VARS[request["provider"]]
    return {
        "method": request["method"], "url": request["url"], "params": request["params"],
        "headers": _auth_headers(request["provider"], f"${variable}"),
        "body": _abbreviate(request["body"]),
    }


# ------------------------------------------------------------ submit and poll

def submit(request: dict, key: str):
    """Send the job. Returns ('accepted', provider_id), ('rejected', why) or, for narration, ('audio', bytes)."""
    provider = request["provider"]
    status, raw = http_request(request["method"], request["url"], headers=_auth_headers(provider, key),
                               body=request["body"], params=request["params"],
                               timeout=240 if provider == "elevenlabs" else 60)
    if provider == "elevenlabs":
        if status >= 400:
            return "rejected", f"ElevenLabs HTTP {status}: {_scrub(raw[:300].decode('utf-8', 'replace'))}"
        return "audio", raw
    payload = _json_body(raw)
    if status >= 400 or not isinstance(payload, dict):
        return "rejected", f"{provider} HTTP {status}: {_scrub(raw[:300].decode('utf-8', 'replace'))}"
    if provider == "replicate":
        if not payload.get("id"):
            return "rejected", f"Replicate returned no job id: {_scrub(str(payload)[:300])}"
        return "accepted", str(payload["id"])
    if payload.get("code") != 200 or not isinstance(payload.get("data"), dict) or not payload["data"].get("taskId"):
        return "rejected", f"Kie refused the job: {_scrub(str(payload.get('msg'))[:300])}"
    return "accepted", str(payload["data"]["taskId"])


def poll_once(provider: str, provider_id: str, key: str) -> dict:
    """Return {'state': 'running'|'done'|'failed', 'urls': [...], 'error': str}."""
    if provider == "replicate":
        status, raw = http_request("GET", f"{REPLICATE_BASE}/predictions/{provider_id}",
                                   headers=_auth_headers(provider, key), timeout=30)
        payload = _json_body(raw)
        if status >= 400 or not isinstance(payload, dict):
            raise GenerateError(f"Replicate status check failed (HTTP {status})")
        state = payload.get("status")
        if state == "succeeded":
            output = payload.get("output")
            urls = output if isinstance(output, list) else ([output] if output else [])
            return {"state": "done", "urls": [u for u in urls if isinstance(u, str)], "error": ""}
        if state in ("failed", "canceled"):
            return {"state": "failed", "urls": [], "error": _scrub(str(payload.get("error"))[:500])}
        return {"state": "running", "urls": [], "error": ""}
    status, raw = http_request("GET", f"{KIE_BASE}/jobs/recordInfo", headers=_auth_headers(provider, key),
                               params={"taskId": provider_id}, timeout=30)
    payload = _json_body(raw)
    if status >= 400 or not isinstance(payload, dict) or payload.get("code") != 200 \
            or not isinstance(payload.get("data"), dict):
        raise GenerateError(f"Kie status check failed (HTTP {status})")
    data = payload["data"]
    state = data.get("state")
    if state == "success":
        try:
            result = json.loads(data.get("resultJson") or "")
        except (TypeError, ValueError):
            result = None
        urls = result.get("resultUrls") if isinstance(result, dict) else None
        if not isinstance(urls, list) or not urls:
            raise GenerateError("Kie reported success but returned no result link")
        return {"state": "done", "urls": [u for u in urls if isinstance(u, str)], "error": ""}
    if state == "fail":
        return {"state": "failed", "urls": [], "error": _scrub(str(data.get("failMsg"))[:500])}
    return {"state": "running", "urls": [], "error": ""}


def _download(url: str, out: Path) -> None:
    if not _is_url(url):
        raise GenerateError("The provider returned a result link that is not https")
    status, raw = http_request("GET", url, timeout=600)
    if status >= 400 or not raw:
        raise GenerateError(f"Could not download the result (HTTP {status})")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)


def wait_and_download(ledger_path: Path, job: dict, key: str, *, wait: float, interval: float) -> dict:
    """Poll a job that is already submitted, then save its result. Never submits."""
    started = time.monotonic()
    while True:
        try:
            state = poll_once(job["provider"], job["provider_job_id"], key)
        except OSError as error:
            raise GenerateError(
                f"Lost the connection while waiting ({_scrub(error)[:200]}). The job is in the ledger. Collect it "
                f"later with: generate.py resume {job['id']}. Do not submit it again.", code=4)
        if state["state"] == "done":
            if not state["urls"]:
                raise GenerateError("The job finished without a result link")
            _download(state["urls"][0], Path(job["output"]))
            return _update_job(ledger_path, job["id"], status="done")
        if state["state"] == "failed":
            job = _update_job(ledger_path, job["id"], status="failed", error=state["error"])
            raise GenerateError(
                f"The provider reported a failed job: {state['error']}. It is in the ledger as failed and does "
                "not count toward the ceiling. Check your provider bill if you are unsure.", code=5)
        if time.monotonic() - started > wait:
            raise GenerateError(
                f"Still running after {int(wait)} seconds. The job is in the ledger. Collect it later with: "
                f"generate.py resume {job['id']}. Do not submit it again.", code=4)
        time.sleep(interval)


# ------------------------------------------------------------------- commands

def _ledger_path(args) -> Path:
    return Path(args.ledger).expanduser().resolve()


def cmd_init(args) -> int:
    path = _ledger_path(args)
    if path.exists():
        raise GenerateError(f"{path} already exists. Change the ceiling with: generate.py ceiling AMOUNT --reason WHY")
    if args.ceiling <= 0:
        raise GenerateError("The ceiling must be more than zero")
    path.parent.mkdir(parents=True, exist_ok=True)
    save_ledger(path, {"version": 1, "currency": "USD", "ceiling": round(float(args.ceiling), 2),
                       "ceiling_history": [{"ceiling": round(float(args.ceiling), 2), "when": _now(),
                                            "reason": args.reason or "first ceiling"}],
                       "jobs": []})
    print(json.dumps({"ledger": str(path), "ceiling": round(float(args.ceiling), 2)}, indent=1))
    return 0


def cmd_ceiling(args) -> int:
    path = _ledger_path(args)
    if args.amount <= 0:
        raise GenerateError("The ceiling must be more than zero")
    with _locked(path):
        ledger = load_ledger(path)
        ledger["ceiling"] = round(float(args.amount), 2)
        ledger.setdefault("ceiling_history", []).append(
            {"ceiling": ledger["ceiling"], "when": _now(), "reason": args.reason})
        save_ledger(path, ledger)
    print(json.dumps({"ledger": str(path), "ceiling": ledger["ceiling"], "committed": committed(ledger)}, indent=1))
    return 0


def summary(ledger: dict) -> dict:
    spent = committed(ledger)
    by_kind: dict = {}
    for job in ledger["jobs"]:
        if job.get("status") in COUNTED:
            row = by_kind.setdefault(job["kind"], {"jobs": 0, "estimated_cost": 0.0, "seconds": 0.0})
            row["jobs"] += 1
            row["estimated_cost"] = round(row["estimated_cost"] + float(job["estimated_cost"]), 4)
            row["seconds"] = round(row["seconds"] + float(job.get("seconds") or 0), 2)
    return {
        "ceiling": ledger["ceiling"], "committed": spent, "remaining": round(float(ledger["ceiling"]) - spent, 4),
        "by_kind": by_kind,
        "open_jobs": [j["id"] for j in ledger["jobs"] if j.get("status") in ("submitting", "submitted", "unknown")],
        "not_counted": [j["id"] for j in ledger["jobs"] if j.get("status") not in COUNTED],
        "note": "Costs are estimates from list prices. Your provider bills are the real numbers.",
    }


def cmd_status(args) -> int:
    ledger = load_ledger(_ledger_path(args))
    report = summary(ledger)
    if args.jobs:
        report["jobs"] = ledger["jobs"]
    print(json.dumps(report, indent=1))
    return 0


def cmd_job(args) -> int:
    route = ROUTES[args.route]
    request = build_request(args)
    table = load_table(args.models)
    is_seedance = bool(route.get("seedance"))
    price = estimate(
        args.route, table, seconds=getattr(args, "seconds", None),
        resolution=(getattr(args, "resolution", None) or "720p") if route["kind"] == "video" else "720p",
        audio=is_seedance and not getattr(args, "no_audio", False),
        characters=request.get("characters"), rate=args.rate,
    )
    out = Path(args.out).expanduser().resolve()
    ledger_path = _ledger_path(args)
    job = {
        "kind": route["kind"], "route": args.route, "provider": route["provider"], "model": request["model"],
        "label": args.label, "seconds": getattr(args, "seconds", None), "characters": request.get("characters"),
        "estimated_cost": price["cost"], "rate": price["rate"], "rate_unit": price["unit"],
        "price_source": price["source"], "output": str(out), "provider_job_id": None,
    }

    if args.dry_run:
        report = {"dry_run": True, "sent": False, "request": describe_request(request),
                  "estimated_cost": price["cost"], "price": f"{price['rate']} {price['unit']}, from {price['source']}",
                  "output": str(out)}
        if ledger_path.is_file():
            ledger = load_ledger(ledger_path)
            spent = committed(ledger)
            report["ceiling"] = {"ceiling": ledger["ceiling"], "committed": spent,
                                 "would_be_refused": round(spent + price["cost"], 4) > float(ledger["ceiling"]) + 1e-9}
        else:
            report["ceiling"] = "no ledger yet; a real job needs one (generate.py init --ceiling AMOUNT)"
        print(json.dumps(report, indent=1))
        return 0

    if out.exists():
        raise GenerateError(f"{out} already exists. Choose a new name so an earlier take is not lost. Nothing was sent.")
    key = _key(route["provider"])
    job = reserve(ledger_path, job, args.reason)
    try:
        outcome, detail = submit(request, key)
    except GenerateError:
        raise
    except Exception as error:  # the request may or may not have reached the provider
        _update_job(ledger_path, job["id"], status="unknown", error=_scrub(error)[:300])
        raise GenerateError(
            f"The connection failed while sending {job['id']} ({_scrub(error)[:200]}). It may have been accepted, "
            "so it still counts toward the ceiling. Check the provider's dashboard before trying again.", code=4)
    if outcome == "rejected":
        _update_job(ledger_path, job["id"], status="rejected", error=detail)
        raise GenerateError(f"{detail}. Recorded as rejected. It does not count toward the ceiling.", code=5)
    if outcome == "audio":
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(detail)
        job = _update_job(ledger_path, job["id"], status="done")
    else:
        job = _update_job(ledger_path, job["id"], status="submitted", provider_job_id=detail)
        job = wait_and_download(ledger_path, job, key, wait=args.wait, interval=args.poll_seconds)
    ledger = load_ledger(ledger_path)
    print(json.dumps({"job": job, "committed": committed(ledger), "ceiling": ledger["ceiling"]}, indent=1))
    return 0


def cmd_resume(args) -> int:
    ledger_path = _ledger_path(args)
    ledger = load_ledger(ledger_path)
    job = next((j for j in ledger["jobs"] if j["id"] == args.job_id), None)
    if job is None:
        raise GenerateError(f"No job {args.job_id} in the ledger")
    if job.get("status") == "done":
        raise GenerateError(f"{args.job_id} is already done: {job['output']}")
    if not job.get("provider_job_id"):
        raise GenerateError(
            f"{args.job_id} has no provider job id, so it cannot be collected. Check the provider's dashboard.")
    if Path(job["output"]).exists():
        raise GenerateError(f"{job['output']} already exists")
    key = _key(job["provider"])
    job = wait_and_download(ledger_path, job, key, wait=args.wait, interval=args.poll_seconds)
    print(json.dumps({"job": job}, indent=1))
    return 0


# ------------------------------------------------------------------------ cli

def _routes_of(kind: str) -> list:
    return [name for name, route in ROUTES.items() if route["kind"] == kind]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="generate.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    def ledger_option(command):
        command.add_argument("--ledger", default=DEFAULT_LEDGER, help="ledger file (default: ledger.json here)")

    init = commands.add_parser("init", help="create the ledger and set the spend ceiling")
    init.add_argument("--ceiling", type=float, required=True, help="the most this project may spend, in US dollars")
    init.add_argument("--reason", help="where the number came from, for example the plan total")
    ledger_option(init)
    init.set_defaults(run=cmd_init)

    ceiling = commands.add_parser("ceiling", help="change the spend ceiling (a person's decision)")
    ceiling.add_argument("amount", type=float)
    ceiling.add_argument("--reason", required=True, help="why it changed and who agreed")
    ledger_option(ceiling)
    ceiling.set_defaults(run=cmd_ceiling)

    status = commands.add_parser("status", help="show the ceiling, what is committed and what is left")
    status.add_argument("--jobs", action="store_true", help="also list every job")
    ledger_option(status)
    status.set_defaults(run=cmd_status)

    def job_options(command, kind):
        routes = _routes_of(kind)
        command.add_argument("--route", choices=routes, default=routes[0] if len(routes) == 1 else None,
                             required=len(routes) > 1)
        command.add_argument("--out", required=True, help="where to save the result; must not exist yet")
        command.add_argument("--label", help="scene or asset name, for example scene-03 or cast-sheet")
        command.add_argument("--reason", help="what was wrong with the last take of this label")
        command.add_argument("--rate", type=float, help="price to use instead of the price table")
        command.add_argument("--models", default=str(DEFAULT_MODELS), help="price table JSON")
        command.add_argument("--dry-run", action="store_true", help="print the request and estimate; send nothing")
        command.add_argument("--wait", type=float, default=900, help="seconds to wait for the result")
        command.add_argument("--poll-seconds", type=float, default=5)
        ledger_option(command)
        command.set_defaults(run=cmd_job, kind=kind)

    image = commands.add_parser("image", help="make one still image")
    image.add_argument("--prompt")
    image.add_argument("--prompt-file")
    image.add_argument("--ref", action="append", help="reference image, file or https link; repeat in order")
    image.add_argument("--aspect-ratio", help="for example 9:16")
    image.add_argument("--resolution", help="for example 1K or 2K, if the model takes it")
    job_options(image, "image")

    video = commands.add_parser("video", help="make one video clip")
    video.add_argument("--prompt")
    video.add_argument("--prompt-file")
    video.add_argument("--seconds", type=float, required=True)
    video.add_argument("--start-image", help="first frame, file or https link")
    video.add_argument("--end-image", help="last frame, where the model takes one")
    video.add_argument("--ref", action="append", help="reference image; repeat in order (Image1, Image2, ...)")
    video.add_argument("--voice", action="append", help="voice sample; repeat in order (Audio1, Audio2, ...)")
    video.add_argument("--resolution", help="480p, 720p or 1080p")
    video.add_argument("--aspect-ratio", help="for example 9:16")
    video.add_argument("--no-audio", action="store_true", help="ask Seedance for a silent clip")
    job_options(video, "video")

    narration = commands.add_parser("narration", help="make a narration or a voice sample")
    narration.add_argument("--text")
    narration.add_argument("--text-file")
    narration.add_argument("--voice-id", required=True, help="the voice's id in your ElevenLabs library")
    narration.add_argument("--model-id", help="ElevenLabs model id (default: eleven_v4)")
    narration.add_argument("--output-format", default="mp3_44100_128")
    job_options(narration, "narration")

    resume = commands.add_parser("resume", help="collect a job that is already in the ledger; never resubmits")
    resume.add_argument("job_id")
    resume.add_argument("--wait", type=float, default=900)
    resume.add_argument("--poll-seconds", type=float, default=5)
    ledger_option(resume)
    resume.set_defaults(run=cmd_resume)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.run(args)
    except GenerateError as error:
        print(_scrub(f"ERROR: {error}"), file=sys.stderr)
        return error.code
    except (OSError, ValueError) as error:
        print(_scrub(f"ERROR: {error}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
