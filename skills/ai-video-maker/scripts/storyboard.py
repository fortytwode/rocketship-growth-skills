#!/usr/bin/env python3
"""Build the storyboard page for an AI video project.

    python storyboard.py <project_dir> [--source storyboard.yaml] [--out STORYBOARD.html] [--pdf]

Reads a storyboard source (YAML, documented in the skill's
``references/storyboard-format.md``), validates it, and writes one self-contained
HTML page with every image embedded as a compressed thumbnail, plus a plain-text
companion beside it. Both production routes use the same page: ``dialogue``
(on-screen speech, with voiceover, captions, app audio and text threads as
needed) and ``narrated`` (one voice over the pictures). The board shows visual
intent for human approval; building it approves nothing.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps, UnidentifiedImageError

FORMATS = ("dialogue", "narrated")
# Line type -> legend label. Order is the legend order.
LINE_TYPES = {
    "vo": "Voiceover",
    "dialogue": "On-screen dialogue",
    "caption": "Caption overlay",
    "app": "App / diegetic audio",
    "text": "Text thread",
}
# Types whose label is the speaker, so a speaker is required.
SPEAKER_REQUIRED = {"dialogue", "text"}
# A narrated film has one voice over the pictures; nobody speaks on screen.
NOT_IN_FORMAT = {"narrated": {"dialogue"}}
FRAMES = ("black", "white")

TOP_KEYS = {"title", "client", "format", "script_version", "board_version", "summary",
            "cast", "props", "settings", "panels", "continuity", "notes"}
REQUIRED_TOP = ("title", "format", "script_version", "panels")
PANEL_KEYS = {"id", "scene", "image", "frame", "action", "lines"}
LINE_KEYS = {"type", "speaker", "text"}
ITEM_KEYS = {"name", "image", "note"}
CONTINUITY_KEYS = {"cut", "where", "in_hand", "state"}
REFERENCE_KINDS = {"cast": "Cast", "props": "Prop", "settings": "Setting"}
CONTINUITY_HEADINGS = (("cut", "Cut"), ("where", "Where everyone is"),
                       ("in_hand", "In hand"), ("state", "Doors / state"))

# Thumbnail settings tried in order until the page fits the size limit.
# (longest edge in pixels, JPEG quality). Images are never enlarged.
THUMBNAIL_STEPS = ((1280, 82), (1080, 78), (900, 74), (720, 70), (560, 65), (440, 60), (340, 55))
DEFAULT_MAX_MB = 15.0


class StoryboardError(Exception):
    """A problem the operator must fix; each entry in ``problems`` says what and where."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or [message]


# --------------------------------------------------------------------------- source

def _scalar(value: Any) -> str | None:
    """A YAML scalar as text (YAML reads `2` and `v2` differently); None when absent or empty."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _image_path(project: Path, raw: Any, where: str, errors: list[str]) -> Path | None:
    text = _scalar(raw)
    if text is None:
        errors.append(f"{where}: image must be a file path inside the project")
        return None
    candidate = Path(text)
    path = (candidate if candidate.is_absolute() else project / candidate).resolve()
    try:
        path.relative_to(project)
    except ValueError:
        errors.append(f"{where}: image {text!r} is outside the project folder; copy it into the project")
        return None
    if not path.is_file():
        errors.append(f"{where}: image not found: {text}")
        return None
    try:
        with Image.open(path) as probe:
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        errors.append(f"{where}: {text} is not a readable image")
        return None
    return path


def _unknown(value: dict, allowed: set[str], where: str, errors: list[str]) -> None:
    extra = sorted(str(key) for key in value if key not in allowed)
    if extra:
        errors.append(f"{where}: unknown keys {extra}; allowed: {sorted(allowed)}")


def _items(project: Path, raw: Any, key: str, errors: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"{key} must be a list of entries with name, image and note")
        return []
    items = []
    for index, entry in enumerate(raw):
        where = f"{key}[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be a mapping with name, image and note")
            continue
        _unknown(entry, ITEM_KEYS, where, errors)
        name = _scalar(entry.get("name"))
        if name is None:
            errors.append(f"{where}: name is required")
        image = None
        if entry.get("image") is not None:
            image = _image_path(project, entry.get("image"), f"{where} ({name or 'unnamed'})", errors)
        note = _scalar(entry.get("note"))
        if image is None and note is None and entry.get("image") is None:
            errors.append(f"{where} ({name or 'unnamed'}): give an image, a note or both")
        items.append({"name": name or "", "image": image, "note": note or ""})
    return items


def load_source(project: Path, source: Path) -> dict[str, Any]:
    """Parse and validate a storyboard source. Raises with every problem found, not just the first."""
    if not source.is_file():
        raise StoryboardError(f"storyboard source not found: {source}")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise StoryboardError(f"{source.name} is not valid YAML: {error}") from error
    except UnicodeDecodeError as error:
        raise StoryboardError(f"{source.name} is not UTF-8 text") from error
    if not isinstance(raw, dict):
        raise StoryboardError(f"{source.name} must be a mapping with title, format, script_version and panels")

    errors: list[str] = []
    _unknown(raw, TOP_KEYS, source.name, errors)
    for key in REQUIRED_TOP:
        if raw.get(key) is None:
            errors.append(f"{key} is required")
    board: dict[str, Any] = {}
    for key in ("title", "client", "script_version", "board_version", "summary"):
        value = raw.get(key)
        board[key] = _scalar(value) or ""
        if value is not None and _scalar(value) is None:
            errors.append(f"{key} must be text")
    fmt = raw.get("format")
    if fmt is not None and fmt not in FORMATS:
        errors.append(f"format must be one of {list(FORMATS)}, not {fmt!r}")
    board["format"] = fmt if fmt in FORMATS else ""
    excluded = NOT_IN_FORMAT.get(board["format"], set())

    panels_raw = raw.get("panels")
    panels: list[dict] = []
    if panels_raw is not None and (not isinstance(panels_raw, list) or not panels_raw):
        errors.append("panels must list at least one panel, in film order")
        panels_raw = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(panels_raw or []):
        where = f"panels[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} must be a mapping")
            continue
        _unknown(entry, PANEL_KEYS, where, errors)
        panel_id = _scalar(entry.get("id"))
        if panel_id is None:
            errors.append(f"{where}: id is required (quote it to keep leading zeros, e.g. id: \"01\")")
            panel_id = str(index + 1)
        elif panel_id in seen_ids:
            errors.append(f"{where}: id {panel_id!r} is used by an earlier panel; ids must be unique")
        seen_ids.add(panel_id)
        where = f"panel {panel_id}"
        image = None
        frame = entry.get("frame")
        if entry.get("image") is not None and frame is not None:
            errors.append(f"{where}: give image or frame, not both")
        elif entry.get("image") is not None:
            image = _image_path(project, entry.get("image"), where, errors)
        elif frame is not None:
            if frame not in FRAMES:
                errors.append(f"{where}: frame must be one of {list(FRAMES)} (a deliberate blank)")
        else:
            errors.append(f"{where}: image is required (or frame: black for a deliberate blank)")
        action = _scalar(entry.get("action")) or ""
        lines = []
        lines_raw = entry.get("lines")
        if lines_raw is not None and not isinstance(lines_raw, list):
            errors.append(f"{where}: lines must be a list")
            lines_raw = []
        for line_index, line in enumerate(lines_raw or []):
            line_where = f"{where} line {line_index + 1}"
            if not isinstance(line, dict):
                errors.append(f"{line_where} must be a mapping with type, speaker and text")
                continue
            _unknown(line, LINE_KEYS, line_where, errors)
            kind = line.get("type")
            if kind not in LINE_TYPES:
                errors.append(f"{line_where}: unknown line type {kind!r}; use one of {list(LINE_TYPES)}")
                continue
            if kind in excluded:
                errors.append(f"{line_where}: a {board['format']} board has no {LINE_TYPES[kind].lower()} lines; "
                              f"use format: dialogue for a film where characters speak on screen")
            speaker = _scalar(line.get("speaker")) or ""
            if kind in SPEAKER_REQUIRED and not speaker:
                errors.append(f"{line_where}: a {kind} line needs a speaker")
            text = _scalar(line.get("text"))
            if text is None:
                errors.append(f"{line_where}: text is required")
            lines.append({"type": kind, "speaker": speaker, "text": text or ""})
        if not action and not lines:
            errors.append(f"{where} is empty: give it action text, lines or both")
        panels.append({"id": panel_id, "scene": _scalar(entry.get("scene")) or "", "image": image,
                       "frame": frame if frame in FRAMES else None, "action": action, "lines": lines})
    board["panels"] = panels

    for key in ("cast", "props", "settings"):
        board[key] = _items(project, raw.get(key), key, errors)

    continuity = []
    rows = raw.get("continuity")
    if rows is not None and not isinstance(rows, list):
        errors.append("continuity must be a list of rows with cut, where, in_hand and state")
        rows = []
    for index, row in enumerate(rows or []):
        where = f"continuity[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{where} must be a mapping with cut, where, in_hand and state")
            continue
        _unknown(row, CONTINUITY_KEYS, where, errors)
        values = {key: _scalar(row.get(key)) or "" for key in CONTINUITY_KEYS}
        if not values["cut"]:
            errors.append(f"{where}: cut is required (the boundary, e.g. B01 -> B02)")
        if not any(values[key] for key in ("where", "in_hand", "state")):
            errors.append(f"{where}: give at least one of where, in_hand, state")
        continuity.append(values)
    board["continuity"] = continuity

    notes = raw.get("notes")
    if notes is None:
        board["notes"] = []
    elif isinstance(notes, list) and all(_scalar(note) for note in notes):
        board["notes"] = [_scalar(note) for note in notes]
    elif _scalar(notes):
        board["notes"] = [_scalar(notes)]
    else:
        errors.append("notes must be text or a list of text")
        board["notes"] = []

    if errors:
        raise StoryboardError(f"{source.name} has {len(errors)} problem(s); fix them and build again", errors)
    board["_source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    board["_source_name"] = source.name
    return board


# --------------------------------------------------------------------------- images

def thumbnail(path: Path, longest_edge: int, quality: int) -> bytes:
    """A JPEG no larger than ``longest_edge`` on its long side; orientation applied, metadata dropped."""
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    else:
        image = image.convert("RGB")
    image.thumbnail((longest_edge, longest_edge), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _images(board: dict) -> list[Path]:
    found: list[Path] = []
    for panel in board["panels"]:
        if panel["image"] is not None:
            found.append(panel["image"])
    for key in ("cast", "props", "settings"):
        found.extend(item["image"] for item in board[key] if item["image"] is not None)
    return list(dict.fromkeys(found))


# --------------------------------------------------------------------------- rendering

def line_label(line: dict) -> str:
    speaker = line["speaker"]
    kind = line["type"]
    if kind == "vo":
        return f"{speaker} (V.O.)" if speaker else "V.O."
    if kind == "caption":
        return f"Caption · {speaker}" if speaker else "Caption"
    if kind == "app":
        return speaker or "App"
    if kind == "text":
        return f"{speaker} (text)"
    return speaker


def _used_types(board: dict) -> list[str]:
    used = {line["type"] for panel in board["panels"] for line in panel["lines"]}
    return [kind for kind in LINE_TYPES if kind in used]


def _meta(board: dict) -> list[str]:
    parts = []
    if board["client"]:
        parts.append(board["client"])
    parts.append("Dialogue drama" if board["format"] == "dialogue" else "Narrated (voiceover only)")
    parts.append(f"Script {board['script_version']}")
    if board["board_version"]:
        parts.append(f"Board {board['board_version']}")
    parts.append(f"{len(board['panels'])} panels")
    return parts


CSS = """
:root{--bg:#f6f5f2;--surface:#fff;--ink:#1d1d1f;--muted:#5b5b60;--rule:#dcdad5;--frame:#000;
--vo:#8a5a00;--dialogue:#0b5f99;--caption:#b0234a;--app:#2f6b12;--text:#5a3db5;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#121214;--surface:#1c1c1f;--ink:#ececee;
--muted:#a3a3a8;--rule:#333338;--vo:#f5c26b;--dialogue:#8fd3ff;--caption:#ff8fa3;--app:#b6f09c;--text:#c9b6ff;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#121214;--surface:#1c1c1f;--ink:#ececee;--muted:#a3a3a8;--rule:#333338;
--vo:#f5c26b;--dialogue:#8fd3ff;--caption:#ff8fa3;--app:#b6f09c;--text:#c9b6ff;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1400px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:26px;line-height:1.2;margin:0 0 6px}
h2{font-size:18px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--rule)}
.meta{color:var(--muted);margin:0 0 10px}.meta span+span:before{content:" · "}
.summary{max-width:75ch;margin:0 0 14px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:0;padding:10px 12px;background:var(--surface);
border:1px solid var(--rule);border-radius:8px;font-size:13px}
.legend b{font-weight:600;margin-right:4px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;background:currentColor}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(230px,100%),1fr));gap:18px}
.panel{margin:0;background:var(--surface);border:1px solid var(--rule);border-radius:8px;overflow:hidden;
break-inside:avoid;page-break-inside:avoid}
.panel img,.blank{display:block;width:100%;height:auto}
.blank{aspect-ratio:9/16;background:#000}.blank.white{background:#fff;border-bottom:1px solid var(--rule)}
.panel figcaption{padding:10px 12px 12px}
.head{display:flex;gap:8px;align-items:baseline;margin-bottom:4px}
.id{font-weight:700;font-variant-numeric:tabular-nums}.scene{color:var(--muted);font-size:13px}
.action{margin:0}
.line{border-left:3px solid currentColor;padding:2px 0 2px 9px;margin-top:8px}
.line .who{display:block;font-size:11.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.line .say{display:block;color:var(--ink);white-space:pre-line}
.vo{color:var(--vo)}.dialogue{color:var(--dialogue)}.caption{color:var(--caption)}.app{color:var(--app)}.text{color:var(--text)}
.refs{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(280px,100%),1fr));gap:18px}
.ref{margin:0;background:var(--surface);border:1px solid var(--rule);border-radius:8px;overflow:hidden;break-inside:avoid}
.ref img{display:block;width:100%;height:auto}.ref figcaption{padding:8px 12px 10px}
.ref .kind{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.ref .note{display:block;color:var(--muted);white-space:pre-line}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--surface);font-size:14px}
th,td{border:1px solid var(--rule);padding:6px 10px;text-align:left;vertical-align:top}
th{font-weight:600;background:var(--bg)}td:first-child{white-space:nowrap;font-variant-numeric:tabular-nums}
.notes{padding-left:20px;max-width:80ch}
footer{margin-top:40px;color:var(--muted);font-size:12px}
@media print{:root{--bg:#fff;--surface:#fff;--ink:#000;--muted:#444;--rule:#bbb;--vo:#8a5a00;--dialogue:#0b5f99;
--caption:#b0234a;--app:#2f6b12;--text:#5a3db5}body{font-size:11pt}main{max-width:none;padding:0}
.grid{grid-template-columns:repeat(4,1fr);gap:10px}h2{break-after:avoid}}
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _img(data: bytes, alt: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f'<img src="data:image/jpeg;base64,{encoded}" alt="{_esc(alt)}" loading="lazy">'


def render_html(board: dict, encoded: dict[Path, bytes]) -> str:
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f"<title>{_esc(board['title'])} storyboard</title>", f"<style>{CSS}</style></head><body><main>",
           f"<h1>{_esc(board['title'])}</h1>",
           '<p class="meta">' + "".join(f"<span>{_esc(part)}</span>" for part in _meta(board)) + "</p>"]
    if board["summary"]:
        out.append(f'<p class="summary">{_esc(board["summary"])}</p>')
    used = _used_types(board)
    if used:
        out.append('<p class="legend"><b>Lines</b>' + "".join(
            f'<span><i class="{kind}"></i>{_esc(LINE_TYPES[kind])}</span>'
            for kind in used) + "</p>")

    out.append('<h2>Panels</h2><div class="grid">')
    for panel in board["panels"]:
        alt = f"Panel {panel['id']}" + (f": {panel['action']}" if panel["action"] else "")
        if panel["image"] is not None:
            picture = _img(encoded[panel["image"]], alt)
        else:
            picture = f'<div class="blank {panel["frame"]}" role="img" aria-label="{_esc(alt)}"></div>'
        head = f'<span class="id">{_esc(panel["id"])}</span>'
        if panel["scene"]:
            head += f'<span class="scene">Sc {_esc(panel["scene"])}</span>'
        body = [f'<div class="head">{head}</div>']
        if panel["action"]:
            body.append(f'<p class="action">{_esc(panel["action"])}</p>')
        for line in panel["lines"]:
            body.append(f'<div class="line {line["type"]}"><span class="who">{_esc(line_label(line))}</span>'
                        f'<span class="say">{_esc(line["text"])}</span></div>')
        out.append(f'<figure class="panel">{picture}<figcaption>{"".join(body)}</figcaption></figure>')
    out.append("</div>")

    sections = [(key, label) for key, label in (("cast", "Cast"), ("props", "Props"), ("settings", "Settings"))
                if board[key]]
    if sections:
        out.append("<h2>" + _esc(", ".join(label for _, label in sections)) + '</h2><div class="refs">')
        for key, label in sections:
            for item in board[key]:
                picture = _img(encoded[item["image"]], f"{label}: {item['name']}") if item["image"] else ""
                note = f'<span class="note">{_esc(item["note"])}</span>' if item["note"] else ""
                out.append(f'<figure class="ref">{picture}<figcaption><span class="kind">{REFERENCE_KINDS[key]}'
                           f'</span><br><b>{_esc(item["name"])}</b>{note}</figcaption></figure>')
        out.append("</div>")

    if board["continuity"]:
        out.append('<h2>Continuity at each cut</h2><div class="tablewrap"><table><thead><tr>'
                   + "".join(f"<th>{_esc(title)}</th>" for _, title in CONTINUITY_HEADINGS)
                   + "</tr></thead><tbody>")
        for row in board["continuity"]:
            out.append("<tr>" + "".join(f"<td>{_esc(row[key]) or '-'}</td>" for key, _ in CONTINUITY_HEADINGS)
                       + "</tr>")
        out.append("</tbody></table></div>")

    if board["notes"]:
        out.append('<h2>Notes</h2><ul class="notes">' + "".join(f"<li>{_esc(n)}</li>" for n in board["notes"])
                   + "</ul>")
    out.append(f"<footer>Built with storyboard.py from {_esc(board['_source_name'])} "
               f"(sha256 {board['_source_sha256'][:16]}). The board shows visual intent for approval; "
               "building it approves nothing.</footer></main></body></html>\n")
    return "\n".join(out)


def render_text(board: dict, project: Path) -> str:
    def rel(path: Path) -> str:
        return path.relative_to(project).as_posix()

    out = [board["title"], " | ".join(_meta(board))]
    if board["summary"]:
        out += ["", board["summary"]]
    used = _used_types(board)
    if used:
        out += ["", "Lines: " + "; ".join(f"{kind} = {LINE_TYPES[kind]}" for kind in used)]
    out += ["", "PANELS"]
    for panel in board["panels"]:
        head = f"[{panel['id']}]"
        if panel["scene"]:
            head += f" Sc {panel['scene']}"
        picture = rel(panel["image"]) if panel["image"] else f"({panel['frame']} frame)"
        out += ["", f"{head}  {picture}"]
        if panel["action"]:
            out.append(f"  {panel['action']}")
        for line in panel["lines"]:
            text = line["text"].replace("\n", "\n      ")
            out.append(f"  {line['type'].upper():<8} {line_label(line)}: {text}")
    for key, label in (("cast", "CAST"), ("props", "PROPS"), ("settings", "SETTINGS")):
        if board[key]:
            out += ["", label]
            for item in board[key]:
                parts = [item["name"]]
                if item["note"]:
                    parts.append(item["note"])
                if item["image"]:
                    parts.append(rel(item["image"]))
                out.append("- " + " | ".join(parts))
    if board["continuity"]:
        out += ["", "CONTINUITY AT EACH CUT", " | ".join(title for _, title in CONTINUITY_HEADINGS)]
        for row in board["continuity"]:
            out.append(" | ".join(row[key] or "-" for key, _ in CONTINUITY_HEADINGS))
    if board["notes"]:
        out += ["", "NOTES"] + [f"- {note}" for note in board["notes"]]
    out += ["", f"Source: {board['_source_name']} sha256 {board['_source_sha256']}"]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- build

def _write(path: Path, data: bytes) -> None:
    """Replace ``path`` whole, with ordinary permissions, so a reader never sees half a page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    try:
        with open(temporary, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def build(project: Path, source: Path, out: Path, max_bytes: int) -> dict[str, Any]:
    board = load_source(project, source)
    images = _images(board)
    page = None
    chosen = None
    for longest_edge, quality in THUMBNAIL_STEPS:
        encoded = {path: thumbnail(path, longest_edge, quality) for path in images}
        page = render_html(board, encoded).encode("utf-8")
        chosen = (longest_edge, quality)
        if len(page) <= max_bytes:
            break
    else:
        raise StoryboardError(
            f"the page is {len(page) / 1e6:.1f} MB even with {THUMBNAIL_STEPS[-1][0]} px thumbnails, over the "
            f"{max_bytes / 1e6:.1f} MB limit; split the board or drop images that repeat")
    text_path = out.with_suffix(".txt")
    try:
        _write(out, page)
        _write(text_path, render_text(board, project).encode("utf-8"))
    except PermissionError as error:
        raise StoryboardError(f"{out.name} or {text_path.name} is open in another program; close it and build again") from error
    return {"status": "BUILT", "html": str(out), "text": str(text_path), "bytes": len(page),
            "sha256": hashlib.sha256(page).hexdigest(), "format": board["format"],
            "panels": len(board["panels"]), "images": len(images),
            "thumbnail": {"longest_edge": chosen[0], "jpeg_quality": chosen[1]},
            "source": str(source), "source_sha256": board["_source_sha256"]}


# --------------------------------------------------------------------------- pdf

_BROWSER_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome",
                  "msedge", "microsoft-edge")
_BROWSER_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_browser() -> str | None:
    """A Chromium-family browser that can print headless, or None."""
    override = os.environ.get("STORYBOARD_BROWSER")
    if override:
        return override if Path(override).is_file() else None
    for name in _BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in _BROWSER_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


PDF_TIMEOUT_S = 180.0


def print_pdf(html_path: Path, browser: str, timeout: float = PDF_TIMEOUT_S) -> Path:
    """Print the page with a headless browser.

    Some browser builds keep running after the PDF is written, so completion is
    judged by the file itself: once it is a complete PDF, the browser is closed.
    """
    pdf = html_path.with_suffix(".pdf")
    started = time.monotonic()
    if pdf.exists():
        pdf.unlink()
    with tempfile.TemporaryDirectory(prefix="storyboard-") as profile:
        log_path = Path(profile) / "browser.log"
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(
                [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                 f"--user-data-dir={Path(profile) / 'profile'}", f"--print-to-pdf={pdf}",
                 html_path.resolve().as_uri()],
                stdout=subprocess.DEVNULL, stderr=log,
            )
            try:
                while process.poll() is None and not _complete_pdf(pdf):
                    if time.monotonic() - started > timeout:
                        raise StoryboardError(f"the browser did not print the PDF within {timeout:.0f} s")
                    time.sleep(0.25)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
        error_text = log_path.read_text(encoding="utf-8", errors="replace")
    if not _complete_pdf(pdf):
        raise StoryboardError(f"the browser could not print the PDF: {error_text.strip()[-400:]}")
    return pdf


def _complete_pdf(path: Path) -> bool:
    """A PDF file that has been written to its end-of-file marker."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(5)
            stream.seek(max(0, path.stat().st_size - 1024))
            tail = stream.read()
    except OSError:
        return False
    return head == b"%PDF-" and b"%%EOF" in tail


# --------------------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("project")
    parser.add_argument("--source", default="storyboard.yaml", help="storyboard source, relative to the project")
    parser.add_argument("--out", default="STORYBOARD.html", help="output page, relative to the project; "
                        "the plain-text companion is written beside it with a .txt suffix")
    parser.add_argument("--pdf", action="store_true", help="also print a PDF with a headless Chromium-family browser")
    parser.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="size limit for the page")
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    if not project.is_dir():
        print(json.dumps({"error": f"project folder not found: {project}"}), file=sys.stderr)
        return 1
    source = Path(args.source)
    source = (source if source.is_absolute() else project / source).resolve()
    out = Path(args.out)
    out = (out if out.is_absolute() else project / out).resolve()
    if out.suffix.lower() not in (".html", ".htm"):
        print(json.dumps({"error": "--out must name an .html file"}), file=sys.stderr)
        return 1
    try:
        result = build(project, source, out, int(args.max_mb * 1_000_000))
    except StoryboardError as error:
        report: dict[str, Any] = {"error": str(error)}
        if error.problems != [str(error)]:
            report["problems"] = error.problems
        print(json.dumps(report, indent=1), file=sys.stderr)
        return 1
    result["pdf"] = None
    warnings = []
    if args.pdf:
        browser = find_browser()
        if browser is None:
            warnings.append("no headless Chrome, Chromium or Edge found; open the HTML in a browser and "
                            "print to PDF (the page has print styles)")
        else:
            try:
                result["pdf"] = str(print_pdf(out, browser))
            except (StoryboardError, OSError) as error:
                warnings.append(f"PDF not made ({error}); open the HTML in a browser and print to PDF")
    result["warnings"] = warnings
    print(json.dumps(result, indent=1))
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
