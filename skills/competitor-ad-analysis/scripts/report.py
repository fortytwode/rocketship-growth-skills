#!/usr/bin/env python3
"""Render a local, self-contained competitor ad analysis report."""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import os
from collections import Counter
from pathlib import Path
from urllib.parse import quote

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS
FIELDS = ("summary", "hook", "mechanic", "setting", "emotion", "emotion_evidence", "product_role", "payoff", "cta", "spoken_words", "overlay_text")


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def cell(value) -> str:
    """Neutralize spreadsheet formulas after converting to a displayable cell."""
    value = str(value if value is not None else "")
    if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def local_link(raw, base: Path, out: Path) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    p = Path(raw)
    if p.is_absolute() or ".." in p.parts:
        return None
    try:
        resolved = (base / p).resolve(strict=True)
        resolved.relative_to(base.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file() or resolved.suffix.lower() not in MEDIA_EXTS:
        return None
    rel = Path(os.path.relpath(resolved, out.resolve()))
    return quote(rel.as_posix(), safe="/.-_")


def copy_local_assets(data, base: Path, out: Path):
    """Copy only safe referenced assets into a separate report directory."""
    base, out = base.resolve(), out.resolve()
    if base == out:
        return
    if data.get("top_sample"):
        copy_local_assets(data["top_sample"], base, out)
    for ad in data.get("ads") or []:
        refs = [ad.get("media_file")] + [frame.get("path") for frame in (ad.get("frames") or [])]
        for raw in refs:
            link = local_link(raw, base, out)
            if not link:
                continue
            rel = Path(raw)
            target = out / rel
            # Do not follow existing symlinked directories or files out of the report tree.
            current = out
            for part in rel.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError(f"Refusing symlink in report asset path: {rel}")
            try:
                target.resolve().relative_to(out)
            except ValueError as exc:
                raise ValueError(f"Report asset path escapes output directory: {rel}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            source = (base / rel).resolve(strict=True)
            shutil.copy2(source, target)


def source_link(raw) -> str | None:
    if not isinstance(raw, str) or not re.match(r"^https?://", raw, re.I):
        return None
    return raw


def s(value) -> str:
    return str(value if value is not None else "")


def render_matrix(ads, field, other_field):
    rows = sorted({s((a.get("analysis") or {}).get(field)) or "Unclassified" for a in ads if a.get("status") == "reviewed"})
    cols = sorted({s((a.get("analysis") or {}).get(other_field)) or "Unclassified" for a in ads if a.get("status") == "reviewed"})
    counts = Counter((s((a.get("analysis") or {}).get(field)) or "Unclassified", s((a.get("analysis") or {}).get(other_field)) or "Unclassified") for a in ads if a.get("status") == "reviewed")
    return rows, cols, counts


def write_matrix(path, ads, field, other_field):
    rows, cols, counts = render_matrix(ads, field, other_field)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([field.title() + " / " + other_field.title(), *[cell(c) for c in cols]])
        for r in rows:
            w.writerow([cell(r), *[counts[(r, c)] for c in cols]])


def thumbnail_card(ad, base, out, rank_label="", representative=False):
    """Show every member, including a placeholder when no safe thumbnail exists."""
    media = local_link(ad.get("media_file"), base, out)
    thumbnail = media if Path(s(ad.get("media_file"))).suffix.lower() in IMAGE_EXTS else None
    if not thumbnail:
        for frame in ad.get("frames") or []:
            if Path(s(frame.get("path"))).suffix.lower() in IMAGE_EXTS:
                thumbnail = local_link(frame.get("path"), base, out)
                if thumbnail:
                    break
    src = source_link(ad.get("drive_url")) or source_link(ad.get("source_url")) or media
    visual = f'<img loading="lazy" src="{esc(thumbnail)}" alt="Ad {esc(ad.get("ad_id"))} thumbnail">' if thumbnail else '<div class="missing-thumbnail">Thumbnail unavailable</div>'
    if src:
        visual = f'<a href="{esc(src)}" rel="noopener noreferrer">{visual}</a>'
    analysis = ad.get("analysis") or {}
    hook = s(analysis.get("card_title") or analysis.get("hook") or analysis.get("summary") or ad.get("ad_copy") or "Hook unavailable")
    if len(hook) > 120:
        hook = hook[:117] + "…"
    badge = '<span class="badge">Representative</span>' if representative else ""
    label = f'<p class="rank-label">{esc(rank_label)}</p>' if rank_label else ""
    return f'<article class="member" data-ad-id="{esc(ad.get("ad_id"))}">{label}{visual}{badge}<h4>{esc(hook)}</h4><p class="muted">Ad {esc(ad.get("ad_id"))} · {esc(ad.get("launch_date") or "Date unavailable")}</p></article>'


def gallery_rows(cards):
    """Keep four members together in print while preserving the screen grid."""
    return "".join('<div class="gallery-row">' + "".join(cards[index:index + 4]) + '</div>' for index in range(0, len(cards), 4))


def family_gallery(data, base, out):
    ads = {a.get("ad_id"): a for a in data.get("ads") or []}
    sections = []
    for index, family in enumerate(data.get("families") or []):
        ids = family.get("ad_ids") or []
        supplied = family.get("representative_ad_ids") or []
        representatives = set(supplied) if all(aid in ids for aid in supplied) else set()
        cards = gallery_rows([thumbnail_card(ads[aid], base, out, representative=aid in representatives) for aid in ids if aid in ads])
        notes = "".join(f'<section><h4>{label}</h4>{list_html(family[key])}</section>' for key, label in (("shared_structure", "Shared structure"), ("exceptions", "Exceptions")) if family.get(key))
        notes = f'<div class="pair">{notes}</div>' if notes else ""
        sections.append(f'<section class="cluster" id="cluster-{index + 1}"><h2>{esc(family.get("name"))} · {len(ids)} ads</h2><p>{esc(family.get("definition"))}</p>{notes}<div class="member-gallery">{cards}</div></section>')
    return "".join(sections) or '<p>Creative family clustering is pending.</p>'


def top_gallery(data, base, out):
    if not data:
        return ""
    verified = data.get("cohort") == "top" and (data.get("coverage") or {}).get("ranking_verified") is True
    archived = data.get("cohort") == "top" and (data.get("capture") or {}).get("ranking_basis") == "archived_selection"
    title = "Top 10 · source-ranked gallery" if verified and len(data.get("ads") or []) == 10 else "Source-ranked gallery" if verified else "Separate sample · recorded order"
    if archived and not verified:
        title = "Archived top-ten selection" if len(data.get("ads") or []) == 10 else "Archived selection"
    note = "Ranks reflect the verified source capture, not a performance conclusion." if verified else "Ranking was not verified for this cohort; positions show recorded input order only."
    if archived and not verified:
        note = s((data.get("capture") or {}).get("ranking_note") or "Archived source selection; current source ranking has not been independently verified.")
    cards = []
    for ordinal, ad in enumerate(data.get("ads") or [], 1):
        rank = ad.get("source_rank")
        label = f"Source rank {rank}" if verified and rank is not None else f"Recorded order {ordinal}"
        if archived and not verified and rank is not None:
            label = f"Archived #{rank}"
        cards.append(thumbnail_card(ad, base, out, rank_label=label))
    cov = data.get("coverage") or {}
    metadata = f"Selected records {len(data.get('ads') or [])} · Reviewed records {cov.get('reviewed', 0)} · Cohort: {s(data.get('cohort'))} · Model: {s(data.get('model'))}"
    capture = data.get("capture") or {}
    for key in ("date_from", "date_to", "captured_at"):
        if capture.get(key):
            metadata += f" · {key.replace('_', ' ').title()}: {s(capture[key])}"
    return f'<section class="top-sample"><h2>{title}</h2><p>{esc(note)}</p><p class="muted">{esc(metadata)}</p><div class="member-gallery">{gallery_rows(cards)}</div></section>'


def render_html(data, base: Path, out: Path) -> str:
    cov = data.get("coverage") or {}
    capture = data.get("capture") or {}
    title = esc(data.get("title") or "Competitor ad analysis")
    ads = data.get("ads") or []
    families = data.get("families") or []
    family_names = [s(f.get("name")) for f in families]
    verified = data.get("cohort") == "top" and cov.get("ranking_verified") is True
    ranking_note = "Ranking verified in source capture." if verified else "Ranking was not verified; this report makes no rank or performance claims."
    fam_head = "".join(f"<th>{esc(n)}</th>" for n in family_names)
    matrix_rows = []
    for ad in ads:
        matrix_rows.append("<tr><th>" + esc(ad.get("ad_id")) + "</th>" + "".join(f"<td>{int(ad.get('ad_id') in (family.get('ad_ids') or []))}</td>" for family in families) + "</tr>")
    if not family_names:
        family_matrix = "<p>No creative families supplied.</p>"
    else:
        family_matrix = f'<div class="scroll"><table><thead><tr><th>Ad</th>{fam_head}</tr></thead><tbody>{"".join(matrix_rows)}</tbody></table></div>'
    family_cards = []
    for index, family in enumerate(families):
        count = len(family.get("ad_ids") or [])
        family_cards.append(f'<article class="family"><h3>{esc(family.get("name"))} · {count} ads</h3><p>{esc(family.get("definition"))}</p><a href="#cluster-{index + 1}">See all {count} ads</a></article>')
    ad_cards = []
    for ad in ads:
        aid = esc(ad.get("ad_id")); analysis = ad.get("analysis") or {}; status = ad.get("status")
        src = source_link(ad.get("source_url"))
        source = f'<a href="{esc(src)}" rel="noopener noreferrer">View source</a>' if src else "Source link unavailable"
        date = esc(ad.get("launch_date") or "Date unavailable")
        if status == "unavailable":
            content = f'<div class="unavailable"><strong>Analysis unavailable</strong><p>{esc(ad.get("error") or "No reason supplied.")}</p></div>'
        else:
            blocks = "".join(f'<div class="datum"><h4>{esc(k.replace("_", " ").title())}</h4><p>{esc(analysis.get(k))}</p></div>' for k in FIELDS if analysis.get(k) not in (None, "", []))
            beats = analysis.get("beats") or []
            beat_rows = "".join(f'<tr><td>{esc(b.get("start_seconds"))}–{esc(b.get("end_seconds"))}s</td><td>{esc(b.get("role"))}</td><td>{esc(b.get("observation"))}</td><td>{esc(b.get("dialogue"))}</td><td>{esc(b.get("overlay"))}</td></tr>' for b in beats)
            beat_table = f'<h4>Beat-by-beat</h4><div class="scroll"><table><thead><tr><th>Time</th><th>Role</th><th>Observation</th><th>Dialogue</th><th>Overlay</th></tr></thead><tbody>{beat_rows}</tbody></table></div>' if beats else ""
            frames = []
            for fr in ad.get("frames") or []:
                link = local_link(fr.get("path"), base, out)
                if link:
                    frames.append(f'<figure><a href="{esc(link)}"><img loading="lazy" src="{esc(link)}" alt="Frame at {esc(fr.get("seconds"))} seconds"></a><figcaption>{esc(fr.get("seconds"))}s</figcaption></figure>')
            media = local_link(ad.get("media_file"), base, out)
            player = ""
            if media:
                ext = Path(ad["media_file"]).suffix.lower()
                if ext in IMAGE_EXTS:
                    player = f'<img class="media-image" src="{esc(media)}" alt="Ad {aid} media">'
                else:
                    player = f'<video controls preload="metadata"><source src="{esc(media)}" type="{"video/mp4" if ext in {".mp4", ".m4v", ".mov"} else "video/webm"}">Your browser cannot play this video.</video>'
            frame_html = f'<div class="frames">{"".join(frames)}</div>' if frames else ""
            content = f'{player}{frame_html}<div class="data-grid">{blocks}</div>{beat_table}<h4>Limitations</h4>{list_html(analysis.get("limitations"))}'
        ad_cards.append(f'<article class="ad"><header><div><h3>Ad {aid}</h3><p class="muted">{date} · {esc(ad.get("execution_id"))}</p></div><span class="badge {"bad" if status == "unavailable" else "ok"}">{esc(status)}</span></header><p>{source}</p><blockquote>{esc(ad.get("ad_copy"))}</blockquote>{content}</article>')
    if data.get("top_sample"):
        top_doc = render_html({**data["top_sample"], "top_sample": None}, base, out)
        marker = '<details class="secondary"><summary>Detailed per-ad analysis</summary>'
        top_details = top_doc.split(marker, 1)[1].split("</details>", 1)[0]
        ad_cards.append("<h2>Separate sample details</h2>" + top_details)
    reviewed_count = sum(a.get("status") == "reviewed" for a in ads)
    unavailable_count = sum(a.get("status") == "unavailable" for a in ads)
    unique_count = len({a.get("execution_id") for a in ads if a.get("status") == "reviewed"})
    distinct_note = "Distinct creative count not established" if cov.get("deduplication_verified") is False else f"Unique reviewed executions {unique_count}"
    caps = f"Selected records {len(ads)} · Reviewed records {reviewed_count} · Unavailable records {unavailable_count} · {distinct_note}"
    method = f"Cohort: {esc(data.get('cohort'))}. Model: {esc(data.get('model'))}. Schema version: {esc(data.get('schema_version'))}. {ranking_note}"
    capture_fields = ("status", "reason", "completeness", "available_records", "raw_record_cap", "unique_target", "date_from", "date_to", "captured_at", "summary", "ranking_basis", "ranking_note")
    capture_lines = "".join(f'<div><strong>{esc(key.replace("_", " ").title())}:</strong> {esc(capture.get(key))}</div>' for key in capture_fields if capture.get(key) not in (None, "", []))
    capture_status = s(capture.get("status")).lower()
    capture_class = "bad" if capture_status in {"partial", "blocked", "empty", "error"} else "ok"
    capture_html = f'<section class="capture"><h3>Source capture</h3><span class="badge {capture_class}">{esc(capture.get("status") or "status unavailable")}</span><div class="capture-grid">{capture_lines or "<p>No capture details supplied.</p>"}</div></section>'
    class_matrices = []
    for dimension, label in (("setting", "Mechanic by setting"), ("emotion", "Mechanic by emotion")):
        rows, cols, counts = render_matrix(ads, "mechanic", dimension)
        body = "".join("<tr><th>" + esc(r) + "</th>" + "".join(f"<td>{counts[(r, c)]}</td>" for c in cols) + "</tr>" for r in rows)
        heads = "".join(f"<th>{esc(c)}</th>" for c in cols)
        class_matrices.append(f'<h3>{label}</h3><div class="scroll"><table><thead><tr><th>Reviewed records</th>{heads}</tr></thead><tbody>{body}</tbody></table></div>' if rows else f"<h3>{label}</h3><p>No reviewed records.</p>")
    if not families:
        clustering_note = "<p>Creative family clustering is pending.</p>"
    else:
        clustering_note = ""
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
:root{{--ink:#152329;--muted:#63737a;--line:#d9e2e3;--paper:#f5f8f7;--card:#fff;--teal:#087f75;--soft:#e6f3f0}}*{{box-sizing:border-box}}body{{margin:0;overflow-wrap:anywhere;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}}main{{max-width:1120px;margin:auto;padding:28px 20px 72px}}h1,h2,h3,h4{{line-height:1.2}}h1{{font-size:clamp(2rem,5vw,3.5rem);margin:.2em 0}}h2{{margin-top:2.3em;border-bottom:1px solid var(--line);padding-bottom:.45em}}h3{{margin:.2em 0}}p{{margin:.5em 0}}a{{color:#086f68;overflow-wrap:anywhere}}.hero{{background:#123b40;color:white;padding:clamp(24px,5vw,52px);border-radius:22px}}.hero p{{color:#d3e8e5}}.coverage,.ad,.family,.capture{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin:14px 0}}.coverage{{display:flex;gap:10px 22px;flex-wrap:wrap}}.capture-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px 18px;margin-top:12px}}.muted{{color:var(--muted);font-size:.92rem}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:700px;font-size:.92rem}}th,td{{border:1px solid var(--line);padding:9px;text-align:left;vertical-align:top}}th{{background:var(--soft)}}.family-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}}.pair,.data-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}.datum{{background:#f7faf9;padding:12px;border-radius:9px}}.datum h4,.pair h4{{margin:.1em 0 .4em}}ul{{padding-left:1.2em}}.ad header{{display:flex;justify-content:space-between;gap:10px;align-items:start}}.badge{{border-radius:999px;padding:4px 10px;font-size:.85rem;background:var(--soft);color:#086f68}}.badge.bad{{background:#fff0e8;color:#9b441c}}blockquote{{border-left:3px solid var(--teal);margin:14px 0;padding:8px 14px;color:#385057;background:#f7faf9;white-space:pre-wrap}}video,.media-image{{width:min(100%,720px);display:block;border-radius:10px;margin:14px 0}}.frames{{display:flex;gap:12px;overflow-x:auto;margin:12px 0}}figure{{margin:0;min-width:180px;width:28%}}figure img{{width:100%;height:160px;object-fit:contain;border-radius:9px;background:#e7eeee}}figcaption{{font-size:.85rem;color:var(--muted)}}.unavailable{{background:#fff6f0;border-radius:10px;padding:14px}}@media(max-width:600px){{main{{padding:14px 12px 44px}}.coverage{{display:grid;gap:4px}}.ad,.family,.capture{{padding:15px}}}}
.gallery-row{{display:contents}}.member-gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(205px,1fr));gap:16px;margin:20px 0}}.member{{background:white;border:1px solid var(--line);padding:12px;border-radius:10px;break-inside:avoid}}.member img,.missing-thumbnail{{display:block;width:100%;height:230px;object-fit:contain;background:#e7eeee}}.missing-thumbnail{{padding:60px 15px;text-align:center}}.member h4{{margin:12px 0 8px}}.rank-label{{font-weight:700}}.secondary{{margin:28px 0}}summary{{cursor:pointer;font-weight:700}}@page{{size:A4 landscape;margin:12mm;@bottom-right{{content:counter(page);font-size:9pt;color:#63737a}}}}@media print{{body{{background:white;font-size:10pt}}main{{max-width:none;padding:0}}.hero{{border-radius:0;padding:14px}}h1{{font-size:24pt}}.capture{{padding:12px;margin:10px 0}}.capture-grid{{display:block;font-size:9pt}}.capture-grid>div{{display:inline-block;vertical-align:top;width:48%;padding:2px 0}}.coverage{{padding:12px}}.cluster,.top-sample{{break-before:page;page-break-before:always}}.member-gallery{{display:block;break-before:avoid;page-break-before:avoid}}.gallery-row{{display:table;width:100%;table-layout:fixed;border-spacing:8px;margin-bottom:8px;break-inside:avoid;page-break-inside:avoid}}.gallery-row:first-child{{break-before:avoid;page-break-before:avoid}}.member{{display:table-cell;vertical-align:top;width:25%;padding:10px;font-size:9pt;page-break-inside:avoid}}.cluster>h2,.cluster>p,.cluster>.pair,.top-sample>h2,.top-sample>p{{break-after:avoid;page-break-after:avoid}}.member img,.missing-thumbnail{{height:130px}}.cluster h2,.top-sample h2{{margin-top:0}}.scroll{{overflow:visible}}table{{min-width:0}}.secondary,.print-button{{display:none}}.pair{{display:block}}.family-grid{{display:block;font-size:0}}.family{{display:inline-block;vertical-align:top;width:48%;margin:0 2% 8px 0;padding:10px;font-size:9pt;break-inside:avoid}}.family h3{{font-size:12pt}}}}
</style><main><button class="print-button" onclick="window.print()">Print / Save PDF</button><section class="hero"><p>CREATIVE RESEARCH</p><h1>{title}</h1></section><h2>Method and coverage</h2><p>{method}</p><div class="coverage">{caps}</div>{capture_html}<h2>Cluster overview</h2><div class="family-grid">{"".join(family_cards) or "<p>No families supplied.</p>"}</div>{family_gallery(data, base, out)}{top_gallery(data.get("top_sample"), base, out)}<details class="secondary"><summary>Classification matrices and membership audit</summary><h2>Mechanic classification matrices</h2>{''.join(class_matrices)}<h2>Creative family matrix</h2>{family_matrix}{clustering_note}</details><details class="secondary"><summary>Detailed per-ad analysis</summary>{"".join(ad_cards) or "<p>No ads supplied.</p>"}</details><h2>Overall limitations</h2>{list_html(data.get("limitations"))}</main></html>'''


def list_html(values):
    if not values:
        return "<p class=\"muted\">None supplied.</p>"
    return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in values) + "</ul>"


def markdown(data, base, out):
    cov = data.get("coverage") or {}
    capture = data.get("capture") or {}
    distinct_note = "Distinct creative count not established" if cov.get("deduplication_verified") is False else f"unique executions {cov.get('unique_executions', 0)}"
    lines = ["# Competitor ad analysis", "", f"**Method:** cohort `{s(data.get('cohort'))}`; model `{s(data.get('model'))}`; schema `{s(data.get('schema_version'))}`.", "", f"**Coverage:** selected {cov.get('selected', 0)}; reviewed {cov.get('reviewed', 0)}; unavailable {cov.get('unavailable', 0)}; {distinct_note}.", "", "Ranking verified in source capture." if data.get("cohort") == "top" and cov.get("ranking_verified") is True else "Ranking was not verified; no rank or performance claims are made.", "", "## Creative families", ""]
    insert_at = lines.index("## Creative families")
    capture_lines = ["## Source capture", ""]
    for key in ("status", "reason", "completeness", "available_records", "raw_record_cap", "unique_target", "date_from", "date_to", "captured_at", "summary", "ranking_basis", "ranking_note"):
        if capture.get(key) not in (None, "", []):
            capture_lines.append(f"**{key.replace('_', ' ').title()}:** {md_escape(capture.get(key))}")
            capture_lines.append("")
    if not capture_lines[2:]:
        capture_lines += ["No capture details supplied.", ""]
    lines[insert_at:insert_at] = capture_lines
    for f in data.get("families") or []:
        lines += [f"### {md_escape(f.get('name'))}", "", md_escape(f.get("definition")), "", "Shared structure: " + "; ".join(md_escape(x) for x in (f.get("shared_structure") or [])), "", "Exceptions: " + "; ".join(md_escape(x) for x in (f.get("exceptions") or [])), "", "Ads: " + ", ".join(md_escape(x) for x in (f.get("ad_ids") or [])), ""]
    lines += ["## Ads", ""]
    for a in data.get("ads") or []:
        lines += [f"### Ad {md_escape(a.get('ad_id'))} ({md_escape(a.get('status'))})", "", f"Launch date: {md_escape(a.get('launch_date'))}. Execution: {md_escape(a.get('execution_id'))}.", "", f"Source: {md_escape(a.get('source_url'))}", "", f"> {md_escape(a.get('ad_copy'))}", ""]
        media = local_link(a.get("media_file"), base, out)
        if media:
            lines += [f"Media: [{md_escape(Path(a['media_file']).name)}]({media})", ""]
        frame_links = [(fr, local_link(fr.get("path"), base, out)) for fr in (a.get("frames") or [])]
        frame_links = [(fr, link) for fr, link in frame_links if link]
        if frame_links:
            lines += ["Frames:", ""] + [f"- [{md_escape(fr.get('seconds'))}s]({link})" for fr, link in frame_links] + [""]
        an = a.get("analysis") or {}
        if a.get("status") == "unavailable":
            lines += ["Analysis unavailable: " + md_escape(a.get("error")), ""]
        else:
            for key in FIELDS:
                if an.get(key) not in (None, "", []): lines += [f"**{key.replace('_',' ').title()}:** {md_escape(an[key])}", ""]
            if an.get("beats"):
                lines += ["| Start | End | Role | Observation | Dialogue | Overlay |", "|---|---|---|---|---|---|"]
                for b in an["beats"]:
                    lines.append("| " + " | ".join(md_escape(b.get(k)).replace("|", "\\|").replace("\n", " ") for k in ("start_seconds", "end_seconds", "role", "observation", "dialogue", "overlay")) + " |")
                lines.append("")
            if an.get("limitations"):
                lines += ["**Analysis limitations:**", ""] + [f"- {md_escape(x)}" for x in an["limitations"]] + [""]
    lines += ["## Overall limitations", ""] + [f"- {md_escape(x)}" for x in data.get("limitations") or []]
    result = "\n".join(lines).rstrip() + "\n"
    if data.get("top_sample"):
        result += "\n## Separate sample\n\n" + markdown(data["top_sample"], base, out)
    return result


def md_escape(value):
    """Escape raw HTML in Markdown while retaining normal readable text."""
    return html.escape(s(value), quote=False)


def prefix_asset_refs(data, namespace):
    for ad in data.get("ads") or []:
        if ad.get("media_file") and not Path(ad["media_file"]).is_absolute():
            ad["media_file"] = namespace + "/" + ad["media_file"]
        for frame in ad.get("frames") or []:
            if frame.get("path") and not Path(frame["path"]).is_absolute():
                frame["path"] = namespace + "/" + frame["path"]


def validate_data(data):
    if data.get("top_sample") is not None:
        if not isinstance(data["top_sample"], dict) or data["top_sample"].get("top_sample") is not None:
            raise ValueError("top_sample must be a single cohort object")
        validate_data(data["top_sample"])
    cov = data.get("coverage") or {}
    ads = data.get("ads") or []
    reviewed = [a for a in ads if a.get("status") == "reviewed"]
    unavailable = [a for a in ads if a.get("status") == "unavailable"]
    if any(a.get("status") not in {"reviewed", "unavailable"} for a in ads):
        raise ValueError("Each ad status must be reviewed or unavailable")
    expected = {
        "selected": len(ads),
        "reviewed": len(reviewed),
        "unavailable": len(unavailable),
        "unique_executions": len({a.get("execution_id") for a in reviewed}),
    }
    for key, actual in expected.items():
        if cov.get(key) != actual:
            raise ValueError(f"coverage.{key} is {cov.get(key)!r}; expected {actual}")
    families = data.get("families") or []
    if families:
        names = [f.get("name") for f in families]
        if len(names) != len(set(names)):
            raise ValueError("Family names must be unique for the family matrix")
        expected_ids = [a.get("ad_id") for a in reviewed]
        family_ids = [ad_id for f in families for ad_id in (f.get("ad_ids") or [])]
        if Counter(family_ids) != Counter(expected_ids):
            raise ValueError("Families must include each reviewed ad_id exactly once")


def write_report(input_path: Path, output_dir: Path, top_input: Path | None = None):
    input_path = input_path.resolve()
    base = input_path.parent
    out = output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    validate_data(data)
    if top_input is not None:
        if data.get("top_sample") is not None:
            raise ValueError("Input already has top_sample; do not also supply --top-input")
        top_input = Path(top_input).resolve()
        top = json.loads(top_input.read_text(encoding="utf-8"))
        validate_data(top)
        if top.get("top_sample") is not None:
            raise ValueError("Separate top input cannot contain another top_sample")
        for namespace, sample, source_base in (("main", data, base), ("top", top, top_input.parent)):
            if (out / namespace).is_symlink():
                raise ValueError("Refusing symlinked report asset namespace")
            copy_local_assets(sample, source_base, out / namespace)
            prefix_asset_refs(sample, namespace)
        data["top_sample"] = top
    else:
        copy_local_assets(data, base, out)
    validate_data(data)
    render_base = out if out != base else base
    (out / "index.html").write_text(render_html(data, render_base, out), encoding="utf-8")
    (out / "report.md").write_text(markdown(data, render_base, out), encoding="utf-8")
    with (out / "ads.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["ad_id", "execution_id", "source_url", "launch_date", "status", "ad_copy", *FIELDS, "error"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for a in data.get("ads") or []:
            an = a.get("analysis") or {}
            row = {k: a.get(k, "") for k in ("ad_id", "execution_id", "source_url", "launch_date", "status", "ad_copy", "error")}
            row.update({k: an.get(k, "") for k in FIELDS})
            w.writerow({k: cell(v) for k, v in row.items()})
    write_matrix(out / "mechanic-setting.csv", data.get("ads") or [], "mechanic", "setting")
    write_matrix(out / "mechanic-emotion.csv", data.get("ads") or [], "mechanic", "emotion")
    copy_path = out / "analysis.json"
    copy_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="analysis.json")
    parser.add_argument("--out", required=True, type=Path, help="report output directory")
    parser.add_argument("--top-input", type=Path, help="Separately analyzed optional top cohort JSON")
    args = parser.parse_args()
    render(args.input, args.out, args.top_input)


def render(input_path: Path, out_dir: Path, top_input: Path | None = None):
    """Public convenience entry point matching the CLI input/output contract."""
    return write_report(Path(input_path), Path(out_dir), top_input)

if __name__ == "__main__":
    main()
