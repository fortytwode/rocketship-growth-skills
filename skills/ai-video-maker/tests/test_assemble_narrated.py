"""Contract and real-media tests for the voiceover film assembler."""

from __future__ import annotations

import array
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = TOOLS / "assemble_narrated.py"
_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)
FONT = next((f for f in _FONT_CANDIDATES if f.exists()), _FONT_CANDIDATES[0])


def load_module():
    spec = importlib.util.spec_from_file_location("assemble_narrated", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, data: bytes = b"fixture") -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path.relative_to(path.parents[1])), "sha256": sha(path)}


def base_plan(root: Path) -> dict:
    narration = root / "assets" / "narration.wav"
    segment = root / "assets" / "segment.mp4"
    narration.parent.mkdir(parents=True, exist_ok=True)
    narration.write_bytes(b"narration")
    segment.write_bytes(b"segment")
    return {
        "schema_version": 1,
        "canvas": {"width": 64, "height": 96, "fps": 10},
        "narration": {"path": "assets/narration.wav", "sha256": sha(narration)},
        "segments": [{
            "id": "opening",
            "source": "assets/segment.mp4",
            "sha256": sha(segment),
            "in_seconds": 0,
            "duration_seconds": 1,
        }],
        "output": {"path": "outputs/film.mp4", "receipt": "work/assembly.json"},
    }


@pytest.fixture
def mocked(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(
        module,
        "probe_video",
        lambda _path, _tools: {
            "width": 64,
            "height": 96,
            "fps": "10/1",
            "frames": 20,
            "duration_seconds": 2.0,
        },
    )
    monkeypatch.setattr(module, "probe_audio", lambda _path, _tools: {"duration_seconds": 0.8})
    monkeypatch.setattr(module, "_require_decoded_audio_signal", lambda _path, _tools: None)
    monkeypatch.setattr(module, "resolve_media_tools", lambda **_kwargs: ("ffmpeg", "ffprobe"))
    return module


def test_missing_and_stale_media_fail_before_assembly(mocked, tmp_path):
    plan = base_plan(tmp_path)
    plan["segments"][0]["source"] = "assets/missing.mp4"
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(mocked.NarratedAssemblyError, match="segment 1 is missing"):
        mocked.load_plan(tmp_path, path)

    plan = base_plan(tmp_path)
    plan["narration"]["sha256"] = "0" * 64
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(mocked.NarratedAssemblyError, match="narration bytes do not match"):
        mocked.load_plan(tmp_path, path)


@pytest.mark.parametrize("value", [-1, 0, float("nan"), float("inf"), 0.15])
def test_invalid_or_non_frame_aligned_durations_are_rejected(mocked, tmp_path, value):
    plan = base_plan(tmp_path)
    plan["segments"][0]["duration_seconds"] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(mocked.NarratedAssemblyError, match="duration_seconds|non-finite"):
        mocked.load_plan(tmp_path, path)


def test_endcard_defaults_to_no_text_and_duplicate_policy_is_temporal(mocked, tmp_path):
    plan = base_plan(tmp_path)
    plan["endcard"] = {"seconds": 0.5, "background": "#171512"}
    path = tmp_path / "default.json"
    path.write_text(json.dumps(plan))
    normalized = mocked.load_plan(tmp_path, path)
    assert normalized["endcard"]["text"] is None
    assert normalized["total_frames"] == 15

    plan["captions"] = {
        "events": [{"text": "Install tonight", "start": 0.8, "end": 1.5}],
        "font": {"path": "assets/font.ttf", "sha256": "0" * 64},
    }
    font = tmp_path / "assets" / "font.ttf"
    font.write_bytes(b"font")
    plan["captions"]["font"]["sha256"] = sha(font)
    plan["endcard"].update({
        "text": " install  tonight ",
        "font": {"path": "assets/font.ttf", "sha256": sha(font)},
    })
    path.write_text(json.dumps(plan))
    with pytest.raises(mocked.NarratedAssemblyError, match="duplicates an overlapping caption"):
        mocked.load_plan(tmp_path, path)

    plan["endcard"]["allow_caption_duplicate"] = True
    path.write_text(json.dumps(plan))
    assert mocked.load_plan(tmp_path, path)["endcard"]["text"] == "install tonight"

    plan["endcard"].pop("allow_caption_duplicate")
    plan["captions"]["events"][0].update({"start": 0, "end": 0.5})
    path.write_text(json.dumps(plan))
    assert mocked.load_plan(tmp_path, path)["endcard"]["text"] == "install tonight"


def test_inline_and_hash_bound_file_captions_share_the_same_contract(mocked, tmp_path):
    plan = base_plan(tmp_path)
    font = tmp_path / "assets" / "font.ttf"
    font.write_bytes(b"font")
    caption_file = tmp_path / "captions.json"
    events = [{"text": "A quiet line", "start": 0, "end": 0.5}]
    caption_file.write_text(json.dumps(events))
    plan["captions"] = {
        "path": "captions.json",
        "sha256": sha(caption_file),
        "font": {"path": "assets/font.ttf", "sha256": sha(font)},
        "style": {"bottom_margin": 12, "max_width": 56, "max_lines": 2,
                  "min_font_size": 8, "max_font_size": 12},
    }
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(plan))
    normalized = mocked.load_plan(tmp_path, path)
    assert normalized["captions"]["events"][0]["text"] == "A quiet line"
    assert normalized["captions"]["style"]["bottom_margin"] == 12
    assert any(row["role"] == "captions" for row in normalized["sources"])

    plan["captions"].pop("path")
    plan["captions"].pop("sha256")
    plan["captions"]["events"] = events
    path.write_text(json.dumps(plan))
    assert mocked.load_plan(tmp_path, path)["captions"]["events"][0]["end_frame"] == 5


def test_narration_may_be_padded_but_never_truncated(mocked, tmp_path, monkeypatch):
    plan = base_plan(tmp_path)
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(plan))
    assert mocked.load_plan(tmp_path, path)["silence_padding_seconds"] == pytest.approx(0.2)

    monkeypatch.setattr(mocked, "probe_audio", lambda _path, _tools: {"duration_seconds": 1.001})
    with pytest.raises(mocked.NarratedAssemblyError, match="narration is longer"):
        mocked.load_plan(tmp_path, path)


def test_optional_sfx_is_hash_bound_and_keeps_explicit_timing(mocked, tmp_path):
    plan = base_plan(tmp_path)
    sfx = tmp_path / "assets" / "impact.mp4"
    sfx.write_bytes(b"approved native sound")
    plan["sfx"] = [{
        "source": "assets/impact.mp4", "sha256": sha(sfx),
        "in_seconds": 0, "duration_seconds": 0.2,
        "timeline_start_seconds": 0.4, "gain_db": -6, "fade_seconds": 0.005,
    }]
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(plan))
    normalized = mocked.load_plan(tmp_path, path)
    assert normalized["sfx"][0]["frames"] == 2
    assert normalized["sfx"][0]["timeline_start_frame"] == 4
    assert str(normalized["sfx"][0]["fade_seconds"]) == "0.005"
    assert normalized["sources"][-1]["role"] == "sfx:1"


def test_outputs_cannot_overwrite_sources_each_other_or_prior_outputs(mocked, tmp_path):
    plan = base_plan(tmp_path)
    plan["output"]["path"] = "assets/segment.mp4"
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(mocked.NarratedAssemblyError, match="output path collides with an input"):
        mocked.load_plan(tmp_path, path)

    plan = base_plan(tmp_path)
    plan["output"]["receipt"] = plan["output"]["path"]
    path.write_text(json.dumps(plan))
    with pytest.raises(mocked.NarratedAssemblyError, match="output and receipt must be different"):
        mocked.load_plan(tmp_path, path)

    plan = base_plan(tmp_path)
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "film.mp4").write_bytes(b"prior")
    path.write_text(json.dumps(plan))
    with pytest.raises(mocked.NarratedAssemblyError, match="refusing to overwrite existing output"):
        mocked.load_plan(tmp_path, path)


def test_final_publish_is_exclusive_even_after_preflight(mocked, tmp_path):
    temporary = tmp_path / "rendering.mp4"
    destination = tmp_path / "published.mp4"
    temporary.write_bytes(b"new render")
    destination.write_bytes(b"racing render")
    with pytest.raises(mocked.NarratedAssemblyError, match="appeared during render"):
        mocked._publish_exclusive(temporary, destination)
    assert destination.read_bytes() == b"racing render"


def test_competing_receipt_preserves_verified_output_for_bound_recovery(mocked, tmp_path, monkeypatch):
    plan_path = tmp_path / "edit-plan.json"
    plan_path.write_text("{}")
    output = tmp_path / "outputs" / "film.mp4"
    receipt_path = tmp_path / "work" / "receipt.json"
    plan = {
        "root": tmp_path,
        "plan_path": plan_path,
        "plan_sha256": sha(plan_path),
        "canvas": {"width": 64, "height": 96, "fps": 10},
        "tools": ("ffmpeg", "ffprobe"),
        "narration_path": tmp_path / "assets" / "narration.wav",
        "narration_duration_seconds": 0.8,
        "segments": [{
            "path": tmp_path / "assets" / "segment.mp4", "in_frame": 0,
            "frames": 10, "crop": None,
        }],
        "captions": None,
        "endcard": None,
        "sfx": [],
        "sources": [],
        "total_frames": 10,
        "duration_seconds": 1.0,
        "silence_padding_seconds": 0.2,
        "output_path": output,
        "receipt_path": receipt_path,
    }
    monkeypatch.setattr(mocked, "_verify_bindings", lambda _plan: None)
    monkeypatch.setattr(mocked, "_validate_output", lambda _path, _plan: {
        "width": 64, "height": 96, "fps": "10/1", "frames": 10,
        "duration_seconds": 1.0, "audio_duration_seconds": 1.0,
    })

    def render(command, **_kwargs):
        Path(command[-1]).write_bytes(b"verified render")

    monkeypatch.setattr(mocked.subprocess, "run", render)
    original_exclusive_json = mocked._exclusive_json
    raced = False

    def competing_receipt(path, payload):
        nonlocal raced
        if path == receipt_path:
            if raced:
                return original_exclusive_json(path, payload)
            raced = True
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text('{"status": "COMPETING"}\n')
        original_exclusive_json(path, payload)

    monkeypatch.setattr(mocked, "_exclusive_json", competing_receipt)
    with pytest.raises(mocked.NarratedAssemblyError, match="recovery receipt") as error:
        mocked.assemble(plan)

    recovery_path = Path(str(error.value).rsplit(": ", 1)[1])
    assert output.read_bytes() == b"verified render"
    assert json.loads(receipt_path.read_text()) == {"status": "COMPETING"}
    assert recovery_path.is_file()

    with pytest.raises(mocked.NarratedAssemblyError, match="refusing to overwrite existing receipt"):
        mocked.recover_receipt(tmp_path, recovery_path)
    assert output.read_bytes() == b"verified render"
    assert recovery_path.is_file()

    receipt_path.unlink()
    output.write_bytes(b"arbitrary replacement")
    with pytest.raises(mocked.NarratedAssemblyError, match="output bytes do not match"):
        mocked.recover_receipt(tmp_path, recovery_path)
    assert not receipt_path.exists()
    assert recovery_path.is_file()

    output.write_bytes(b"verified render")
    recovered = mocked.recover_receipt(tmp_path, recovery_path)
    assert recovered["status"] == "ASSEMBLED"
    assert json.loads(receipt_path.read_text()) == recovered
    assert output.read_bytes() == b"verified render"
    assert not recovery_path.exists()


def test_cli_exposes_plan_check_and_assemble_help():
    for args in (["--help"], ["plan", "--help"], ["check", "--help"], ["assemble", "--help"], ["recover", "--help"]):
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), *args], capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "--project-root" in result.stdout or args == ["--help"]
        assert ("--recovery-receipt" if args == ["recover", "--help"] else "--edit-plan") in result.stdout or args == ["--help"]


def _require_real_tools(module):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe required")
    try:
        return module.resolve_media_tools(require_drawtext=True)
    except module.NarratedAssemblyError as error:
        pytest.skip(str(error))


def _pcm(path: Path) -> array.array:
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
        "-ar", "8000", "-f", "s16le", "-",
    ])
    values = array.array("h")
    values.frombytes(raw)
    return values


@pytest.mark.skipif(not FONT.exists(), reason="no bold TTF found on this machine")
def test_real_ffmpeg_assembly_preserves_narration_and_writes_hash_receipt(tmp_path):
    module = load_module()
    ffmpeg, _ = _require_real_tools(module)
    assets = tmp_path / "assets"
    assets.mkdir()
    segment = assets / "segment.mp4"
    narration = assets / "narration.wav"
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=64x96:r=10:d=1", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(segment),
    ], check=True)
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=8000:duration=0.5", "-c:a", "pcm_s16le",
        str(narration),
    ], check=True)
    font = assets / "font.ttf"
    font.write_bytes(FONT.read_bytes())
    plan = {
        "schema_version": 1,
        "canvas": {"width": 64, "height": 96, "fps": 10},
        "narration": {"path": "assets/narration.wav", "sha256": sha(narration)},
        "segments": [{
            "id": "only", "source": "assets/segment.mp4", "sha256": sha(segment),
            "in_seconds": 0, "duration_seconds": 1,
        }],
        "captions": {
            "events": [{"text": "Narration wins", "start": 0, "end": 0.5}],
            "font": {"path": "assets/font.ttf", "sha256": sha(font)},
            "style": {"bottom_margin": 8, "max_width": 56, "max_lines": 2,
                      "min_font_size": 8, "max_font_size": 12},
        },
        "output": {"path": "outputs/film.mp4", "receipt": "work/receipt.json"},
    }
    plan_path = tmp_path / "edit-plan.json"
    plan_path.write_text(json.dumps(plan))

    normalized = module.load_plan(tmp_path, plan_path)
    receipt = module.assemble(normalized)

    output = tmp_path / "outputs" / "film.mp4"
    receipt_path = tmp_path / "work" / "receipt.json"
    persisted = json.loads(receipt_path.read_text())
    assert receipt == persisted
    assert persisted["plan"]["sha256"] == sha(plan_path)
    assert persisted["output"]["sha256"] == sha(output)
    assert {row["role"] for row in persisted["sources"]} >= {"narration", "segment:only", "caption_font"}
    samples = _pcm(output)
    assert sum(abs(value) for value in samples[:3000]) / 3000 > 500
    assert sum(abs(value) for value in samples[-2000:]) / 2000 < 100


@pytest.mark.skipif(not FONT.exists(), reason="no bold TTF found on this machine")
def test_real_ffmpeg_assembly_wraps_configured_endcard_text(tmp_path):
    module = load_module()
    ffmpeg, _ = _require_real_tools(module)
    assets = tmp_path / "assets"
    assets.mkdir()
    segment = assets / "segment.mp4"
    narration = assets / "narration.wav"
    font = assets / "font.ttf"
    font.write_bytes(FONT.read_bytes())
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=720x1280:r=10:d=1", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(segment),
    ], check=True)
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=8000:duration=0.5", "-c:a", "pcm_s16le",
        str(narration),
    ], check=True)
    text = "Listen tonight"
    plan = {
        "schema_version": 1,
        "canvas": {"width": 720, "height": 1280, "fps": 10},
        "narration": {"path": "assets/narration.wav", "sha256": sha(narration)},
        "segments": [{
            "id": "opening", "source": "assets/segment.mp4", "sha256": sha(segment),
            "in_seconds": 0, "duration_seconds": 1,
        }],
        "endcard": {
            "seconds": 0.5, "background": "#171512", "text": text,
            "font": {"path": "assets/font.ttf", "sha256": sha(font)},
            "text_style": {"font_size": 42, "max_width": 620, "y": 760},
        },
        "output": {"path": "outputs/endcard-film.mp4", "receipt": "work/endcard-receipt.json"},
    }
    plan_path = tmp_path / "endcard-plan.json"
    plan_path.write_text(json.dumps(plan))

    receipt = module.assemble(module.load_plan(tmp_path, plan_path))
    output = tmp_path / "outputs" / "endcard-film.mp4"
    persisted = json.loads((tmp_path / "work" / "endcard-receipt.json").read_text())
    assert receipt == persisted
    assert persisted["endcard"] == {"frames": 5, "text": text}
    assert {row["role"] for row in persisted["sources"]} >= {"narration", "segment:opening", "endcard_font"}
    assert next(row["sha256"] for row in persisted["sources"] if row["role"] == "endcard_font") == sha(font)
    assert persisted["output"]["sha256"] == sha(output)


def test_real_ffmpeg_rejects_exact_digital_silence(tmp_path):
    module = load_module()
    ffmpeg, _ = _require_real_tools(module)
    assets = tmp_path / "assets"
    assets.mkdir()
    segment = assets / "segment.mp4"
    narration = assets / "silent.wav"
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=black:s=64x96:r=10:d=1", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(segment),
    ], check=True)
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "anullsrc=r=8000:cl=mono", "-t", "0.5", "-c:a", "pcm_s16le", str(narration),
    ], check=True)
    plan = {
        "schema_version": 1,
        "canvas": {"width": 64, "height": 96, "fps": 10},
        "narration": {"path": "assets/silent.wav", "sha256": sha(narration)},
        "segments": [{
            "source": "assets/segment.mp4", "sha256": sha(segment),
            "in_seconds": 0, "duration_seconds": 1,
        }],
        "output": {"path": "outputs/film.mp4", "receipt": "work/receipt.json"},
    }
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(module.NarratedAssemblyError, match="digital silence"):
        module.load_plan(tmp_path, path)


def test_real_ffmpeg_rejects_narration_that_overruns_picture_by_one_millisecond(tmp_path):
    module = load_module()
    ffmpeg, _ = _require_real_tools(module)
    assets = tmp_path / "assets"
    assets.mkdir()
    segment = assets / "segment.mp4"
    narration = assets / "narration.wav"
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=c=black:s=64x96:r=10:d=1", "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", str(segment),
    ], check=True)
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=8000:duration=1.001", "-c:a", "pcm_s16le",
        str(narration),
    ], check=True)
    plan = {
        "schema_version": 1,
        "canvas": {"width": 64, "height": 96, "fps": 10},
        "narration": {"path": "assets/narration.wav", "sha256": sha(narration)},
        "segments": [{
            "source": "assets/segment.mp4", "sha256": sha(segment),
            "in_seconds": 0, "duration_seconds": 1,
        }],
        "output": {"path": "outputs/film.mp4", "receipt": "work/receipt.json"},
    }
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(module.NarratedAssemblyError, match="narration is longer"):
        module.load_plan(tmp_path, path)


def test_windows_style_pair_keeps_the_executable_suffix(tmp_path, monkeypatch):
    module = load_module()
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    for tool in (ffmpeg, ffprobe):
        tool.write_bytes(b"#!/bin/sh\n")
        tool.chmod(0o755)
    monkeypatch.setenv("NARRATED_FFMPEG", str(ffmpeg))
    assert module.resolve_media_tools() == (str(ffmpeg), str(ffprobe))


def test_publish_falls_back_to_exclusive_create_without_hard_links(tmp_path, monkeypatch):
    module = load_module()
    temporary = tmp_path / "render.tmp"
    temporary.write_bytes(b"film")
    destination = tmp_path / "film.mp4"

    def no_links(src, dst):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(module.os, "link", no_links)
    module._publish_exclusive(temporary, destination)
    assert destination.read_bytes() == b"film"
    with pytest.raises(module.NarratedAssemblyError, match="refusing to overwrite"):
        module._publish_exclusive(temporary, destination)
    assert destination.read_bytes() == b"film"
