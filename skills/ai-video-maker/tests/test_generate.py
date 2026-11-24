"""Tests for generate.py. Every HTTP call is replaced by a fake; nothing reaches a provider."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_KEYS = {
    "REPLICATE_API_TOKEN": "fake-replicate-token-0001",
    "KIE_API_KEY": "fake-kie-key-0002",
    "ELEVENLABS_API_KEY": "fake-elevenlabs-key-0003",
}


def load_module():
    spec = importlib.util.spec_from_file_location("generate", ROOT / "scripts" / "generate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


G = load_module()


class FakeHttp:
    """Stands in for generate.http_request and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers=None, body=None, params=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body,
                           "params": params})
        if not self.responses:
            raise AssertionError(f"unexpected HTTP call: {method} {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, payload = response
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name, value in FAKE_KEYS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(G.time, "sleep", lambda seconds: None)
    (tmp_path / "prompt.txt").write_text("A man in a navy overshirt slowly falls asleep.", encoding="utf-8")
    (tmp_path / "frame.jpg").write_bytes(b"\xff\xd8\xff fake jpeg")
    assert G.main(["init", "--ceiling", "10"]) == 0
    return tmp_path


def fake(monkeypatch, *responses) -> FakeHttp:
    http = FakeHttp(responses)
    monkeypatch.setattr(G, "http_request", http)
    return http


def ledger(project) -> dict:
    return json.loads((project / "ledger.json").read_text())


def assert_no_keys(text: str):
    for value in FAKE_KEYS.values():
        assert value not in text


GROK = ["video", "--route", "grok-video", "--prompt-file", "prompt.txt", "--start-image", "frame.jpg",
        "--seconds", "5", "--label", "scene-01"]


def test_dry_run_prints_the_request_and_sends_nothing(project, monkeypatch, capsys):
    http = fake(monkeypatch)
    assert G.main(GROK + ["--out", "clips/a.mp4", "--dry-run"]) == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert http.calls == []
    assert report["sent"] is False
    assert report["request"]["url"] == "https://api.replicate.com/v1/models/xai/grok-imagine-video/predictions"
    assert report["request"]["headers"]["Authorization"] == "Bearer $REPLICATE_API_TOKEN"
    body = report["request"]["body"]["input"]
    assert body["duration"] == 5 and body["prompt"].startswith("A man in a navy")
    assert body["image"].startswith("data:image/jpeg;base64,<")
    assert report["estimated_cost"] == pytest.approx(0.35)
    assert report["ceiling"]["would_be_refused"] is False
    assert ledger(project)["jobs"] == []
    assert not (project / "clips").exists()
    assert_no_keys(out)


def test_dry_run_needs_no_key_and_no_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    http = fake(monkeypatch)
    code = G.main(["narration", "--voice-id", "voice123", "--text", "Hello there.", "--out", "n.mp3", "--dry-run"])
    assert code == 0 and http.calls == []
    report = json.loads(capsys.readouterr().out)
    assert report["request"]["url"].endswith("/v1/text-to-speech/voice123")
    assert report["request"]["headers"]["xi-api-key"] == "$ELEVENLABS_API_KEY"
    assert "no ledger" in report["ceiling"]


def test_replicate_video_is_submitted_polled_downloaded_and_recorded(project, monkeypatch, capsys):
    http = fake(
        monkeypatch,
        (201, {"id": "pred1", "status": "starting"}),
        (200, {"status": "processing"}),
        (200, {"status": "succeeded", "output": "https://files.example/out.mp4"}),
        (200, b"video-bytes"),
    )
    assert G.main(GROK + ["--out", "clips/a.mp4"]) == 0
    captured = capsys.readouterr()
    assert (project / "clips" / "a.mp4").read_bytes() == b"video-bytes"
    submit, poll, _, download = http.calls
    assert submit["headers"]["Authorization"] == "Bearer " + FAKE_KEYS["REPLICATE_API_TOKEN"]
    assert submit["body"]["input"]["image"].startswith("data:image/jpeg;base64,")
    assert poll["url"] == "https://api.replicate.com/v1/predictions/pred1"
    assert "Authorization" not in download["headers"]
    job = ledger(project)["jobs"][0]
    assert job["status"] == "done" and job["model"] == "xai/grok-imagine-video"
    assert job["seconds"] == 5 and job["estimated_cost"] == pytest.approx(0.35)
    assert job["output"].endswith("clips/a.mp4") and job["provider_job_id"] == "pred1"
    assert_no_keys(captured.out + captured.err + (project / "ledger.json").read_text())


def test_a_job_past_the_ceiling_is_refused_before_any_request(project, monkeypatch, capsys):
    http = fake(monkeypatch)
    code = G.main(["video", "--route", "seedance-2.5-kie", "--prompt", "Two people talk.", "--seconds", "30",
                   "--resolution", "1080p", "--out", "clips/big.mp4"])
    assert code == 3
    assert http.calls == []
    assert ledger(project)["jobs"] == []
    assert "Refused" in capsys.readouterr().err


def test_the_ceiling_counts_earlier_jobs(project, monkeypatch):
    assert G.main(["ceiling", "0.5", "--reason", "test"]) == 0
    fake(monkeypatch, (201, {"id": "p1"}), (200, {"status": "succeeded", "output": ["https://f.example/a.mp4"]}),
         (200, b"x"))
    assert G.main(GROK + ["--out", "a.mp4"]) == 0
    http = fake(monkeypatch)
    assert G.main(GROK + ["--out", "b.mp4", "--reason", "hand passed through the chair"]) == 3
    assert http.calls == [] and len(ledger(project)["jobs"]) == 1


def test_a_retake_of_the_same_label_needs_a_stated_reason(project, monkeypatch, capsys):
    fake(monkeypatch, (201, {"id": "p1"}), (200, {"status": "succeeded", "output": "https://f.example/a.mp4"}),
         (200, b"x"))
    assert G.main(GROK + ["--out", "a.mp4"]) == 0
    http = fake(monkeypatch)
    assert G.main(GROK + ["--out", "b.mp4"]) == 2
    assert http.calls == [] and "what was wrong" in capsys.readouterr().err
    fake(monkeypatch, (201, {"id": "p2"}), (200, {"status": "succeeded", "output": "https://f.example/b.mp4"}),
         (200, b"y"))
    assert G.main(GROK + ["--out", "b.mp4", "--reason", "his eyes stayed open"]) == 0
    assert ledger(project)["jobs"][1]["reason"] == "his eyes stayed open"


def test_kie_seedance_dialogue_request_and_result(project, monkeypatch):
    http = fake(
        monkeypatch,
        (200, {"code": 200, "data": {"taskId": "task9"}}),
        (200, {"code": 200, "data": {"state": "generating"}}),
        (200, {"code": 200, "data": {"state": "success",
                                     "resultJson": json.dumps({"resultUrls": ["https://kie.example/v.mp4"]})}}),
        (200, b"kie-video"),
    )
    code = G.main(["video", "--route", "seedance-2.5-kie", "--prompt", "BOSS: Page six.", "--seconds", "8",
                   "--ref", "https://host.example/cast.jpg", "--ref", "https://host.example/room.jpg",
                   "--voice", "https://host.example/boss.mp3", "--out", "clips/s1.mp4"])
    assert code == 0
    submit, poll = http.calls[0], http.calls[1]
    assert submit["url"] == "https://api.kie.ai/api/v1/jobs/createTask"
    assert submit["headers"]["Authorization"] == "Bearer " + FAKE_KEYS["KIE_API_KEY"]
    assert submit["body"]["model"] == "bytedance/seedance-2-5"
    sent = submit["body"]["input"]
    assert sent["reference_image_urls"] == ["https://host.example/cast.jpg", "https://host.example/room.jpg"]
    assert sent["reference_audio_urls"] == ["https://host.example/boss.mp3"]
    assert sent["duration"] == 8 and sent["resolution"] == "720p" and sent["generate_audio"] is True
    assert "first_frame_url" not in sent
    assert poll["params"] == {"taskId": "task9"}
    assert (project / "clips" / "s1.mp4").read_bytes() == b"kie-video"
    assert ledger(project)["jobs"][0]["estimated_cost"] == pytest.approx(8 * 0.315)


def test_kie_refuses_local_files_and_mixed_inputs(project, monkeypatch, capsys):
    http = fake(monkeypatch)
    base = ["video", "--route", "seedance-2.5-kie", "--prompt", "x", "--seconds", "5", "--out", "c.mp4"]
    assert G.main(base + ["--start-image", "frame.jpg"]) == 2
    assert "https link" in capsys.readouterr().err
    assert G.main(base + ["--start-image", "https://h.example/a.jpg", "--ref", "https://h.example/b.jpg"]) == 2
    assert "cannot combine" in capsys.readouterr().err
    assert http.calls == [] and ledger(project)["jobs"] == []


def test_seedance_with_a_start_frame_uses_the_frame_shape(project, monkeypatch, capsys):
    fake(monkeypatch)
    assert G.main(["video", "--route", "seedance-2.5-replicate", "--prompt", "x", "--seconds", "6",
                   "--start-image", "frame.jpg", "--out", "c.mp4", "--dry-run"]) == 0
    sent = json.loads(capsys.readouterr().out)["request"]["body"]["input"]
    assert sent["aspect_ratio"] == "adaptive" and "image" in sent and sent["generate_audio"] is True


def test_clip_length_limits_are_checked(project, monkeypatch, capsys):
    http = fake(monkeypatch)
    assert G.main(["video", "--route", "grok-video", "--prompt", "x", "--seconds", "20", "--out", "c.mp4"]) == 2
    assert G.main(["video", "--route", "kling-3.0", "--prompt", "x", "--seconds", "7", "--rate", "0.1",
                   "--out", "c.mp4"]) == 2
    assert http.calls == []


def test_a_route_without_a_table_price_needs_a_rate(project, monkeypatch, capsys):
    http = fake(monkeypatch)
    base = ["video", "--route", "kling-3.0", "--prompt", "x", "--start-image", "frame.jpg", "--seconds", "5",
            "--out", "c.mp4", "--dry-run"]
    assert G.main(base) == 2
    assert "--rate" in capsys.readouterr().err
    assert G.main(base + ["--rate", "0.1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["estimated_cost"] == pytest.approx(0.5)
    assert report["request"]["body"]["input"]["mode"] == "standard"
    assert report["request"]["url"].endswith("/models/kwaivgi/kling-v3-video/predictions")
    assert http.calls == []


def test_image_with_references(project, monkeypatch):
    http = fake(monkeypatch, (201, {"id": "img1"}),
                (200, {"status": "succeeded", "output": ["https://f.example/i.jpg"]}), (200, b"jpeg"))
    assert G.main(["image", "--prompt", "Image 1 is the cast. Image 2 is the room.", "--ref", "frame.jpg",
                   "--ref", "https://h.example/room.jpg", "--aspect-ratio", "9:16", "--label", "panel-01",
                   "--out", "storyboard/01.jpg"]) == 0
    sent = http.calls[0]["body"]["input"]
    assert http.calls[0]["url"].endswith("/models/google/nano-banana-pro/predictions")
    assert sent["image_input"][0].startswith("data:image/jpeg;base64,")
    assert sent["image_input"][1] == "https://h.example/room.jpg" and sent["aspect_ratio"] == "9:16"
    job = ledger(project)["jobs"][0]
    assert job["kind"] == "image" and job["estimated_cost"] == pytest.approx(0.15)
    assert "not re-checked" in job["price_source"]


def test_narration_is_priced_by_characters_and_saved(project, monkeypatch, capsys):
    text = "My daughter saw me fall asleep. " * 10
    (project / "narration.txt").write_text(text, encoding="utf-8")
    http = fake(monkeypatch, (200, b"mp3-bytes"))
    assert G.main(["narration", "--voice-id", "voiceA", "--text-file", "narration.txt",
                   "--out", "assets/narration.mp3"]) == 0
    call = http.calls[0]
    assert call["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voiceA"
    assert call["headers"]["xi-api-key"] == FAKE_KEYS["ELEVENLABS_API_KEY"]
    assert call["body"] == {"text": text.strip(), "model_id": "eleven_v4"}
    assert call["params"] == {"output_format": "mp3_44100_128"}
    assert (project / "assets" / "narration.mp3").read_bytes() == b"mp3-bytes"
    job = ledger(project)["jobs"][0]
    assert job["characters"] == len(text.strip())
    assert job["estimated_cost"] == pytest.approx(round(len(text.strip()) / 1000 * 0.08, 4))
    captured = capsys.readouterr()
    assert_no_keys(captured.out + captured.err)


def test_a_rejected_job_is_recorded_but_not_counted_and_the_key_is_scrubbed(project, monkeypatch, capsys):
    echoed = "bad token " + FAKE_KEYS["REPLICATE_API_TOKEN"]
    fake(monkeypatch, (401, {"detail": echoed}))
    assert G.main(GROK + ["--out", "a.mp4"]) == 5
    captured = capsys.readouterr()
    job = ledger(project)["jobs"][0]
    assert job["status"] == "rejected" and G.committed(ledger(project)) == 0
    assert_no_keys(captured.out + captured.err + (project / "ledger.json").read_text())
    assert "[key removed]" in job["error"]


def test_a_dropped_connection_on_submit_still_counts(project, monkeypatch, capsys):
    fake(monkeypatch, OSError("connection reset"))
    assert G.main(GROK + ["--out", "a.mp4"]) == 4
    job = ledger(project)["jobs"][0]
    assert job["status"] == "unknown" and G.committed(ledger(project)) == pytest.approx(0.35)
    assert "dashboard" in capsys.readouterr().err


def test_a_failed_job_is_recorded_and_not_counted(project, monkeypatch):
    fake(monkeypatch, (201, {"id": "p1"}), (200, {"status": "failed", "error": "content flagged"}))
    assert G.main(GROK + ["--out", "a.mp4"]) == 5
    job = ledger(project)["jobs"][0]
    assert job["status"] == "failed" and job["error"] == "content flagged"
    assert G.committed(ledger(project)) == 0 and not (project / "a.mp4").exists()


def test_timeout_leaves_the_job_open_and_resume_collects_without_resubmitting(project, monkeypatch, capsys):
    fake(monkeypatch, (201, {"id": "p1"}), (200, {"status": "processing"}))
    assert G.main(GROK + ["--out", "a.mp4", "--wait", "-1"]) == 4
    assert "resume job-0001" in capsys.readouterr().err
    assert ledger(project)["jobs"][0]["status"] == "submitted"
    http = fake(monkeypatch, (200, {"status": "succeeded", "output": "https://f.example/a.mp4"}), (200, b"late"))
    assert G.main(["resume", "job-0001"]) == 0
    assert [c["method"] for c in http.calls] == ["GET", "GET"]
    assert (project / "a.mp4").read_bytes() == b"late"
    assert len(ledger(project)["jobs"]) == 1 and ledger(project)["jobs"][0]["status"] == "done"


def test_missing_key_stops_before_the_ledger_and_the_network(project, monkeypatch, capsys):
    monkeypatch.delenv("REPLICATE_API_TOKEN")
    http = fake(monkeypatch)
    assert G.main(GROK + ["--out", "a.mp4"]) == 2
    assert "REPLICATE_API_TOKEN is not set" in capsys.readouterr().err
    assert http.calls == [] and ledger(project)["jobs"] == []


def test_an_existing_output_is_never_overwritten(project, monkeypatch):
    (project / "a.mp4").write_bytes(b"keep me")
    http = fake(monkeypatch)
    assert G.main(GROK + ["--out", "a.mp4"]) == 2
    assert http.calls == [] and (project / "a.mp4").read_bytes() == b"keep me"


def test_no_job_runs_without_a_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", FAKE_KEYS["ELEVENLABS_API_KEY"])
    http = fake(monkeypatch)
    assert G.main(["narration", "--voice-id", "v", "--text", "Hi.", "--out", "n.mp3"]) == 2
    assert "init --ceiling" in capsys.readouterr().err and http.calls == []


def test_status_and_ceiling_change(project, monkeypatch, capsys):
    capsys.readouterr()
    assert G.main(["ceiling", "25", "--reason", "plan total with retakes"]) == 0
    capsys.readouterr()
    assert G.main(["status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ceiling"] == 25 and report["committed"] == 0 and report["remaining"] == 25
    assert len(ledger(project)["ceiling_history"]) == 2
    assert G.main(["init", "--ceiling", "5"]) == 2


def test_every_route_has_a_price_row_or_is_documented_as_needing_a_rate():
    table = json.loads((ROOT / "data" / "models.json").read_text())
    ids = {m["id"] for m in table["models"]}
    for name, route in G.ROUTES.items():
        if route["price_id"] is None:
            assert name == "kling-3.0"
        else:
            assert route["price_id"] in ids, name
