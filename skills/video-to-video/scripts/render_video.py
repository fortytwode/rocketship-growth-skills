#!/usr/bin/env python3
"""Portable deterministic validator/storyboard/concat renderer (FFmpeg required)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROVENANCE_LABELS = {
    "client-owned", "approved-generated", "newly-generated", "deterministic-derivative",
    "client-owned inventoried", "approved generated reused", "newly generated", "deterministic derivative",
}
PROBE_TIMEOUT_SECONDS = 30
RENDER_TIMEOUT_SECONDS = 600
VIDEO_FIELDS = ("codec_name", "profile", "width", "height", "pix_fmt", "avg_frame_rate", "time_base",
                "sample_aspect_ratio", "display_aspect_ratio")
AUDIO_FIELDS = ("codec_name", "sample_rate", "channels", "channel_layout")


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("manifest.scenes must be a non-empty array")
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValueError(f"scene {index} must be an object")
        for key in ("id", "line", "visual", "provenance"):
            if not scene.get(key):
                raise ValueError(f"scene {index} missing {key}")
        if not isinstance(scene["provenance"], str) or scene["provenance"] not in PROVENANCE_LABELS:
            raise ValueError(f"scene {index} provenance must use a documented source category")
    return data


def scene_sources(data: dict) -> list[Path]:
    clips = []
    for index, scene in enumerate(data["scenes"], 1):
        source = scene.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError(f"scene {index} source must be a nonempty string")
        if any(ord(c) < 32 or ord(c) == 127 for c in source):
            raise ValueError(f"scene {index} source contains a control character")
        path = Path(source)
        resolved = path.resolve()
        if any(ord(c) < 32 or ord(c) == 127 for c in resolved.as_posix()):
            raise ValueError(f"scene {index} source resolves to a path with a control character")
        clips.append(path)
    missing = [str(p) for p in clips if not p.is_file()]
    if missing:
        raise ValueError(f"missing scene sources: {', '.join(missing)}")
    return clips


def validate(args: argparse.Namespace) -> None:
    data = load_manifest(args.manifest)
    scene_sources(data)
    digest = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    print(json.dumps({"status": "valid", "manifest_sha256": digest, "scene_count": len(data["scenes"])}, indent=2))


def storyboard(args: argparse.Namespace) -> None:
    if args.output.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("storyboard is Markdown; use an .md output filename")
    data = load_manifest(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Reconstruction storyboard", "", f"Concept: {data.get('concept', '')}", ""]
    for scene in data["scenes"]:
        lines.extend([f"## {scene['id']}  {scene.get('time', '')}", f"**Line:** {scene['line']}",
                      f"**Visual:** {scene['visual']}", f"**Provenance:** {scene['provenance']}", ""])
    with args.output.open('x', encoding='utf-8') as output:
        output.write("\n".join(lines))
    print(args.output)


def probe(path: Path, ffprobe: str) -> dict:
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
                                check=True, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as e:
        raise ValueError(f"ffprobe timed out inspecting {path}") from e
    except subprocess.CalledProcessError as e:
        raise ValueError(f"ffprobe could not inspect {path}: {e.stderr.strip()}") from e
    try:
        streams = json.loads(result.stdout)["streams"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ValueError(f"ffprobe returned invalid stream metadata for {path}") from e
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) > 1 or len(streams) != len(videos) + len(audios):
        raise ValueError(f"{path} must contain exactly one video stream and zero or one audio stream; additional streams are unsupported")
    return {"video": videos[0], "audio": audios[0] if audios else None}


def compatibility(reference: dict, candidate: dict, path: Path) -> None:
    # A concat container cannot retain a different display transform per scene.
    # Single-scene byte copies preserve it; multi-scene inputs need normalization.
    for video in (reference['video'], candidate['video']):
        transform = any('display matrix' in str(side.get('side_data_type', '')).lower()
                        for side in video.get('side_data_list', []))
        if transform or video.get('tags', {}).get('rotate', '0') not in ('0', 0, '0.0'):
            raise ValueError('multi-clip display transforms are unsupported; normalize orientation in your editor before assembly')
    for kind, fields in (("video", VIDEO_FIELDS), ("audio", AUDIO_FIELDS)):
        left, right = reference[kind], candidate[kind]
        if (left is None) != (right is None):
            raise ValueError(f"incompatible clips: audio presence differs at {path}")
        if left is None:
            continue
        diffs = [f for f in fields if left.get(f) != right.get(f)]
        if diffs:
            raise ValueError(f"incompatible clips: {kind} {', '.join(diffs)} differ at {path}")


def render(args: argparse.Namespace) -> None:
    clips = scene_sources(load_manifest(args.manifest))
    # Refuse destination aliases before creating directories or temporary files.
    output_arg = args.output
    if output_arg.is_symlink() or output_arg.exists():
        raise ValueError(f"output already exists: {output_arg}")
    out_resolved = output_arg.resolve()
    protected = [args.manifest.resolve(), *(p.resolve() for p in clips)]
    if out_resolved in protected:
        raise ValueError("output path aliases the manifest or a scene input")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    missing = [name for name, path in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not path]
    if missing:
        raise ValueError("missing required tool(s): " + ", ".join(missing) + "; install FFmpeg with ffmpeg and ffprobe on PATH")
    reference = probe(clips[0], ffprobe)
    profiles = [reference]
    for clip in clips[1:]:
        candidate = probe(clip, ffprobe)
        compatibility(reference, candidate, clip)
        profiles.append(candidate)
    if len(clips) > 1 and any(p["audio"] and p["audio"].get("codec_name") != "pcm_s16le" for p in profiles):
        raise ValueError("multi-scene assembly supports only uncompressed PCM audio; compressed audio may not concatenate gaplessly")
    if len(clips) == 1 and args.output.suffix.lower() != clips[0].suffix.lower():
        raise ValueError("single-scene output must keep the source container suffix; use a separate editor/remux tool to change containers")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=args.output.parent) as td:
        temp = Path(td)
        concat = temp / "concat.txt"
        temp_output = temp / f"output{args.output.suffix}"
        def concat_quote(path: Path) -> str:
            return "file '" + path.resolve().as_posix().replace("'", r"'\''") + "'"
        if len(clips) == 1:
            shutil.copyfile(clips[0], temp_output)
        else:
            concat.write_text("\n".join(concat_quote(p) for p in clips) + "\n", encoding="utf-8")
            try:
                subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                                "-c", "copy", str(temp_output)], check=True, capture_output=True, text=True,
                               timeout=RENDER_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as e:
                raise ValueError("FFmpeg render timed out") from e
            except subprocess.CalledProcessError as e:
                raise ValueError(f"FFmpeg render failed: {e.stderr.strip()}") from e
        try:
            os.link(temp_output, args.output)
        except FileExistsError as e:
            raise ValueError(f"output appeared during render; refusing to replace: {args.output}") from e
        except OSError as e:
            raise ValueError(f"atomic no-overwrite publication requires hard-link support on the output filesystem: {e}") from e
    print(args.output)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="render_video")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("validate", validate), ("storyboard", storyboard), ("render", render)):
        p = sub.add_parser(name)
        p.add_argument("--manifest", type=Path, required=True)
        p.add_argument("--output", type=Path, required=name != "validate")
        p.set_defaults(func=fn)
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
