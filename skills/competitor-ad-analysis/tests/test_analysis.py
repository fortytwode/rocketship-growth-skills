import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("competitor_ad_analysis", SCRIPTS / "analyze.py")
analysis = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analysis
SPEC.loader.exec_module(analysis)
REAL_RESOLVE_MODEL = analysis.resolve_model


@pytest.fixture(autouse=True)
def no_network_model_check(monkeypatch):
    # run() confirms the model with the provider before any media call. Tests
    # use a placeholder key, so that lookup must never leave the machine.
    monkeypatch.setattr(analysis, "resolve_model",
                        lambda client, requested: (requested or analysis.MODEL_DEFAULT, ""))
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


def manifest(tmp_path, paths, ids=None):
    ids = ids or [f"ad-{i}" for i in range(len(paths))]
    rows = [{"ad_id": aid, "media_file": path, "ad_copy": f"Copy {aid}"}
            for aid, path in zip(ids, paths)]
    source = tmp_path / "manifest.json"
    source.write_text(json.dumps({"ads": rows}), encoding="utf-8")
    return source


def small_image(path, color=(30, 60, 90)):
    Image.new("RGB", (3, 2), color).save(path, format="PNG")


def valid_analysis():
    return analysis.AdAnalysis(
        summary="Observed summary", hook="Opening", mechanic="Demonstration", setting="Home",
        emotion="Curiosity", emotion_evidence="Question in copy", product_role="Shown",
        payoff="Result shown", cta="Not shown", spoken_words="None", overlay_text="None",
        limitations=[], beats=[],
    )


def run_manifest(source, out, *, model=None, dry=False, skip_cluster=True, client=None, unique_limit=None, limit=20):
    args = analysis.parser().parse_args([
        "--manifest", str(source), "--out", str(out), "--limit", str(limit),
        *( ["--model", model] if model else []),
        *( ["--unique-limit", str(unique_limit)] if unique_limit is not None else []),
        *( ["--dry-run"] if dry else []),
        *( ["--skip-cluster"] if skip_cluster else []),
    ])
    return analysis.run(args, client=client)


def capture_file(tmp_path, rows):
    source = tmp_path / "capture.json"
    source.write_text(json.dumps({"ads": rows}), encoding="utf-8")
    return source


def run_capture(source, out, *, limit=20, unique_limit=None):
    args = analysis.parser().parse_args([
        "--capture", str(source), "--out", str(out), "--limit", str(limit), "--skip-cluster",
        *( ["--unique-limit", str(unique_limit)] if unique_limit is not None else []),
    ])
    return analysis.run(args)


def fake_capture_download(calls, color=(30, 60, 90)):
    def download(row, dest):
        calls.append(row.get("_image_url"))
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "creative.jpg"
        Image.new("RGB", (3, 2), color).save(path, format="JPEG")
        return {"media_file": str(path.resolve()), "media_type": "IMAGE", "snapshot_card_count": 0}
    return download


def test_exact_duplicate_media_analyzed_once_and_retained_per_ad(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    source_file = tmp_path / "asset.png"
    small_image(source_file)
    source = manifest(tmp_path, ["asset.png", "asset.png"], ids=["first", "second"])
    calls = []

    def fake_analyze(*args, **kwargs):
        calls.append(args[1])
        return valid_analysis()

    monkeypatch.setattr(analysis, "analyze_media", fake_analyze)
    out = tmp_path / "out"
    assert run_manifest(source, out) == 0
    result = json.loads((out / "analysis.json").read_text())
    assert len(calls) == 1
    assert [row["ad_id"] for row in result["ads"]] == ["first", "second"]
    assert result["coverage"] == {"selected": 2, "reviewed": 2, "unavailable": 0,
                                  "unique_executions": 1, "ranking_verified": False, "failed": 0, "not_attempted": 0}


def test_rerun_cache_and_model_change(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    small_image(tmp_path / "asset.png")
    source = manifest(tmp_path, ["asset.png"])
    calls = []
    monkeypatch.setattr(analysis, "analyze_media", lambda *args, **kwargs: (calls.append(args[4].name) or valid_analysis()))
    out = tmp_path / "out"
    assert run_manifest(source, out, model="model-a") == 0
    assert run_manifest(source, out, model="model-a") == 0
    assert calls == ["model-a"]
    assert run_manifest(source, out, model="model-b") == 0
    assert calls == ["model-a", "model-b"]


def test_unsafe_local_manifest_path_rejected_before_media_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    source = manifest(tmp_path, ["../outside.png"])
    calls = []
    monkeypatch.setattr(analysis, "analyze_media", lambda *args, **kwargs: calls.append(args))
    out = tmp_path / "out"
    assert run_manifest(source, out) == 1
    result = json.loads((out / "analysis.json").read_text())
    assert calls == []
    assert result["ads"][0]["status"] == "unavailable"


def test_provider_429_stops_calls_but_accounts_for_all_selected_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    small_image(tmp_path / "a.png")
    small_image(tmp_path / "b.png", (80, 20, 10))
    source = manifest(tmp_path, ["a.png", "b.png"], ids=["one", "two"])
    calls = []

    def raise_quota(*args, **kwargs):
        calls.append(args[1])
        raise RuntimeError("provider failed") if False else type("ProviderError", (Exception,), {"code": 429})("quota")

    monkeypatch.setattr(analysis, "analyze_media", raise_quota)
    out = tmp_path / "out"
    assert run_manifest(source, out) == 1
    result = json.loads((out / "analysis.json").read_text())
    assert len(calls) == 1
    assert [row["ad_id"] for row in result["ads"]] == ["one", "two"]
    assert result["coverage"]["selected"] == 2
    assert result["coverage"]["unavailable"] == 2
    assert "Not attempted" in result["ads"][1]["error"]


@pytest.mark.parametrize("families", [
    [{"name": "x", "ad_ids": ["a", "a"]}],
    [{"name": "x", "ad_ids": ["a"]}],
])
def test_family_assignments_reject_duplicate_or_missing_ids(families):
    with pytest.raises(ValueError, match="exactly once"):
        analysis.validate_families(families, ["a", "b"])


def test_timestamp_nan_reversed_and_out_of_duration_rejected():
    beat_values = [float("nan"), 3]
    with pytest.raises(ValueError):
        analysis.Beat(start_seconds=beat_values[0], end_seconds=4, role="x", observation="x", dialogue="x", overlay="x")
    valid = valid_analysis()
    valid.beats = [analysis.Beat(start_seconds=3, end_seconds=2, role="x", observation="x", dialogue="x", overlay="x")]
    with pytest.raises(ValueError, match="reversed"):
        analysis.validate_analysis(valid, 4)
    valid.beats = [analysis.Beat(start_seconds=0, end_seconds=8, role="x", observation="x", dialogue="x", overlay="x")]
    with pytest.raises(ValueError, match="duration"):
        analysis.validate_analysis(valid, 4)


def test_dry_run_needs_no_dotenv_key_and_makes_no_network_calls(monkeypatch, tmp_path, capsys):
    source = manifest(tmp_path, ["not-downloaded.png"])
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(analysis, "read_config", lambda *_: pytest.fail("dry-run must not read config"))
    monkeypatch.setattr(analysis, "download_asset", lambda *_: pytest.fail("dry-run must not download"))
    monkeypatch.setattr(analysis, "analyze_media", lambda *_: pytest.fail("dry-run must not call model"))
    assert run_manifest(source, tmp_path / "out", dry=True) == 0
    assert json.loads(capsys.readouterr().out)["selected_records"] == 1


def test_capture_resume_reuses_verified_local_asset_if_download_later_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    source = capture_file(tmp_path, [{"ad_id": "resume", "_image_url": "https://scontent.fbcdn.net/asset.jpg"}])
    out = tmp_path / "out"
    downloads, analyses = [], []
    monkeypatch.setattr(analysis, "download_asset", fake_capture_download(downloads))
    monkeypatch.setattr(analysis, "analyze_media", lambda *args, **kwargs: (analyses.append(args[1]) or valid_analysis()))
    assert run_capture(source, out) == 0
    monkeypatch.setattr(analysis, "download_asset", lambda *_: pytest.fail("matching verified receipt should avoid downloader"))
    assert run_capture(source, out) == 0
    result = json.loads((out / "analysis.json").read_text())
    assert downloads == ["https://scontent.fbcdn.net/asset.jpg"]
    assert len(analyses) == 1
    assert result["ads"][0]["status"] == "reviewed"


def test_changed_capture_url_invalidates_asset_receipt_and_redownloads(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    rows = [{"ad_id": "url-change", "_image_url": "https://scontent.fbcdn.net/first.jpg"}]
    source = capture_file(tmp_path, rows)
    out = tmp_path / "out"
    downloads, analyses = [], []
    monkeypatch.setattr(analysis, "download_asset", fake_capture_download(downloads, (10, 20, 30)))
    monkeypatch.setattr(analysis, "analyze_media", lambda *args, **kwargs: (analyses.append(args[1]) or valid_analysis()))
    assert run_capture(source, out) == 0
    rows[0]["_image_url"] = "https://scontent.fbcdn.net/second.jpg"
    source.write_text(json.dumps({"ads": rows}), encoding="utf-8")
    monkeypatch.setattr(analysis, "download_asset", fake_capture_download(downloads, (80, 90, 100)))
    assert run_capture(source, out) == 0
    assert downloads == ["https://scontent.fbcdn.net/first.jpg", "https://scontent.fbcdn.net/second.jpg"]
    assert len(analyses) == 2


def test_tampered_capture_asset_bytes_invalidate_receipt_and_redownload(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    source = capture_file(tmp_path, [{"ad_id": "tampered", "_image_url": "https://scontent.fbcdn.net/asset.jpg"}])
    out = tmp_path / "out"
    downloads = []
    monkeypatch.setattr(analysis, "download_asset", fake_capture_download(downloads))
    monkeypatch.setattr(analysis, "analyze_media", lambda *args, **kwargs: valid_analysis())
    assert run_capture(source, out) == 0
    asset_path = next((out / "media").glob("*/creative.jpg"))
    Image.new("RGB", (3, 2), (250, 1, 2)).save(asset_path, format="JPEG")
    assert run_capture(source, out) == 0
    assert downloads == ["https://scontent.fbcdn.net/asset.jpg"] * 2


def test_unique_limit_counts_distinct_reviewed_media_and_stops_after_target(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    rows = [
        {"ad_id": "one", "_image_url": "https://scontent.fbcdn.net/shared-a.jpg"},
        {"ad_id": "two", "_image_url": "https://scontent.fbcdn.net/shared-b.jpg"},
        {"ad_id": "three", "_image_url": "https://scontent.fbcdn.net/distinct.jpg"},
        {"ad_id": "four", "_image_url": "https://scontent.fbcdn.net/never.jpg"},
    ]
    source = capture_file(tmp_path, rows)
    downloads, analyses = [], []

    def download(row, dest):
        downloads.append(row["_image_url"])
        color = (1, 2, 3) if row["ad_id"] in {"one", "two"} else (100, 110, 120)
        return fake_capture_download([], color)(row, dest)

    monkeypatch.setattr(analysis, "download_asset", download)
    monkeypatch.setattr(analysis, "analyze_media", lambda *args, **kwargs: (analyses.append(args[1]) or valid_analysis()))
    out = tmp_path / "out"
    assert run_capture(source, out, limit=4, unique_limit=2) == 0
    result = json.loads((out / "analysis.json").read_text())
    assert len(result["ads"]) == 3
    assert [row["ad_id"] for row in result["ads"]] == ["one", "two", "three"]
    assert result["coverage"]["unique_executions"] == 2
    assert result["capture"]["stopped_at_unique_target"] is True
    assert "never.jpg" not in downloads
    assert len(analyses) == 2


def test_unique_target_shortfall_is_reported_explicitly(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    rows = [{"ad_id": f"copy-{i}", "_image_url": f"https://scontent.fbcdn.net/copy-{i}.jpg"} for i in range(3)]
    source = capture_file(tmp_path, rows)
    monkeypatch.setattr(analysis, "download_asset", fake_capture_download([], (20, 30, 40)))
    monkeypatch.setattr(analysis, "analyze_media", lambda *args, **kwargs: valid_analysis())
    out = tmp_path / "out"
    assert run_capture(source, out, limit=3, unique_limit=2) == 0
    result = json.loads((out / "analysis.json").read_text())
    assert result["coverage"]["unique_executions"] == 1
    assert any("did not yield the requested 2 distinct reviewed executions" in item for item in result["limitations"])


def test_gemini_wire_schema_strips_nested_additional_properties_but_local_validation_stays_strict(tmp_path):
    class FakeModels:
        config = None

        def generate_content(self, **kwargs):
            self.config = kwargs["config"]
            return SimpleNamespace(text=json.dumps({
                **valid_analysis().model_dump(),
                "beats": [{"start_seconds": 0, "end_seconds": 0, "role": "hook",
                           "observation": "Visible", "dialogue": "", "overlay": "", "unexpected": "field"}],
            }), usage_metadata=None)

    client = SimpleNamespace(models=FakeModels())
    source_schema = analysis.AdAnalysis.model_json_schema()

    def contains_additional_properties(value):
        if isinstance(value, dict):
            return "additionalProperties" in value or any(contains_additional_properties(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_additional_properties(item) for item in value)
        return False

    assert contains_additional_properties(source_schema)
    with pytest.raises(ValueError, match="extra"):
        analysis.generate(client, analysis.RunModel("example-model"), "example input", analysis.AdAnalysis, tmp_path, "media")
    wire_schema = client.models.config.response_schema
    assert not contains_additional_properties(wire_schema)


def test_all_zero_video_timings_are_not_accepted_as_a_storyboard():
    result = valid_analysis()
    result.beats = [analysis.Beat(start_seconds=0, end_seconds=0, role='hook',
                                 observation='Visible scene', dialogue='', overlay='')]
    with pytest.raises(ValueError, match='all zero'):
        analysis.validate_analysis(result, 86.12)
    assert analysis.validate_analysis(result, 0) is result


def test_elapsed_video_seconds_remain_valid():
    result = valid_analysis()
    result.beats = [analysis.Beat(start_seconds=74, end_seconds=149, role='practice',
                                 observation='Visible scene', dialogue='', overlay='')]
    assert analysis.validate_analysis(result, 174.08) is result
    result.beats[0].end_seconds = 229
    with pytest.raises(ValueError, match='exceeds'):
        analysis.validate_analysis(result, 174.08)


class _ProviderError(Exception):
    def __init__(self, code, status=''):
        super().__init__('raw provider text that must not be shown: key=SECRET')
        self.code, self.status = code, status


def _models_client(available):
    def get(model):
        if model not in available:
            raise _ProviderError(404, 'NOT_FOUND')
        return SimpleNamespace(name=model)
    return SimpleNamespace(models=SimpleNamespace(get=get))


def test_default_model_falls_back_when_unavailable_to_the_key():
    model, note = REAL_RESOLVE_MODEL(_models_client({'gemini-2.5-flash'}), '')
    assert model == 'gemini-2.5-flash'
    assert analysis.MODEL_DEFAULT in note and 'gemini-2.5-flash' in note
    assert REAL_RESOLVE_MODEL(_models_client({analysis.MODEL_DEFAULT}), '') == (analysis.MODEL_DEFAULT, '')


def test_user_chosen_model_is_never_swapped():
    with pytest.raises(_ProviderError):
        REAL_RESOLVE_MODEL(_models_client({analysis.MODEL_DEFAULT}), 'gemini-2.5-flash')


def test_provider_error_shows_code_and_hint_but_not_raw_text():
    text = analysis.provider_error(_ProviderError(404, 'NOT_FOUND'))
    assert text.startswith('HTTP 404 NOT_FOUND') and 'GEMINI_MODEL' in text
    assert 'SECRET' not in text
    assert 'SECRET' not in analysis.provider_error(_ProviderError(503, 'bad status: key=SECRET'))
    assert 'capacity or network' in analysis.provider_error(type('ReadTimeout', (Exception,), {})())


def test_generate_retries_temporary_overload_then_succeeds(tmp_path, monkeypatch):
    calls, sleeps = [], []
    def generate_content(**kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise _ProviderError(503, 'UNAVAILABLE')
        return SimpleNamespace(text=valid_analysis().model_dump_json(), usage_metadata=None)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    monkeypatch.setattr(analysis.time, 'sleep', sleeps.append)
    result = analysis.generate(client, analysis.RunModel('m'), ['x'], analysis.AdAnalysis, tmp_path, 'media')
    assert result.summary == 'Observed summary'
    assert len(calls) == 3 and sleeps == list(analysis.RETRY_DELAYS_SECONDS[:2])


def test_generate_does_not_retry_a_missing_model(tmp_path, monkeypatch):
    calls = []
    def generate_content(**kwargs):
        calls.append(1)
        raise _ProviderError(404, 'NOT_FOUND')
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    monkeypatch.setattr(analysis.time, 'sleep', lambda s: None)
    with pytest.raises(_ProviderError):
        analysis.generate(client, analysis.RunModel('m'), ['x'], analysis.AdAnalysis, tmp_path, 'media')
    assert len(calls) == 1


def test_timeout_defaults_to_ten_minutes_and_is_bounded(tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_TIMEOUT_SECONDS', raising=False)
    assert analysis.read_timeout(tmp_path / '.env') == 600
    assert analysis.read_timeout(tmp_path / '.env', 900) == 900
    with pytest.raises(ValueError):
        analysis.read_timeout(tmp_path / '.env', 5)


def test_active_only_changes_the_ad_library_url():
    import capture_engine
    assert 'active_status=all' in capture_engine._make_ad_library_url('123', 'impressions')
    assert 'active_status=active' in capture_engine._make_ad_library_url('123', 'impressions', active_only=True)


def test_unavailable_model_stops_before_any_media_call(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    image = tmp_path / "a.png"
    small_image(image)
    source = manifest(tmp_path, ["a.png"])
    def unavailable(client, requested):
        raise _ProviderError(404, 'NOT_FOUND')
    monkeypatch.setattr(analysis, "resolve_model", unavailable)
    monkeypatch.setattr(analysis, "analyze_media", lambda *a, **k: pytest.fail("no media call after a failed model check"))
    out = tmp_path / "out"
    assert run_manifest(source, out) == 1
    printed = capsys.readouterr().out
    assert "HTTP 404 NOT_FOUND" in printed and "SECRET" not in printed
    result = json.loads((out / "analysis.json").read_text())
    assert any("HTTP 404" in line for line in result["limitations"])


def test_media_download_error_is_not_described_as_a_model_error():
    import urllib.error
    exc = urllib.error.HTTPError('https://example.invalid/a.mp4?sig=SECRET', 403, 'Forbidden', None, None)
    text = analysis.provider_error(exc)
    assert text.startswith('Media download failed (HTTP 403)')
    assert 'API key' not in text and 'SECRET' not in text


def test_stalled_processing_and_network_failures_get_their_own_hint():
    assert 'timeout-seconds' not in analysis.provider_error(analysis.ProcessingStalled('x'))
    assert 'network' in analysis.provider_error(type('ConnectError', (Exception,), {})())


def test_zero_timeout_is_rejected_not_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_TIMEOUT_SECONDS', raising=False)
    with pytest.raises(ValueError):
        analysis.read_timeout(tmp_path / '.env', 0)
    monkeypatch.setenv('GEMINI_TIMEOUT_SECONDS', '0')
    with pytest.raises(ValueError):
        analysis.read_timeout(tmp_path / '.env')


def test_failed_model_check_accounts_for_every_selected_ad(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    small_image(tmp_path / "a.png")
    small_image(tmp_path / "b.png", (80, 20, 10))
    source = manifest(tmp_path, ["a.png", "b.png"], ids=["one", "two"])
    def unavailable(client, requested):
        raise _ProviderError(404, 'NOT_FOUND')
    monkeypatch.setattr(analysis, "resolve_model", unavailable)
    out = tmp_path / "out"
    assert run_manifest(source, out) == 1
    result = json.loads((out / "analysis.json").read_text())
    assert [ad["ad_id"] for ad in result["ads"]] == ["one", "two"]
    assert all("Not attempted" in ad["error"] for ad in result["ads"])


def test_offline_model_check_does_not_block_a_cached_rerun(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    small_image(tmp_path / "a.png")
    source = manifest(tmp_path, ["a.png"])
    monkeypatch.setattr(analysis, "analyze_media", lambda *a, **k: valid_analysis())
    out = tmp_path / "out"
    assert run_manifest(source, out) == 0
    def offline(client, requested):
        raise type('ConnectError', (Exception,), {})('no route')
    monkeypatch.setattr(analysis, "resolve_model", offline)
    monkeypatch.setattr(analysis, "analyze_media", lambda *a, **k: pytest.fail("cached read must be reused"))
    assert run_manifest(source, out) == 0

@pytest.mark.parametrize('failure', [_ProviderError(503, 'UNAVAILABLE'), type('ReadTimeout', (Exception,), {})('private')])
def test_provider_outage_blocks_new_calls_but_restores_later_cache(monkeypatch, tmp_path, failure):
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    for name, color in [('a', (10, 20, 30)), ('b', (40, 50, 60)), ('c', (70, 80, 90))]:
        small_image(tmp_path / f'{name}.png', color)
    source = manifest(tmp_path, ['c.png'], ids=['cached'])
    out = tmp_path / 'out'
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: valid_analysis())
    assert run_manifest(source, out) == 0
    source = manifest(tmp_path, ['a.png', 'b.png', 'c.png'], ids=['failed', 'new', 'cached'])
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise failure
    monkeypatch.setattr(analysis, 'analyze_media', fail)
    monkeypatch.setattr(analysis, 'generate', lambda *a, **k: pytest.fail('no new family generation during outage'))
    assert run_manifest(source, out, skip_cluster=False) == 1
    result = json.loads((out / 'analysis.json').read_text())
    assert len(calls) == 1
    assert [a['analysis_outcome'] for a in result['ads']] == ['failed', 'not_attempted', 'reviewed']
    assert result['run_outcome'] == 'partial'
    assert result['coverage']['failed'] == 1 and result['coverage']['not_attempted'] == 1
    assert result['coverage']['reviewed'] == 1
    assert 'not_attempted' in (out / 'index.html').read_text()


def test_cdn_http_error_does_not_stop_next_media(monkeypatch, tmp_path):
    import urllib.error
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    source = capture_file(tmp_path, [{'ad_id': 'bad'}, {'ad_id': 'good'}])
    def download(row, dest):
        if row['ad_id'] == 'bad':
            raise urllib.error.HTTPError('https://example.invalid/?sig=SECRET', 403, 'Forbidden', None, None)
        return fake_capture_download([])(row, dest)
    monkeypatch.setattr(analysis, 'download_asset', download)
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: valid_analysis())
    out = tmp_path / 'out'
    assert run_capture(source, out) == 1
    result = json.loads((out / 'analysis.json').read_text())
    assert [a['status'] for a in result['ads']] == ['unavailable', 'reviewed']
    assert 'SECRET' not in (out / 'analysis.json').read_text()


def test_analysis_client_disables_sdk_retries(monkeypatch, tmp_path):
    from google import genai
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    small_image(tmp_path / 'a.png')
    source = manifest(tmp_path, ['a.png'])
    constructors = []
    def client(**kwargs):
        constructors.append(kwargs)
        return SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(genai, 'Client', client)
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: valid_analysis())
    assert run_manifest(source, tmp_path / 'out') == 0
    assert constructors[0]['http_options'].retry_options.attempts == 1
    assert constructors[0]['http_options'].timeout == 600000


def test_exhausted_generate_has_four_attempts_and_no_timeout_retry(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(analysis.time, 'sleep', lambda _: None)
    def fail(**kwargs):
        calls.append(1)
        raise _ProviderError(503, 'UNAVAILABLE')
    client = SimpleNamespace(models=SimpleNamespace(generate_content=fail))
    with pytest.raises(_ProviderError):
        analysis.generate(client, analysis.RunModel('m'), 'x', analysis.AdAnalysis, tmp_path, 'media')
    assert len(calls) == 4
    calls.clear()
    def timeout(**kwargs):
        calls.append(1)
        raise type('ReadTimeout', (Exception,), {})()
    client.models.generate_content = timeout
    with pytest.raises(Exception):
        analysis.generate(client, analysis.RunModel('m'), 'x', analysis.AdAnalysis, tmp_path, 'media')
    assert len(calls) == 1


@pytest.mark.parametrize("automatic,alternate_available,code", [
    (False, True, 503),
    (True, False, 503),
    (True, True, 503),
    (True, True, 429),
])
def test_failover_respects_selection_and_request_budget(monkeypatch, tmp_path, automatic, alternate_available, code):
    primary, alternate = analysis.MODEL_DEFAULT, analysis.OVERLOAD_ALTERNATE
    attempts, checks = [], []

    def get(model, config):
        checks.append(model)
        assert config.http_options.timeout == 5000
        assert config.http_options.retry_options.attempts == 1
        if not alternate_available:
            raise _ProviderError(404, 'NOT_FOUND')

    def generate_content(model, **kwargs):
        attempts.append(model)
        raise _ProviderError(code)

    client = SimpleNamespace(models=SimpleNamespace(get=get, generate_content=generate_content))
    monkeypatch.setattr(analysis.time, 'sleep', lambda _: None)
    model = analysis.RunModel(primary, automatic=automatic)
    with pytest.raises(_ProviderError):
        analysis.generate(client, model, 'input', analysis.AdAnalysis, tmp_path, 'media')
    eligible = automatic and code == 503
    assert checks == ([alternate] if eligible else [])
    if eligible and alternate_available:
        assert attempts == [primary, primary, alternate, alternate]
    else:
        assert attempts == [primary] * 4


def test_failover_finishes_cohort_and_reuses_mixed_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "example-test-key")
    monkeypatch.setattr(analysis.time, 'sleep', lambda _: None)
    primary, alternate = analysis.MODEL_DEFAULT, analysis.OVERLOAD_ALTERNATE
    images = [f'{i}.png' for i in range(30)]
    for i, name in enumerate(images):
        small_image(tmp_path / name, (i, 60, 90))
    source = manifest(tmp_path, images)
    out = tmp_path / 'out'
    attempts, checks, uploads, deletions = [], [], [], []
    families = analysis.Families(families=[analysis.Family(
        name='Demonstration', definition='Repeated demonstration',
        ad_ids=[f'ad-{i}' for i in range(30)], shared_structure=[], exceptions=[],
    )])

    def upload(**kwargs):
        name = f'files/ad-{len(uploads)}'
        uploads.append(name)
        return SimpleNamespace(name=name, state='ACTIVE', uri=f'https://example.invalid/{name}')

    def generate_content(model, contents, **kwargs):
        media = contents[0].file_data.file_uri if isinstance(contents, list) else None
        attempts.append((model, media))
        if model == primary and len(uploads) == 2:
            raise _ProviderError(503, 'UNAVAILABLE')
        result = valid_analysis() if media else families
        return SimpleNamespace(text=result.model_dump_json(), usage_metadata=None)

    client = SimpleNamespace(models=SimpleNamespace(get=lambda model, config: checks.append(model),
                                                    generate_content=generate_content),
                             files=SimpleNamespace(upload=upload, delete=lambda name: deletions.append(name)))
    assert run_manifest(source, out, skip_cluster=False, limit=30, client=client) == 0
    result = json.loads((out / 'analysis.json').read_text())
    assert result['run_outcome'] == 'complete'
    assert result['coverage']['reviewed'] == 30
    assert result['coverage']['failed'] == result['coverage']['not_attempted'] == 0
    assert len(uploads) == 30 and deletions == uploads
    assert checks == [alternate]
    second_file = 'https://example.invalid/files/ad-1'
    assert attempts[1:4] == [(primary, second_file), (primary, second_file), (alternate, second_file)]
    assert all(name == alternate for name, _ in attempts[4:])
    assert attempts[-1] == (alternate, None)
    assert [ad['analysis_model'] for ad in result['ads']] == [primary] + [alternate] * 29
    assert result['family_model'] == alternate
    assert (primary + ', ' + alternate) in (out / 'index.html').read_text()

    def offline(*args):
        raise analysis.httpx.ReadTimeout('offline')

    monkeypatch.setattr(analysis, 'resolve_model', offline)
    assert run_manifest(source, out, skip_cluster=False, limit=30) == 0
    resumed = json.loads((out / 'analysis.json').read_text())
    assert resumed['ads'] == result['ads']
    assert resumed['families'] == result['families'] and resumed['family_model'] == alternate


def test_failed_metadata_check_restores_cache_without_new_generation(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    small_image(tmp_path / 'a.png')
    source = manifest(tmp_path, ['a.png'])
    out = tmp_path / 'out'
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: valid_analysis())
    assert run_manifest(source, out) == 0
    def unavailable(*args):
        raise _ProviderError(503, 'UNAVAILABLE')
    monkeypatch.setattr(analysis, 'resolve_model', unavailable)
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: pytest.fail('cache only'))
    assert run_manifest(source, out) == 1
    result = json.loads((out / 'analysis.json').read_text())
    assert result['ads'][0]['status'] == 'reviewed'
    assert result['run_outcome'] == 'partial'


@pytest.mark.parametrize('transport_error', ['ReadError', 'WriteError', 'RemoteProtocolError'])
def test_real_httpx_read_error_stops_new_media_and_family_calls(monkeypatch, tmp_path, transport_error):
    import httpx
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    small_image(tmp_path / 'a.png')
    small_image(tmp_path / 'b.png', (150, 60, 30))
    source = manifest(tmp_path, ['a.png', 'b.png'])
    calls = []
    def analyze(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise getattr(httpx, transport_error)('private details')
        return valid_analysis()
    monkeypatch.setattr(analysis, 'analyze_media', analyze)
    monkeypatch.setattr(analysis, 'generate', lambda *a, **k: pytest.fail('no new family generation'))
    out = tmp_path / 'out'
    assert run_manifest(source, out, skip_cluster=False) == 1
    result = json.loads((out / 'analysis.json').read_text())
    assert len(calls) == 1
    assert [a['analysis_outcome'] for a in result['ads']] == ['failed', 'not_attempted']


def test_offline_rerun_restores_cached_families_without_generation(monkeypatch, tmp_path):
    import httpx
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    small_image(tmp_path / 'a.png')
    source = manifest(tmp_path, ['a.png', 'a.png'], ids=['one', 'two'])
    out = tmp_path / 'out'
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: valid_analysis())
    families = [{'name': 'Demo', 'definition': 'Observed', 'ad_ids': ['one', 'two'],
                 'shared_structure': [], 'exceptions': []}]
    monkeypatch.setattr(analysis, 'generate', lambda *a, **k: analysis.Families(families=families))
    assert run_manifest(source, out, skip_cluster=False) == 0
    assert json.loads((out / 'analysis.json').read_text())['families'] == families
    def offline(*args):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(analysis, 'resolve_model', offline)
    monkeypatch.setattr(analysis, 'generate', lambda *a, **k: pytest.fail('no new provider calls'))
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: pytest.fail('cached media only'))
    assert run_manifest(source, out, skip_cluster=False) == 0
    result = json.loads((out / 'analysis.json').read_text())
    assert result['families'] == families
    assert result['run_outcome'] == 'complete'
    for cached_family in (out / 'cache').glob('families-*.json'):
        cached_family.unlink()
    assert run_manifest(source, out, skip_cluster=False) == 1
    result = json.loads((out / 'analysis.json').read_text())
    assert result['run_outcome'] == 'partial'
    assert result['families'] == []
    assert any('no matching family cache' in note for note in result['limitations'])

@pytest.mark.parametrize('failure', [TimeoutError('private'), ConnectionResetError('private')])
def test_cdn_transport_error_is_item_local(monkeypatch, tmp_path, failure):
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    source = capture_file(tmp_path, [{'ad_id': 'bad'}, {'ad_id': 'good'}])
    def download(row, dest):
        if row['ad_id'] == 'bad':
            raise failure
        return fake_capture_download([])(row, dest)
    calls = []
    monkeypatch.setattr(analysis, 'download_asset', download)
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: (calls.append(1) or valid_analysis()))
    out = tmp_path / 'out'
    assert run_capture(source, out) == 1
    result = json.loads((out / 'analysis.json').read_text())
    assert [a['analysis_outcome'] for a in result['ads']] == ['failed', 'reviewed']
    assert len(calls) == 1


def test_local_frame_preparation_timeout_is_item_local(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'example-test-key')
    small_image(tmp_path / 'a.png')
    small_image(tmp_path / 'b.png', (150, 60, 30))
    source = manifest(tmp_path, ['a.png', 'b.png'], ids=['bad', 'good'])
    prepare = analysis.prepare_local_media
    def prepare_local(path, **kwargs):
        if kwargs['ad_id'] == 'bad':
            raise TimeoutError('private')
        return prepare(path, **kwargs)
    monkeypatch.setattr(analysis, 'prepare_local_media', prepare_local)
    monkeypatch.setattr(analysis, 'analyze_media', lambda *a, **k: valid_analysis())
    out = tmp_path / 'out'
    assert run_manifest(source, out) == 1
    result = json.loads((out / 'analysis.json').read_text())
    assert [a['analysis_outcome'] for a in result['ads']] == ['failed', 'reviewed']
