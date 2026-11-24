#!/usr/bin/env python3
"""Assemble a voiceover film from approved clips and one narration file.

This module deliberately does not generate narration or motion.  It turns an
explicit, project-relative JSON edit plan into one film and one provenance
receipt, while treating the narration file as the authoritative soundtrack.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping

_TOOLKIT_DIR = str(Path(__file__).resolve().parent)
if _TOOLKIT_DIR not in sys.path:  # sibling helper, also when loaded by file path
    sys.path.insert(0, _TOOLKIT_DIR)
from filehash import sha256_file  # noqa: E402


SCHEMA_VERSION = 1
FRAME_TOLERANCE = Decimal("0.0011")
DURATION_TOLERANCE = Decimal("0.002")
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class NarratedAssemblyError(ValueError):
    """The edit plan or its local assets are missing, stale, or unsafe."""


def sha256(path: Path) -> str:
    return sha256_file(path)


def _json(path: Path) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise NarratedAssemblyError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise NarratedAssemblyError(f"non-finite JSON number is forbidden: {value}")

    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except OSError as error:
        raise NarratedAssemblyError(f"could not read JSON: {path}") from error
    except json.JSONDecodeError as error:
        raise NarratedAssemblyError(f"invalid JSON in {path}: {error}") from error


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NarratedAssemblyError(f"{label} must be an object")
    return dict(value)


def _keys(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown or missing:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(unknown)))
        raise NarratedAssemblyError(f"{label} fields: {'; '.join(details)}")


def _text(value: Any, label: str, *, normalize: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NarratedAssemblyError(f"{label} must be a non-empty string")
    return " ".join(value.split()) if normalize else value.strip()


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise NarratedAssemblyError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise NarratedAssemblyError(f"{label} must be finite numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise NarratedAssemblyError(f"{label} must be finite numeric") from error
    if not result.is_finite():
        raise NarratedAssemblyError(f"{label} must be finite numeric")
    return result


def _frames(value: Any, fps: int, label: str, *, allow_zero: bool = False) -> int:
    seconds = _decimal(value, label)
    if seconds < 0 or (seconds == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise NarratedAssemblyError(f"{label} must be {qualifier}")
    exact = seconds * fps
    result = int(exact.to_integral_value(rounding=ROUND_HALF_UP))
    if abs(exact - result) > FRAME_TOLERANCE:
        raise NarratedAssemblyError(f"{label} must align to the {fps} fps frame grid")
    if result == 0 and not allow_zero:
        raise NarratedAssemblyError(f"{label} must be at least one frame")
    return result


def _seconds(frames: int, fps: int) -> str:
    return f"{frames / fps:.9f}".rstrip("0").rstrip(".")


def _project_path(root: Path, value: Any, label: str) -> Path:
    raw = _text(value, f"{label}.path")
    supplied = Path(raw)
    if supplied.is_absolute():
        raise NarratedAssemblyError(f"{label}.path must be project-relative")
    result = (root / supplied).resolve()
    try:
        result.relative_to(root)
    except ValueError as error:
        raise NarratedAssemblyError(f"{label}.path leaves the project root") from error
    return result


def _bound_file(root: Path, value: Any, label: str) -> tuple[Path, str]:
    item = _mapping(value, label)
    _keys(item, {"path", "sha256"}, {"path", "sha256"}, label)
    path = _project_path(root, item.get("path"), label)
    expected = item.get("sha256")
    if not isinstance(expected, str) or SHA256.fullmatch(expected) is None:
        raise NarratedAssemblyError(f"{label}.sha256 must be a lowercase SHA-256 digest")
    if not path.is_file():
        raise NarratedAssemblyError(f"{label} is missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise NarratedAssemblyError(f"{label} bytes do not match sha256")
    return path, actual


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _source(root: Path, role: str, path: Path, digest: str) -> dict[str, str]:
    return {"role": role, "path": _relative(root, path), "sha256": digest}


def resolve_media_tools(*, require_drawtext: bool = False) -> tuple[str, str]:
    """Select a matching ffmpeg/ffprobe pair; prefer ffmpeg-full for text."""
    configured = os.environ.get("NARRATED_FFMPEG")
    candidates = [
        configured,
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
        shutil.which("ffmpeg-full"),
        shutil.which("ffmpeg"),
    ]
    failures: list[str] = []
    for candidate in dict.fromkeys(str(item) for item in candidates if item):
        ffmpeg = Path(candidate)
        # Keep the executable suffix so a Windows ffmpeg.exe pairs with ffprobe.exe.
        ffprobe = ffmpeg.with_name("ffprobe" + ffmpeg.suffix)
        if not ffmpeg.is_file() or not os.access(ffmpeg, os.X_OK):
            failures.append(f"{ffmpeg}: unavailable")
            continue
        if not ffprobe.is_file() or not os.access(ffprobe, os.X_OK):
            failures.append(f"{ffmpeg}: matching ffprobe unavailable")
            continue
        if require_drawtext:
            result = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-filters"], capture_output=True, text=True,
            )
            if result.returncode or " drawtext " not in result.stdout + result.stderr:
                failures.append(f"{ffmpeg}: drawtext unavailable")
                continue
        return str(ffmpeg), str(ffprobe)
    detail = "; ".join(failures) or "no candidates found"
    feature = " with drawtext" if require_drawtext else ""
    raise NarratedAssemblyError(f"a matching ffmpeg/ffprobe pair{feature} is required ({detail})")


def _probe(path: Path, selector: str, tools: tuple[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    _, ffprobe = tools
    try:
        raw = subprocess.check_output([
            ffprobe, "-v", "error", "-count_frames", "-select_streams", selector,
            "-show_streams", "-show_format", "-of", "json", str(path),
        ])
        payload = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise NarratedAssemblyError(f"could not probe media: {path}") from error
    streams = payload.get("streams") or []
    if not streams:
        raise NarratedAssemblyError(f"media has no {selector} stream: {path}")
    return streams[0], payload.get("format") or {}


def _fraction(value: Any, label: str) -> Fraction:
    try:
        result = Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise NarratedAssemblyError(f"{label} is invalid") from error
    if result <= 0:
        raise NarratedAssemblyError(f"{label} must be positive")
    return result


def probe_video(path: Path, tools: tuple[str, str]) -> dict[str, Any]:
    stream, _ = _probe(path, "v:0", tools)
    fps = _fraction(stream.get("avg_frame_rate") or stream.get("r_frame_rate"), "video fps")
    try:
        frames = int(stream.get("nb_read_frames") or stream.get("nb_frames"))
        width, height = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise NarratedAssemblyError(f"video facts are unavailable: {path}") from error
    if frames <= 0 or width <= 0 or height <= 0:
        raise NarratedAssemblyError(f"video facts are invalid: {path}")
    return {
        "width": width, "height": height,
        "fps": f"{fps.numerator}/{fps.denominator}",
        "frames": frames, "duration_seconds": float(Fraction(frames, 1) / fps),
    }


def probe_audio(path: Path, tools: tuple[str, str]) -> dict[str, Any]:
    stream, container = _probe(path, "a:0", tools)
    duration = stream.get("duration")
    if duration in (None, "N/A"):
        duration = container.get("duration")
    seconds = _decimal(duration, f"audio duration for {path}")
    if seconds <= 0:
        raise NarratedAssemblyError(f"audio duration must be positive: {path}")
    return {"duration_seconds": float(seconds)}


def _require_decoded_audio_signal(path: Path, tools: tuple[str, str]) -> None:
    """Reject empty or exactly digital-silent narration without a loudness guess."""
    ffmpeg, _ = tools
    process = subprocess.Popen(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-map", "0:a:0", "-ac", "1", "-c:a", "pcm_s16le", "-f", "s16le", "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    decoded = 0
    has_signal = False
    for block in iter(lambda: process.stdout.read(1 << 16), b""):
        decoded += len(block)
        has_signal = has_signal or any(block)
    return_code = process.wait()
    if return_code:
        raise NarratedAssemblyError(f"could not decode narration audio: {path}")
    if decoded == 0:
        raise NarratedAssemblyError(f"narration has no decoded audio samples: {path}")
    if not has_signal:
        raise NarratedAssemblyError(f"narration is digital silence: {path}")


def _style(raw: Any, canvas: dict[str, int]) -> dict[str, Any]:
    value = {} if raw is None else _mapping(raw, "captions.style")
    allowed = {
        "max_width", "max_lines", "min_font_size", "max_font_size", "x", "y",
        "bottom_margin", "font_color", "border_color", "border_width", "line_spacing",
    }
    _keys(value, allowed, set(), "captions.style")
    height, width = canvas["height"], canvas["width"]
    maximum = _integer(value.get("max_font_size", max(12, height // 24)), "captions.style.max_font_size", 6, height)
    minimum = _integer(value.get("min_font_size", max(6, maximum * 2 // 3)), "captions.style.min_font_size", 6, maximum)
    x = value.get("x", "center")
    if x != "center":
        x = _integer(x, "captions.style.x", 0, width - 1)
    y = value.get("y")
    bottom = value.get("bottom_margin", max(0, height // 4))
    if y is not None:
        y = _integer(y, "captions.style.y", 0, height - 1)
        bottom = None
    else:
        bottom = _integer(bottom, "captions.style.bottom_margin", 0, height - 1)
    font_color = value.get("font_color", "#FFFFFF")
    border_color = value.get("border_color", "#000000")
    if not isinstance(font_color, str) or HEX_COLOR.fullmatch(font_color) is None:
        raise NarratedAssemblyError("captions.style.font_color must be #RRGGBB")
    if not isinstance(border_color, str) or HEX_COLOR.fullmatch(border_color) is None:
        raise NarratedAssemblyError("captions.style.border_color must be #RRGGBB")
    return {
        "max_width": _integer(value.get("max_width", max(1, width * 6 // 7)), "captions.style.max_width", 1, width),
        "max_lines": _integer(value.get("max_lines", 3), "captions.style.max_lines", 1, 8),
        "min_font_size": minimum, "max_font_size": maximum, "x": x, "y": y,
        "bottom_margin": bottom, "font_color": font_color,
        "border_color": border_color,
        "border_width": _integer(value.get("border_width", max(1, maximum // 9)), "captions.style.border_width", 0, maximum),
        "line_spacing": _integer(value.get("line_spacing", max(0, maximum // 7)), "captions.style.line_spacing", 0, maximum),
    }


def _caption_events(value: Any, fps: int, total_frames: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise NarratedAssemblyError("captions events must be a non-empty list")
    result = []
    previous_start = -1
    for index, raw in enumerate(value, 1):
        item = _mapping(raw, f"caption {index}")
        _keys(item, {"text", "start", "end"}, {"text", "start", "end"}, f"caption {index}")
        text = _text(item["text"], f"caption {index}.text", normalize=True)
        start = _frames(item["start"], fps, f"caption {index}.start", allow_zero=True)
        end = _frames(item["end"], fps, f"caption {index}.end")
        if end <= start:
            raise NarratedAssemblyError(f"caption {index}.end must be after start")
        if start < previous_start:
            raise NarratedAssemblyError("caption events must be ordered by start time")
        if end > total_frames:
            raise NarratedAssemblyError(f"caption {index} extends beyond the picture")
        previous_start = start
        result.append({"text": text, "start_frame": start, "end_frame": end})
    return result


def _captions(
    root: Path, raw: Any, canvas: dict[str, int], total_frames: int,
    sources: list[dict[str, str]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    value = _mapping(raw, "captions")
    _keys(value, {"path", "sha256", "events", "font", "style"}, {"font"}, "captions")
    has_file, has_inline = "path" in value or "sha256" in value, "events" in value
    if has_file == has_inline:
        raise NarratedAssemblyError("captions needs exactly one of path+sha256 or events")
    if has_file:
        caption_path, caption_sha = _bound_file(
            root, {"path": value.get("path"), "sha256": value.get("sha256")}, "captions",
        )
        payload = _json(caption_path)
        events = payload.get("events") if isinstance(payload, Mapping) else payload
        sources.append(_source(root, "captions", caption_path, caption_sha))
    else:
        events = value["events"]
    font_path, font_sha = _bound_file(root, value["font"], "caption font")
    sources.append(_source(root, "caption_font", font_path, font_sha))
    return {
        "events": _caption_events(events, canvas["fps"], total_frames),
        "font_path": font_path, "style": _style(value.get("style"), canvas),
    }


def _endcard(
    root: Path, raw: Any, canvas: dict[str, int], sources: list[dict[str, str]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    value = _mapping(raw, "endcard")
    allowed = {
        "seconds", "background", "text", "font", "text_style", "logo", "logo_width",
        "logo_x", "logo_y", "allow_caption_duplicate",
    }
    _keys(value, allowed, {"seconds"}, "endcard")
    fps, width, height = canvas["fps"], canvas["width"], canvas["height"]
    background = value.get("background", "#000000")
    if not isinstance(background, str) or HEX_COLOR.fullmatch(background) is None:
        raise NarratedAssemblyError("endcard.background must be #RRGGBB")
    text = value.get("text")
    if text is not None:
        text = _text(text, "endcard.text", normalize=True)
    result: dict[str, Any] = {
        "frames": _frames(value["seconds"], fps, "endcard.seconds"),
        "background": background, "text": text,
        "allow_caption_duplicate": value.get("allow_caption_duplicate", False),
        "logo": None, "font_path": None,
    }
    if not isinstance(result["allow_caption_duplicate"], bool):
        raise NarratedAssemblyError("endcard.allow_caption_duplicate must be boolean")
    if "logo" in value:
        logo_path, logo_sha = _bound_file(root, value["logo"], "endcard logo")
        sources.append(_source(root, "endcard_logo", logo_path, logo_sha))
        logo_width = _integer(value.get("logo_width", min(width // 2, width)), "endcard.logo_width", 1, width)
        logo_x = value.get("logo_x", "center")
        if logo_x != "center":
            logo_x = _integer(logo_x, "endcard.logo_x", 0, width - 1)
        result["logo"] = {
            "path": logo_path, "width": logo_width, "x": logo_x,
            "y": _integer(value.get("logo_y", height // 5), "endcard.logo_y", 0, height - 1),
        }
    if text is not None:
        if "font" in value:
            font_path, font_sha = _bound_file(root, value["font"], "endcard font")
            sources.append(_source(root, "endcard_font", font_path, font_sha))
            result["font_path"] = font_path
        style = {} if value.get("text_style") is None else _mapping(value["text_style"], "endcard.text_style")
        _keys(style, {"font_size", "max_width", "y", "font_color", "border_color", "border_width"}, set(), "endcard.text_style")
        font_size = _integer(style.get("font_size", max(12, height // 24)), "endcard.text_style.font_size", 6, height)
        result["text_style"] = {
            "font_size": font_size,
            "max_width": _integer(style.get("max_width", max(1, width * 6 // 7)), "endcard.text_style.max_width", 1, width),
            "y": _integer(style.get("y", height * 3 // 5), "endcard.text_style.y", 0, height - 1),
            "font_color": style.get("font_color", "#FFFFFF"),
            "border_color": style.get("border_color", "#000000"),
            "border_width": _integer(style.get("border_width", max(1, font_size // 9)), "endcard.text_style.border_width", 0, font_size),
        }
        for key in ("font_color", "border_color"):
            if not isinstance(result["text_style"][key], str) or HEX_COLOR.fullmatch(result["text_style"][key]) is None:
                raise NarratedAssemblyError(f"endcard.text_style.{key} must be #RRGGBB")
    return result


def load_plan(project_root: Path | str, edit_plan: Path | str) -> dict[str, Any]:
    """Validate and normalize one explicit project/edit-plan pair without writing."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NarratedAssemblyError(f"project root is not a directory: {root}")
    plan_path = Path(edit_plan).expanduser()
    if not plan_path.is_absolute():
        plan_path = root / plan_path
    plan_path = plan_path.resolve()
    try:
        plan_path.relative_to(root)
    except ValueError as error:
        raise NarratedAssemblyError("edit plan must be inside the project root") from error
    if not plan_path.is_file():
        raise NarratedAssemblyError(f"edit plan is missing: {plan_path}")
    raw = _json(plan_path)
    plan = _mapping(raw, "edit plan")
    allowed = {
        "schema_version", "canvas", "narration", "segments", "captions", "endcard",
        "sfx", "output", "total_frames", "duration_seconds",
    }
    _keys(plan, allowed, {"schema_version", "canvas", "narration", "segments", "output"}, "edit plan")
    if plan["schema_version"] != SCHEMA_VERSION:
        raise NarratedAssemblyError(f"schema_version must be {SCHEMA_VERSION}")
    canvas_raw = _mapping(plan["canvas"], "canvas")
    _keys(canvas_raw, {"width", "height", "fps"}, {"width", "height", "fps"}, "canvas")
    canvas = {
        "width": _integer(canvas_raw["width"], "canvas.width", 16, 8192),
        "height": _integer(canvas_raw["height"], "canvas.height", 16, 8192),
        "fps": _integer(canvas_raw["fps"], "canvas.fps", 1, 120),
    }
    if canvas["width"] % 2 or canvas["height"] % 2:
        raise NarratedAssemblyError("canvas width and height must be even for yuv420p")
    wants_text = plan.get("captions") is not None or (
        isinstance(plan.get("endcard"), Mapping) and plan["endcard"].get("text") is not None
    )
    tools = resolve_media_tools(require_drawtext=wants_text)
    sources: list[dict[str, str]] = []
    narration_path, narration_sha = _bound_file(root, plan["narration"], "narration")
    sources.append(_source(root, "narration", narration_path, narration_sha))
    narration_facts = probe_audio(narration_path, tools)
    _require_decoded_audio_signal(narration_path, tools)

    segments_raw = plan["segments"]
    if not isinstance(segments_raw, list) or not segments_raw:
        raise NarratedAssemblyError("segments must be a non-empty list")
    segments: list[dict[str, Any]] = []
    cursor = 0
    for index, raw_segment in enumerate(segments_raw, 1):
        item = _mapping(raw_segment, f"segment {index}")
        _keys(
            item, {"id", "source", "sha256", "in_seconds", "duration_seconds", "crop"},
            {"source", "sha256", "in_seconds", "duration_seconds"}, f"segment {index}",
        )
        source_path, source_sha = _bound_file(
            root, {"path": item["source"], "sha256": item["sha256"]}, f"segment {index}",
        )
        segment_id = _text(item.get("id", f"segment-{index}"), f"segment {index}.id")
        if any(segment["id"] == segment_id for segment in segments):
            raise NarratedAssemblyError(f"duplicate segment id: {segment_id}")
        start = _frames(item["in_seconds"], canvas["fps"], f"segment {index}.in_seconds", allow_zero=True)
        frames = _frames(item["duration_seconds"], canvas["fps"], f"segment {index}.duration_seconds")
        facts = probe_video(source_path, tools)
        if Decimal(str(facts["duration_seconds"])) + DURATION_TOLERANCE < Decimal(start + frames) / canvas["fps"]:
            raise NarratedAssemblyError(f"segment {index} video is too short for its selected timing")
        crop = None
        if item.get("crop") is not None:
            crop_raw = _mapping(item["crop"], f"segment {index}.crop")
            _keys(crop_raw, {"x", "y", "width", "height"}, {"x", "y", "width", "height"}, f"segment {index}.crop")
            crop = {
                "x": _integer(crop_raw["x"], f"segment {index}.crop.x", 0, facts["width"] - 1),
                "y": _integer(crop_raw["y"], f"segment {index}.crop.y", 0, facts["height"] - 1),
                "width": _integer(crop_raw["width"], f"segment {index}.crop.width", 1, facts["width"]),
                "height": _integer(crop_raw["height"], f"segment {index}.crop.height", 1, facts["height"]),
            }
            if crop["x"] + crop["width"] > facts["width"] or crop["y"] + crop["height"] > facts["height"]:
                raise NarratedAssemblyError(f"segment {index}.crop leaves the source frame")
        sources.append(_source(root, f"segment:{segment_id}", source_path, source_sha))
        segments.append({
            "id": segment_id, "path": source_path, "in_frame": start, "frames": frames,
            "timeline_start_frame": cursor, "timeline_end_frame": cursor + frames,
            "crop": crop, "source_facts": facts,
        })
        cursor += frames

    endcard = _endcard(root, plan.get("endcard"), canvas, sources)
    picture_frames = cursor + (endcard["frames"] if endcard else 0)
    if "total_frames" in plan and plan["total_frames"] != picture_frames:
        raise NarratedAssemblyError("total_frames does not match the assembled frame count")
    if "duration_seconds" in plan and _frames(plan["duration_seconds"], canvas["fps"], "duration_seconds") != picture_frames:
        raise NarratedAssemblyError("duration_seconds does not match the assembled frame count")
    captions = _captions(root, plan.get("captions"), canvas, picture_frames, sources)
    if endcard and endcard["text"] is not None and captions and not endcard["allow_caption_duplicate"]:
        normalized_endcard = " ".join(endcard["text"].casefold().split())
        for event in captions["events"]:
            overlaps = event["end_frame"] > cursor and event["start_frame"] < picture_frames
            normalized_caption = " ".join(event["text"].casefold().split())
            if overlaps and normalized_caption == normalized_endcard:
                raise NarratedAssemblyError(
                    "endcard.text duplicates an overlapping caption; remove one or set "
                    "endcard.allow_caption_duplicate=true after deliberate review"
                )
    if endcard and endcard["text"] is not None and endcard["font_path"] is None:
        if captions:
            endcard["font_path"] = captions["font_path"]
        else:
            raise NarratedAssemblyError("endcard.font is required when text is configured without captions")

    sfx_raw = plan.get("sfx", [])
    if not isinstance(sfx_raw, list):
        raise NarratedAssemblyError("sfx must be a list")
    sfx: list[dict[str, Any]] = []
    for index, raw_sfx in enumerate(sfx_raw, 1):
        item = _mapping(raw_sfx, f"sfx {index}")
        _keys(item, {"source", "sha256", "in_seconds", "duration_seconds", "timeline_start_seconds", "gain_db", "fade_seconds"}, {"source", "sha256", "in_seconds", "duration_seconds", "timeline_start_seconds"}, f"sfx {index}")
        source_path, source_sha = _bound_file(
            root, {"path": item["source"], "sha256": item["sha256"]}, f"sfx {index}",
        )
        start = _frames(item["in_seconds"], canvas["fps"], f"sfx {index}.in_seconds", allow_zero=True)
        frames = _frames(item["duration_seconds"], canvas["fps"], f"sfx {index}.duration_seconds")
        timeline = _frames(item["timeline_start_seconds"], canvas["fps"], f"sfx {index}.timeline_start_seconds", allow_zero=True)
        if timeline + frames > picture_frames:
            raise NarratedAssemblyError(f"sfx {index} extends beyond the picture")
        facts = probe_audio(source_path, tools)
        if Decimal(str(facts["duration_seconds"])) + DURATION_TOLERANCE < Decimal(start + frames) / canvas["fps"]:
            raise NarratedAssemblyError(f"sfx {index} audio is too short for its selected timing")
        gain = _decimal(item.get("gain_db", 0), f"sfx {index}.gain_db")
        if not Decimal("-60") <= gain <= Decimal("24"):
            raise NarratedAssemblyError(f"sfx {index}.gain_db must be from -60 to +24")
        fade = _decimal(item.get("fade_seconds", 0), f"sfx {index}.fade_seconds")
        if fade < 0 or fade * 2 > Decimal(frames) / canvas["fps"]:
            raise NarratedAssemblyError(f"sfx {index}.fade_seconds must be non-negative and no more than half the clip")
        sources.append(_source(root, f"sfx:{index}", source_path, source_sha))
        sfx.append({"path": source_path, "in_frame": start, "frames": frames, "timeline_start_frame": timeline, "gain_db": gain, "fade_seconds": fade})

    picture_seconds = Decimal(picture_frames) / canvas["fps"]
    narration_seconds = Decimal(str(narration_facts["duration_seconds"]))
    if narration_seconds > picture_seconds:
        raise NarratedAssemblyError(
            f"narration is longer than the picture ({narration_seconds}s > {picture_seconds}s); "
            "assembly refuses to truncate words"
        )
    output_raw = _mapping(plan["output"], "output")
    _keys(output_raw, {"path", "receipt"}, {"path", "receipt"}, "output")
    output_path = _project_path(root, output_raw["path"], "output")
    receipt_path = _project_path(root, output_raw["receipt"], "receipt")
    if output_path == receipt_path:
        raise NarratedAssemblyError("output and receipt must be different files")
    input_paths = {plan_path, *(root / row["path"] for row in sources)}
    if output_path in input_paths:
        raise NarratedAssemblyError("output path collides with an input")
    if receipt_path in input_paths:
        raise NarratedAssemblyError("receipt path collides with an input")
    if output_path.exists():
        raise NarratedAssemblyError(f"refusing to overwrite existing output: {output_path}")
    if receipt_path.exists():
        raise NarratedAssemblyError(f"refusing to overwrite existing receipt: {receipt_path}")
    return {
        "schema_version": SCHEMA_VERSION, "root": root, "plan_path": plan_path,
        "plan_sha256": sha256(plan_path), "canvas": canvas, "tools": tools,
        "narration_path": narration_path, "narration_duration_seconds": float(narration_seconds),
        "segments": segments, "captions": captions, "endcard": endcard, "sfx": sfx,
        "sources": sources, "total_frames": picture_frames,
        "duration_seconds": float(picture_seconds),
        "silence_padding_seconds": float(max(Decimal(0), picture_seconds - narration_seconds)),
        "output_path": output_path, "receipt_path": receipt_path,
    }


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _color(value: str) -> str:
    return "0x" + value[1:]


def _wrap(text: str, font_path: Path, style: Mapping[str, Any]) -> tuple[str, int]:
    try:
        from PIL import ImageFont
    except ImportError as error:
        raise NarratedAssemblyError("Pillow is required to fit configured text") from error
    for size in range(style["max_font_size"], style["min_font_size"] - 1, -1):
        font = ImageFont.truetype(str(font_path), size)
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if font.getlength(candidate) <= style["max_width"]:
                current = candidate
            else:
                if not current or font.getlength(word) > style["max_width"]:
                    break
                lines.append(current)
                current = word
        else:
            if current:
                lines.append(current)
            if len(lines) <= style.get("max_lines", 1):
                return "\n".join(lines), size
    raise NarratedAssemblyError(f"text cannot fit configured bounds: {text!r}")


def _drawtext(text_file: Path, font: Path, style: Mapping[str, Any], size: int, *, enable: str | None = None) -> str:
    x = "(w-text_w)/2" if style.get("x", "center") == "center" else str(style["x"])
    if style.get("y") is not None:
        y = str(style["y"])
    else:
        y = f"h-{style['bottom_margin']}-text_h"
    value = (
        f"drawtext=fontfile='{_filter_path(font)}':textfile='{_filter_path(text_file)}'"
        f":fontcolor={_color(style['font_color'])}:fontsize={size}"
        f":borderw={style['border_width']}:bordercolor={_color(style['border_color'])}"
        f":line_spacing={style.get('line_spacing', 0)}:fix_bounds=1:x={x}:y={y}"
    )
    if enable:
        value += f":enable='{enable}'"
    return value


def _verify_bindings(plan: Mapping[str, Any]) -> None:
    if sha256(plan["plan_path"]) != plan["plan_sha256"]:
        raise NarratedAssemblyError("edit plan changed after validation")
    for source in plan["sources"]:
        path = plan["root"] / source["path"]
        if not path.is_file() or sha256(path) != source["sha256"]:
            raise NarratedAssemblyError(f"source changed after validation: {source['path']}")


def _validate_output(path: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    facts = probe_video(path, plan["tools"])
    canvas = plan["canvas"]
    if facts["frames"] != plan["total_frames"]:
        raise NarratedAssemblyError("rendered output frame count does not match the plan")
    if (facts["width"], facts["height"]) != (canvas["width"], canvas["height"]):
        raise NarratedAssemblyError("rendered output canvas does not match the plan")
    if _fraction(facts["fps"], "rendered fps") != Fraction(canvas["fps"], 1):
        raise NarratedAssemblyError("rendered output fps does not match the plan")
    audio = probe_audio(path, plan["tools"])
    if abs(Decimal(str(audio["duration_seconds"])) - Decimal(str(plan["duration_seconds"]))) > Decimal("0.05"):
        raise NarratedAssemblyError("rendered audio duration does not match the picture")
    return {**facts, "audio_duration_seconds": audio["duration_seconds"]}


def _exclusive_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _publish_exclusive(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
        return
    except FileExistsError as error:
        raise NarratedAssemblyError(
            f"destination appeared during render; refusing to overwrite: {destination}"
        ) from error
    except OSError:
        # Hard links are unavailable on some volumes (exFAT, some synced
        # folders). Exclusive create keeps the same never-overwrite guarantee.
        pass
    try:
        with destination.open("xb") as target, temporary.open("rb") as source:
            shutil.copyfileobj(source, target)
    except FileExistsError as error:
        raise NarratedAssemblyError(
            f"destination appeared during render; refusing to overwrite: {destination}"
        ) from error
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _write_recovery_receipt(root: Path, output: Path, receipt_path: Path, receipt: Mapping[str, Any]) -> Path:
    """Durably bind one verified output before publishing its final receipt."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{output.stem}-receipt-recovery-", suffix=".json", dir=output.parent,
    )
    recovery_path = Path(raw_path)
    payload = {
        **receipt,
        "status": "PENDING_RECEIPT",
        "receipt": {"path": _relative(root, receipt_path)},
    }
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        recovery_path.unlink(missing_ok=True)
        raise
    return recovery_path


def _inside_root(root: Path, value: Path | str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise NarratedAssemblyError(f"{label} must be inside the project root") from error
    return path


def recover_receipt(project_root: Path | str, recovery_receipt: Path | str) -> dict[str, Any]:
    """Finalize a failed receipt write without rerendering a hash-bound output."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise NarratedAssemblyError(f"project root is not a directory: {root}")
    recovery_path = _inside_root(root, recovery_receipt, "recovery receipt")
    if not recovery_path.is_file():
        raise NarratedAssemblyError(f"recovery receipt is missing: {recovery_path}")
    payload = _mapping(_json(recovery_path), "recovery receipt")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("status") != "PENDING_RECEIPT":
        raise NarratedAssemblyError("recovery receipt is not a pending narrated assembly")
    if payload.get("project_root") != str(root):
        raise NarratedAssemblyError("recovery receipt belongs to a different project root")
    plan = _mapping(payload.get("plan"), "recovery receipt.plan")
    _keys(plan, {"path", "sha256"}, {"path", "sha256"}, "recovery receipt.plan")
    plan_path = _project_path(root, plan["path"], "recovery receipt.plan")
    if not plan_path.is_file() or not isinstance(plan["sha256"], str) or SHA256.fullmatch(plan["sha256"]) is None or sha256(plan_path) != plan["sha256"]:
        raise NarratedAssemblyError("recovery receipt plan bytes do not match its saved binding")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise NarratedAssemblyError("recovery receipt.sources must be a list")
    for index, source in enumerate(sources, 1):
        item = _mapping(source, f"recovery receipt source {index}")
        _keys(item, {"role", "path", "sha256"}, {"role", "path", "sha256"}, f"recovery receipt source {index}")
        source_path = _project_path(root, item["path"], f"recovery receipt source {index}")
        if not source_path.is_file() or not isinstance(item["sha256"], str) or SHA256.fullmatch(item["sha256"]) is None or sha256(source_path) != item["sha256"]:
            raise NarratedAssemblyError(f"recovery receipt source bytes do not match: {item['path']}")
    output = _mapping(payload.get("output"), "recovery receipt.output")
    if not isinstance(output.get("path"), str) or not isinstance(output.get("sha256"), str) or SHA256.fullmatch(output["sha256"]) is None:
        raise NarratedAssemblyError("recovery receipt.output needs path and SHA-256")
    output_path = _project_path(root, output["path"], "recovery receipt.output")
    if not output_path.is_file() or sha256(output_path) != output["sha256"]:
        raise NarratedAssemblyError("recovery receipt output bytes do not match its saved binding")
    destination = _mapping(payload.get("receipt"), "recovery receipt.destination")
    _keys(destination, {"path"}, {"path"}, "recovery receipt.destination")
    receipt_path = _project_path(root, destination["path"], "recovery receipt.destination")
    if receipt_path in {output_path, recovery_path}:
        raise NarratedAssemblyError("recovery receipt destination collides with protected output evidence")
    receipt = dict(payload)
    receipt.pop("receipt")
    receipt["status"] = "ASSEMBLED"
    try:
        _exclusive_json(receipt_path, receipt)
    except FileExistsError as error:
        raise NarratedAssemblyError(f"refusing to overwrite existing receipt: {receipt_path}") from error
    recovery_path.unlink(missing_ok=True)
    return receipt


def _assembled_receipt(
    plan: Mapping[str, Any], output_sha256: str, facts: Mapping[str, Any], ffmpeg: str,
) -> dict[str, Any]:
    output: Path = plan["output_path"]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ASSEMBLED",
        "project_root": str(plan["root"]),
        "plan": {"path": _relative(plan["root"], plan["plan_path"]), "sha256": plan["plan_sha256"]},
        "canvas": plan["canvas"],
        "picture_frames": plan["total_frames"],
        "picture_duration_seconds": plan["duration_seconds"],
        "narration_duration_seconds": plan["narration_duration_seconds"],
        "silence_padding_seconds": plan["silence_padding_seconds"],
        "endcard": {
            "frames": plan["endcard"]["frames"] if plan["endcard"] else 0,
            "text": plan["endcard"]["text"] if plan["endcard"] else None,
        },
        "sources": plan["sources"],
        "output": {"path": _relative(plan["root"], output), "sha256": output_sha256, **facts},
        "ffmpeg": {"path": ffmpeg},
    }


def assemble(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Render one validated plan without overwriting any input or prior output."""
    _verify_bindings(plan)
    output: Path = plan["output_path"]
    receipt_path: Path = plan["receipt_path"]
    if output.exists() or receipt_path.exists():
        raise NarratedAssemblyError("output or receipt appeared after validation; refusing to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg, _ = plan["tools"]
    canvas, fps = plan["canvas"], plan["canvas"]["fps"]
    total_frames = plan["total_frames"]
    inputs: list[str] = []
    filters: list[str] = []
    video_labels: list[str] = []
    for index, segment in enumerate(plan["segments"]):
        inputs.extend(["-i", str(segment["path"])])
        crop = ""
        if segment["crop"]:
            value = segment["crop"]
            crop = f",crop={value['width']}:{value['height']}:{value['x']}:{value['y']}"
        filters.append(
            f"[{index}:v:0]trim=start={_seconds(segment['in_frame'], fps)}:"
            f"end={_seconds(segment['in_frame'] + segment['frames'], fps)},"
            f"setpts=PTS-STARTPTS{crop},scale={canvas['width']}:{canvas['height']}:"
            "force_original_aspect_ratio=increase,"
            f"crop={canvas['width']}:{canvas['height']},fps={fps},"
            f"trim=start_frame=0:end_frame={segment['frames']},settb=expr=1/{fps},"
            f"setpts=N/({fps}*TB),setsar=1,format=yuv420p[v{index}]"
        )
        video_labels.append(f"[v{index}]")
    next_input = len(plan["segments"])
    if plan["endcard"]:
        endcard = plan["endcard"]
        end_label = "endbg"
        filters.append(
            f"color=c={_color(endcard['background'])}:s={canvas['width']}x{canvas['height']}:"
            f"r={fps}:d={_seconds(endcard['frames'], fps)},"
            f"trim=start_frame=0:end_frame={endcard['frames']}[{end_label}]"
        )
        if endcard["logo"]:
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-i", str(endcard["logo"]["path"])])
            x = "(W-w)/2" if endcard["logo"]["x"] == "center" else endcard["logo"]["x"]
            filters.append(
                f"[{next_input}:v:0]scale={endcard['logo']['width']}:-1,"
                f"trim=start_frame=0:end_frame={endcard['frames']}[endlogo]"
            )
            filters.append(
                f"[endbg][endlogo]overlay=x={x}:y={endcard['logo']['y']}:eof_action=pass[endvisual]"
            )
            end_label = "endvisual"
            next_input += 1
        filters.append(
            f"[{end_label}]trim=start_frame=0:end_frame={endcard['frames']},"
            f"settb=expr=1/{fps},setpts=N/({fps}*TB),format=yuv420p[vend]"
        )
        video_labels.append("[vend]")
    filters.append(
        "".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0,"
        f"trim=start_frame=0:end_frame={total_frames},settb=expr=1/{fps},"
        f"setpts=N/({fps}*TB),format=yuv420p[picture]"
    )
    inputs.extend(["-i", str(plan["narration_path"])])
    narration_index = next_input
    duration = _seconds(total_frames, fps)
    filters.append(
        f"[{narration_index}:a:0]apad=whole_dur={duration},atrim=start=0:end={duration},"
        "asetpts=PTS-STARTPTS[narration]"
    )
    next_input += 1
    audio_labels = ["[narration]"]
    for offset, sfx in enumerate(plan["sfx"]):
        inputs.extend(["-i", str(sfx["path"])])
        label = f"sfx{offset}"
        delay_ms = sfx["timeline_start_frame"] * 1000 // fps
        fade = ""
        if sfx["fade_seconds"]:
            fade_out = Decimal(sfx["frames"]) / fps - sfx["fade_seconds"]
            fade = (
                f",afade=t=in:st=0:d={sfx['fade_seconds']}"
                f",afade=t=out:st={float(fade_out):.9f}:d={sfx['fade_seconds']}"
            )
        filters.append(
            f"[{next_input}:a:0]atrim=start={_seconds(sfx['in_frame'], fps)}:"
            f"end={_seconds(sfx['in_frame'] + sfx['frames'], fps)},asetpts=PTS-STARTPTS"
            f"{fade},volume={sfx['gain_db']}dB,adelay={delay_ms}:all=1,apad=whole_dur={duration},"
            f"atrim=start=0:end={duration}[{label}]"
        )
        audio_labels.append(f"[{label}]")
        next_input += 1
    if len(audio_labels) == 1:
        filters.append("[narration]anull[audio]")
    else:
        filters.append(
            "".join(audio_labels) + f"amix=inputs={len(audio_labels)}:normalize=0:duration=first[audio]"
        )

    with tempfile.TemporaryDirectory(prefix="narrated-text-") as text_directory:
        caption_filters: list[str] = []
        text_root = Path(text_directory)
        if plan["captions"]:
            caption = plan["captions"]
            for index, event in enumerate(caption["events"]):
                wrapped, size = _wrap(event["text"], caption["font_path"], caption["style"])
                text_file = text_root / f"caption-{index:04d}.txt"
                text_file.write_text(wrapped, encoding="utf-8")
                enable = f"gte(n\\,{event['start_frame']})*lt(n\\,{event['end_frame']})"
                caption_filters.append(_drawtext(text_file, caption["font_path"], caption["style"], size, enable=enable))
        if plan["endcard"] and plan["endcard"]["text"] is not None:
            endcard = plan["endcard"]
            style = {**endcard["text_style"], "min_font_size": endcard["text_style"]["font_size"], "max_font_size": endcard["text_style"]["font_size"], "x": "center", "bottom_margin": None, "line_spacing": 0, "max_lines": 1}
            wrapped, size = _wrap(endcard["text"], endcard["font_path"], style)
            text_file = text_root / "endcard.txt"
            text_file.write_text(wrapped, encoding="utf-8")
            start = total_frames - endcard["frames"]
            enable = f"gte(n\\,{start})*lt(n\\,{total_frames})"
            caption_filters.append(_drawtext(text_file, endcard["font_path"], style, size, enable=enable))
        if caption_filters:
            filters.append("[picture]" + ",".join(caption_filters) + "[video]")
        else:
            filters.append("[picture]null[video]")

        handle = tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}-", suffix=output.suffix, dir=output.parent, delete=False,
        )
        temporary = Path(handle.name)
        handle.close()
        temporary.unlink()
        try:
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-n", *inputs,
                "-filter_complex", ";".join(filters), "-map", "[video]", "-map", "[audio]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-r", str(fps), "-fps_mode", "cfr", "-frames:v", str(total_frames),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                "-metadata", f"comment=narrated-plan:{plan['plan_sha256']}", str(temporary),
            ], check=True)
            facts = _validate_output(temporary, plan)
            receipt = _assembled_receipt(plan, sha256(temporary), facts, ffmpeg)
            recovery_path = _write_recovery_receipt(plan["root"], output, receipt_path, receipt)
            try:
                _publish_exclusive(temporary, output)
            except BaseException:
                recovery_path.unlink(missing_ok=True)
                raise
        except subprocess.CalledProcessError as error:
            raise NarratedAssemblyError(f"ffmpeg assembly failed with exit code {error.returncode}") from error
        finally:
            temporary.unlink(missing_ok=True)
    try:
        _exclusive_json(receipt_path, receipt)
    except BaseException as error:
        raise NarratedAssemblyError(
            "receipt finalization failed; verified output was preserved; recovery receipt: "
            f"{recovery_path}"
        ) from error
    recovery_path.unlink(missing_ok=True)
    return receipt


def _summary(plan: Mapping[str, Any], status: str) -> dict[str, Any]:
    return {
        "status": status, "schema_version": plan["schema_version"],
        "project_root": str(plan["root"]),
        "plan": {"path": _relative(plan["root"], plan["plan_path"]), "sha256": plan["plan_sha256"]},
        "canvas": plan["canvas"], "picture_frames": plan["total_frames"],
        "picture_duration_seconds": plan["duration_seconds"],
        "narration_duration_seconds": plan["narration_duration_seconds"],
        "silence_padding_seconds": plan["silence_padding_seconds"],
        "segments": [{
            "id": item["id"], "timeline_start_frame": item["timeline_start_frame"],
            "timeline_end_frame": item["timeline_end_frame"], "source_in_frame": item["in_frame"],
        } for item in plan["segments"]],
        "caption_events": len(plan["captions"]["events"]) if plan["captions"] else 0,
        "endcard_frames": plan["endcard"]["frames"] if plan["endcard"] else 0,
        "endcard_text": plan["endcard"]["text"] if plan["endcard"] else None,
        "sfx_events": len(plan["sfx"]), "sources": plan["sources"],
        "output": _relative(plan["root"], plan["output_path"]),
        "receipt": _relative(plan["root"], plan["receipt_path"]),
        "ffmpeg": plan["tools"][0],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assemble_narrated.py",
        description="Check and assemble a voiceover film from a JSON edit plan.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "plan": "print the normalized timeline and exact input bindings without writing",
        "check": "run the complete no-write assembly preflight",
        "assemble": "render the configured output and exclusive JSON receipt",
        "recover": "finalize a saved pending receipt without rerendering",
    }
    for name, description in descriptions.items():
        command = commands.add_parser(name, help=description, description=description)
        command.add_argument("--project-root", required=True, type=Path, help="explicit project directory")
        if name == "recover":
            command.add_argument("--recovery-receipt", required=True, type=Path, help="saved pending receipt inside the project root")
        else:
            command.add_argument("--edit-plan", required=True, type=Path, help="JSON plan inside the project root")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "recover":
            payload = recover_receipt(args.project_root, args.recovery_receipt)
        else:
            plan = load_plan(args.project_root, args.edit_plan)
            if args.command == "assemble":
                payload = assemble(plan)
            else:
                payload = _summary(plan, "PLAN_OK" if args.command == "plan" else "CHECK_OK")
        print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (NarratedAssemblyError, OSError) as error:
        print(f"NARRATED ASSEMBLY FAILED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
