#!/usr/bin/env python3
"""Plan which AI model to use for each shot of a video and estimate the cost.

Standard library only. Reads a shot list (JSON) and a price table (JSON),
picks an eligible model for every shot and prints a plan with a total.
It never calls a provider, reads a key or spends money.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MODELS = HERE.parent / "data" / "models.json"
RES_ORDER = ["480p", "720p", "1080p"]


class PlanError(ValueError):
    pass


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _rate(model, resolution, audio):
    """Price per second for a video model at a resolution, or None."""
    prices = model.get("price_per_second", {})
    rate = prices.get(resolution)
    if rate is None:
        return None
    if audio and not model.get("audio_included", False):
        extra = model.get("audio_price_per_second", {}).get(resolution)
        if extra is None:
            return None
        rate = extra
    return float(rate)


def eligible(model, shot):
    """Return (ok, reason). A model must meet every need the shot states."""
    if model["kind"] != "video":
        return False, "not a video model"
    if shot.get("dialogue") and not model.get("native_audio"):
        return False, "no native speech"
    refs = int(shot.get("reference_images", 0))
    if refs > int(model.get("max_reference_images", 0)):
        if not (refs == 1 and model.get("start_image")):
            return False, "takes %s reference images, shot needs %s" % (
                model.get("max_reference_images", 0), refs)
    if shot.get("photoreal_faces") and model.get("photoreal_faces") == "refused":
        return False, "refused photoreal faces when we tested it"
    if _rate(model, shot.get("resolution", "720p"), bool(shot.get("dialogue") or shot.get("audio"))) is None:
        return False, "no price for %s" % shot.get("resolution", "720p")
    if shot.get("only") and model["id"] not in shot["only"]:
        return False, "not in this shot's allowed list"
    return True, ""


def segments(seconds, model, references=0):
    """Split a shot into clips the model can generate. Returns clip lengths."""
    lo = float(model.get("min_seconds", 1))
    hi = float(model.get("max_seconds", 8))
    fixed = model.get("fixed_seconds")
    if references > 1 and model.get("fixed_seconds_with_references"):
        fixed = model["fixed_seconds_with_references"]
    if fixed:
        n = max(1, math.ceil(seconds / max(fixed)))
        best = min((f for f in fixed if f * n >= seconds), default=max(fixed))
        return [float(best)] * n
    n = max(1, math.ceil(seconds / hi))
    each = max(lo, seconds / n)
    return [round(each, 2)] * n


def cost_for(model, shot, retake_rate):
    res = shot.get("resolution", "720p")
    audio = bool(shot.get("dialogue") or shot.get("audio"))
    clips = segments(float(shot["seconds"]), model, int(shot.get("reference_images", 0)))
    billed = sum(clips)
    rate = _rate(model, res, audio)
    first = billed * rate
    return {
        "clips": clips,
        "billed_seconds": round(billed, 2),
        "rate": rate,
        "first_pass": round(first, 2),
        "with_retakes": round(first * (1 + retake_rate), 2),
    }


def pick(models, shot, retake_rate, prefer="cheapest"):
    options, rejected = [], []
    for m in models:
        ok, why = eligible(m, shot)
        if not ok:
            if m["kind"] == "video":
                rejected.append({"model": m["id"], "reason": why})
            continue
        c = cost_for(m, shot, retake_rate)
        options.append((m, c))
    if not options:
        raise PlanError("No model in the table can make shot %r: %s" % (
            shot.get("id"), "; ".join("%s (%s)" % (r["model"], r["reason"]) for r in rejected)))
    want_hero = shot.get("tier") == "hero" or (prefer == "quality" and shot.get("tier") != "standard")
    pool = ([o for o in options if o[0].get("tier") == "hero"] or options) if want_hero else options
    pool.sort(key=lambda o: (o[1]["with_retakes"], o[0]["id"]))
    return pool[0], [o for o in sorted(options, key=lambda o: o[1]["with_retakes"]) if o is not pool[0]], rejected


def plan(project, table, prefer="cheapest"):
    models = table["models"]
    by_id = {m["id"]: m for m in models}
    retake_rate = float(project.get("retake_rate", 0.5))
    if not 0 <= retake_rate <= 5:
        raise PlanError("retake_rate must be between 0 and 5 (0.5 means half the shots are made twice)")
    shots = project.get("shots") or []
    if not shots:
        raise PlanError("The project has no shots")
    rows, total_first, total = [], 0.0, 0.0
    for i, shot in enumerate(shots, 1):
        shot = dict(shot)
        shot.setdefault("id", "shot-%02d" % i)
        if float(shot.get("seconds", 0)) <= 0:
            raise PlanError("Shot %s needs a positive 'seconds'" % shot["id"])
        if shot.get("resolution", "720p") not in RES_ORDER:
            raise PlanError("Shot %s: resolution must be one of %s" % (shot["id"], ", ".join(RES_ORDER)))
        (model, c), others, rejected = pick(models, shot, retake_rate, prefer)
        rows.append({
            "id": shot["id"], "what": shot.get("what", ""), "seconds": float(shot["seconds"]),
            "model": model["id"], "name": model["name"], "provider": model["provider"],
            "resolution": shot.get("resolution", "720p"), "verified": bool(model.get("verified")),
            "next_best": others[0][0]["id"] if others else None,
            "next_best_cost": others[0][1]["with_retakes"] if others else None,
            "notes": model.get("notes", ""), **c,
        })
        total_first += c["first_pass"]
        total += c["with_retakes"]
    extras = []
    stills = project.get("stills") or {}
    if stills.get("count"):
        m = by_id.get(stills.get("model", "")) or min(
            (x for x in models if x["kind"] == "image"), key=lambda x: x["price_per_image"])
        n = int(stills["count"]) * (1 + int(stills.get("revisions_each", 1)))
        extras.append({"item": "Reference and storyboard images", "detail": "%d images on %s (%s)" % (n, m["name"], m["provider"]),
                       "cost": round(n * float(m["price_per_image"]), 2), "verified": bool(m.get("verified"))})
    vo = project.get("voiceover") or {}
    if vo.get("characters"):
        m = by_id.get(vo.get("model", "")) or next((x for x in models if x["kind"] == "voice"), None)
        if m:
            extras.append({"item": "Voiceover", "detail": "%d characters on %s" % (int(vo["characters"]), m["name"]),
                           "cost": round(int(vo["characters"]) / 1000 * float(m["price_per_1k_characters"]), 2),
                           "verified": bool(m.get("verified"))})
    grand = round(total + sum(e["cost"] for e in extras), 2)
    out = {
        "title": project.get("title", "Untitled"), "prices_as_of": table.get("as_of"),
        "retake_rate": retake_rate, "shots": rows, "extras": extras,
        "video_first_pass": round(total_first, 2), "video_with_retakes": round(total, 2), "total": grand,
        "finished_seconds": round(sum(r["seconds"] for r in rows), 2),
        "unverified_prices": sorted({r["model"] for r in rows if not r["verified"]}),
    }
    sub = project.get("subscription_per_month")
    if sub:
        out["subscription_per_month"] = float(sub)
        out["videos_per_month_to_match_subscription"] = round(float(sub) / grand, 1) if grand else None
    return out


def to_markdown(p):
    L = ["# Cost plan: %s" % p["title"], "",
         "Prices as of %s. They are list prices and estimates, not a quote. Check the provider's page before you spend." % p["prices_as_of"], "",
         "| Shot | What happens | Seconds | Model | Bought from | Clips | First pass | With retakes |",
         "|---|---|---|---|---|---|---|---|"]
    for r in p["shots"]:
        clips = " + ".join("%gs" % c for c in r["clips"])
        L.append("| %s | %s | %g | %s%s | %s | %s | $%.2f | $%.2f |" % (
            r["id"], r["what"], r["seconds"], r["name"], "" if r["verified"] else " *", r["provider"], clips, r["first_pass"], r["with_retakes"]))
    L += ["", "- **Finished length:** %g seconds" % p["finished_seconds"],
          "- **Video, first pass:** $%.2f" % p["video_first_pass"],
          "- **Video, allowing for retakes (%d%% extra):** $%.2f" % (round(p["retake_rate"] * 100), p["video_with_retakes"])]
    for e in p["extras"]:
        L.append("- **%s:** $%.2f (%s)" % (e["item"], e["cost"], e["detail"]))
    L.append("- **Total:** $%.2f" % p["total"])
    if p.get("subscription_per_month"):
        L.append("- **Compared with a $%g a month subscription:** that pays for about %s videos like this one at these prices." % (
            p["subscription_per_month"], p["videos_per_month_to_match_subscription"]))
    if p["unverified_prices"]:
        L += ["", "\\* Price not re-checked against the provider's page: %s." % ", ".join(p["unverified_prices"])]
    alt = [r for r in p["shots"] if r["next_best"]]
    if alt:
        L += ["", "## Next best option per shot", ""]
        for r in alt:
            L.append("- %s: %s at $%.2f with retakes" % (r["id"], r["next_best"], r["next_best_cost"]))
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("project", help="shot list JSON (see examples/shots.json)")
    ap.add_argument("--models", default=str(DEFAULT_MODELS), help="price table JSON")
    ap.add_argument("--prefer", choices=["cheapest", "quality"], default="cheapest")
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    ap.add_argument("--out", help="write the plan to this file as well")
    a = ap.parse_args(argv)
    try:
        result = plan(load_json(a.project), load_json(a.models), a.prefer)
    except (PlanError, KeyError, json.JSONDecodeError, OSError) as exc:
        print("Could not build the plan: %s" % exc, file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2) if a.json else to_markdown(result)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
