"""Contract tests for the storyboard page builder: validation, both formats, every line type, determinism."""

from __future__ import annotations

import base64
import html
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from PIL import Image

TOOLS = Path(__file__).resolve().parents[1] / "scripts"


def load_module():
    spec = importlib.util.spec_from_file_location("shared_storyboard", TOOLS / "storyboard.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


storyboard = load_module()


def image(path: Path, size=(360, 640), color=(120, 80, 40), mode="RGB") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color if mode == "RGB" else (*color, 128)).save(path)
    return path.name


def write_source(project: Path, data: dict, name: str = "storyboard.yaml") -> Path:
    path = project / name
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "a film's folder"  # awkward characters in the project path
    (root / "boards").mkdir(parents=True)
    image(root / "boards" / "p1.jpg")
    image(root / "boards" / "p2.png", size=(640, 360), mode="RGBA")
    image(root / "refs" / "lead sheet.jpg", size=(1600, 900))
    image(root / "refs" / "props.jpg", size=(900, 900))
    return root


def dialogue_source() -> dict:
    return {
        "title": "Kitchen Table",
        "client": "Example Co",
        "format": "dialogue",
        "script_version": "v3",
        "board_version": 2,
        "summary": "Two people, one table, one decision.",
        "cast": [{"name": "Mara", "image": "refs/lead sheet.jpg", "note": "lead"},
                 {"name": "Ben", "note": "silent; seen from behind"}],
        "props": [{"name": "Hero props", "image": "refs/props.jpg", "note": "the blue mug in every panel"}],
        "settings": [{"name": "Kitchen", "note": "window screen-left"}],
        "panels": [
            {"id": "01", "scene": 1, "image": "boards/p1.jpg", "action": "Mara sets the mug down.",
             "lines": [{"type": "vo", "speaker": "Mara", "text": "I almost said no."},
                       {"type": "dialogue", "speaker": "Ben", "text": "You said <yes> & meant it?"}]},
            {"id": "02", "scene": "1", "image": "boards/p2.png",
             "lines": [{"type": "caption", "text": "Three weeks later"},
                       {"type": "app", "text": "Good evening. Tonight's chapter…"},
                       {"type": "text", "speaker": "Cleo", "text": "Line one\nline two"}]},
            {"id": "03", "frame": "black", "action": "Hold on black."},
        ],
        "continuity": [{"cut": "B01 -> B02", "where": "Mara at the sink", "in_hand": "mug", "state": "door shut"},
                       {"cut": "B02 -> B03", "where": "empty kitchen"}],
        "notes": ["Ben is always screen-right."],
    }


def narrated_source() -> dict:
    return {
        "title": "Night Walk",
        "format": "narrated",
        "script_version": 7,
        "cast": [{"name": "Narrator", "image": "refs/lead sheet.jpg", "note": "the voice"}],
        "panels": [
            {"id": "a1", "image": "boards/p1.jpg", "action": "Streetlights.",
             "lines": [{"type": "vo", "speaker": "Narrator", "text": "It started on a Tuesday."}]},
            {"id": "a2", "image": "boards/p1.jpg", "lines": [{"type": "vo", "text": "Then it rained."}]},
        ],
        "notes": "Phones only ever from the back.",
    }


def build(project: Path, data: dict, **kwargs) -> dict:
    write_source(project, data)
    return storyboard.build(project, project / "storyboard.yaml", project / "STORYBOARD.html",
                            kwargs.get("max_bytes", 15_000_000))


def problems(project: Path, data: dict) -> list[str]:
    write_source(project, data)
    with pytest.raises(storyboard.StoryboardError) as caught:
        storyboard.load_source(project, project / "storyboard.yaml")
    return caught.value.problems


# ------------------------------------------------------------------ validation

def test_every_problem_is_reported_together_with_its_location(project):
    data = dialogue_source()
    data["panels"][0]["image"] = "boards/missing.jpg"
    data["panels"][1]["lines"][0]["type"] = "whisper"
    data["panels"].append({"id": "04", "image": "boards/p1.jpg"})
    data["mood"] = "warm"
    found = problems(project, data)
    assert any("panel 01" in p and "not found" in p for p in found)
    assert any("panel 02 line 1" in p and "'whisper'" in p for p in found)
    assert any("panel 04 is empty" in p for p in found)
    assert any("unknown keys ['mood']" in p for p in found)
    assert len(found) == 4


@pytest.mark.parametrize("mutate, expected", [
    (lambda d: d.pop("title"), "title is required"),
    (lambda d: d.update(format="musical"), "format must be one of"),
    (lambda d: d.pop("script_version"), "script_version is required"),
    (lambda d: d.update(panels=[]), "at least one panel"),
    (lambda d: d["panels"][1].update(id="01"), "ids must be unique"),
    (lambda d: d["panels"][0].pop("image"), "image is required"),
    (lambda d: d["panels"][2].update(image="boards/p1.jpg"), "not both"),
    (lambda d: d["panels"][2].update(frame="grey"), "frame must be one of"),
    (lambda d: d["panels"][0]["lines"][1].pop("speaker"), "dialogue line needs a speaker"),
    (lambda d: d["panels"][1]["lines"][2].update(speaker=""), "text line needs a speaker"),
    (lambda d: d["panels"][0]["lines"][0].update(text="  "), "text is required"),
    (lambda d: d["panels"][0]["lines"][0].update(tone="soft"), "unknown keys ['tone']"),
    (lambda d: d["panels"][0].update(image="../outside.jpg"), "outside the project"),
    (lambda d: d["cast"].append({"note": "who?"}), "name is required"),
    (lambda d: d["cast"].append({"name": "Dee"}), "give an image, a note or both"),
    (lambda d: d["continuity"].append({"where": "hall"}), "cut is required"),
    (lambda d: d["continuity"].append({"cut": "B9 -> B10"}), "at least one of where"),
])
def test_each_rule_names_the_problem(project, mutate, expected):
    data = dialogue_source()
    mutate(data)
    found = problems(project, data)
    assert any(expected in p for p in found), found


def test_an_unreadable_image_is_named(project):
    (project / "boards" / "broken.jpg").write_bytes(b"not an image")
    data = dialogue_source()
    data["panels"][0]["image"] = "boards/broken.jpg"
    assert any("not a readable image" in p for p in problems(project, data))


def test_a_narrated_board_refuses_on_screen_dialogue_but_takes_other_line_types(project):
    data = narrated_source()
    data["panels"][0]["lines"].append({"type": "dialogue", "speaker": "Stranger", "text": "Hi."})
    found = problems(project, data)
    assert len(found) == 1 and "format: dialogue" in found[0]
    data = narrated_source()
    data["panels"][1]["lines"] += [{"type": "caption", "text": "Later"}, {"type": "app", "text": "Chime"},
                                   {"type": "text", "speaker": "Mum", "text": "home?"}]
    build(project, data)


def test_source_that_is_not_a_mapping_or_not_yaml_is_refused(project):
    (project / "storyboard.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(storyboard.StoryboardError, match="must be a mapping"):
        storyboard.load_source(project, project / "storyboard.yaml")
    (project / "storyboard.yaml").write_text("title: [unclosed\n", encoding="utf-8")
    with pytest.raises(storyboard.StoryboardError, match="not valid YAML"):
        storyboard.load_source(project, project / "storyboard.yaml")


# ------------------------------------------------------------------ rendering

def test_dialogue_board_renders_every_line_type_with_its_class_and_label(project):
    result = build(project, dialogue_source())
    page = Path(result["html"]).read_text(encoding="utf-8")
    for kind in storyboard.LINE_TYPES:
        assert f'class="line {kind}"' in page
        assert html.escape(storyboard.LINE_TYPES[kind]) in page  # legend entry
    assert "Mara (V.O.)" in page and ">Ben<" in page and ">Caption<" in page
    assert ">App<" in page and "Cleo (text)" in page
    assert "You said &lt;yes&gt; &amp; meant it?" in page and "<yes>" not in page
    assert 'class="blank black"' in page
    assert "Continuity at each cut" in page and "B01 -&gt; B02" in page
    assert page.count("<tr>") == 3  # header + two rows
    assert "Cast, Props, Settings" in page and "Hero props" in page
    assert "Ben is always screen-right." in page
    assert "Example Co" in page and "Script v3" in page and "Board 2" in page
    assert "prefers-color-scheme:dark" in page and "@media print" in page
    # Self-contained: every picture is embedded, nothing is fetched.
    assert not re.search(r'src="(?!data:image/jpeg;base64,)', page)
    assert page.count("data:image/jpeg;base64,") == 4  # 2 panels + cast sheet + props sheet
    assert result["format"] == "dialogue" and result["panels"] == 3 and result["images"] == 4


def test_narrated_board_has_only_the_line_types_it_uses_in_its_legend(project):
    result = build(project, narrated_source())
    page = Path(result["html"]).read_text(encoding="utf-8")
    assert "Narrated (voiceover only)" in page and "Script 7" in page
    assert page.count('class="line vo"') == 2 and "Narrator (V.O.)" in page and ">V.O.<" in page
    assert '<i class="vo">' in page and '<i class="dialogue">' not in page
    assert "Continuity at each cut" not in page
    assert "Phones only ever from the back." in page
    # The same picture used twice is still embedded for each panel that shows it.
    assert page.count("data:image/jpeg;base64,") == 3


def test_text_companion_carries_the_whole_board(project):
    result = build(project, dialogue_source())
    text = Path(result["text"]).read_text(encoding="utf-8")
    assert result["text"].endswith("STORYBOARD.txt")
    assert "[01] Sc 1  boards/p1.jpg" in text and "[03]  (black frame)" in text
    assert "VO       Mara (V.O.): I almost said no." in text
    assert "TEXT     Cleo (text): Line one\n      line two" in text
    assert "B02 -> B03 | empty kitchen | - | -" in text
    assert "Mara | lead | refs/lead sheet.jpg" in text
    assert f"sha256 {result['source_sha256']}" in text


def test_thumbnails_are_bounded_jpegs_and_transparency_is_flattened(project):
    page = Path(build(project, dialogue_source())["html"]).read_text(encoding="utf-8")
    import io
    sizes = []
    for match in re.finditer(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", page):
        with Image.open(io.BytesIO(base64.b64decode(match.group(1)))) as thumb:
            assert thumb.format == "JPEG" and thumb.mode == "RGB"
            sizes.append(thumb.size)
    assert max(max(size) for size in sizes) <= storyboard.THUMBNAIL_STEPS[0][0]
    assert (360, 640) in sizes  # never enlarged


def test_the_page_shrinks_its_thumbnails_to_fit_the_size_limit_or_says_why_not(project):
    big = Image.effect_noise((2400, 2400), 100).convert("RGB")
    big.save(project / "boards" / "noise.jpg", quality=95)
    data = narrated_source()
    data["panels"][0]["image"] = "boards/noise.jpg"
    roomy = build(project, data)
    first = roomy["thumbnail"]["longest_edge"]
    tight = build(project, data, max_bytes=roomy["bytes"] - 1)
    assert tight["thumbnail"]["longest_edge"] < first and tight["bytes"] < roomy["bytes"]
    with pytest.raises(storyboard.StoryboardError, match="over the"):
        build(project, data, max_bytes=1000)


def test_output_is_byte_identical_across_builds(project):
    first = build(project, dialogue_source())
    html_bytes = Path(first["html"]).read_bytes()
    text_bytes = Path(first["text"]).read_bytes()
    second = build(project, dialogue_source())
    assert Path(second["html"]).read_bytes() == html_bytes
    assert Path(second["text"]).read_bytes() == text_bytes
    assert first["sha256"] == second["sha256"]


# ------------------------------------------------------------------ cli

def test_cli_writes_both_files_and_reports_json(project, capsys):
    write_source(project, dialogue_source(), "boards.yaml")
    code = storyboard.main([str(project), "--source", "boards.yaml", "--out", "out/board.html"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BUILT" and report["pdf"] is None and report["warnings"] == []
    assert (project / "out" / "board.html").is_file() and (project / "out" / "board.txt").is_file()


def test_cli_failure_lists_problems_and_writes_nothing(project, capsys):
    data = dialogue_source()
    data["panels"][0]["lines"][0]["type"] = "song"
    write_source(project, data)
    assert storyboard.main([str(project)]) == 1
    report = json.loads(capsys.readouterr().err)
    assert "1 problem" in report["error"] and "'song'" in report["problems"][0]
    assert not (project / "STORYBOARD.html").exists()


def test_pdf_without_a_browser_keeps_the_page_and_says_how_to_print(project, capsys, monkeypatch):
    write_source(project, narrated_source())
    monkeypatch.setattr(storyboard, "find_browser", lambda: None)
    assert storyboard.main([str(project), "--pdf"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["pdf"] is None and "print to PDF" in report["warnings"][0]
    assert (project / "STORYBOARD.html").is_file()


def fake_browser(monkeypatch, writes: bytes | None, exits: bool):
    """A stand-in browser that writes ``writes`` to the PDF path and either exits or keeps running."""
    calls = []

    class Process:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.terminated = False
            if writes is not None:
                target = next(a for a in args if a.startswith("--print-to-pdf=")).split("=", 1)[1]
                Path(target).write_bytes(writes)

        def poll(self):
            return 0 if exits or self.terminated else None

        def terminate(self):
            self.terminated = True
            calls.append("terminated")

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(storyboard, "find_browser", lambda: "/opt/browser")
    monkeypatch.setattr(storyboard.subprocess, "Popen", Process)
    return calls


PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


@pytest.mark.parametrize("exits", [True, False])
def test_pdf_is_judged_by_the_written_file_and_a_lingering_browser_is_closed(project, capsys, monkeypatch, exits):
    write_source(project, narrated_source())
    calls = fake_browser(monkeypatch, PDF_BYTES, exits)
    assert storyboard.main([str(project), "--pdf"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pdf"].endswith("STORYBOARD.pdf") and report["warnings"] == []
    assert calls[0][0] == "/opt/browser" and "--headless=new" in calls[0]
    assert calls[0][-1] == (project / "STORYBOARD.html").resolve().as_uri()
    assert ("terminated" in calls) is (not exits)


def test_a_truncated_or_missing_pdf_is_a_warning_not_a_result(project, capsys, monkeypatch):
    write_source(project, narrated_source())
    fake_browser(monkeypatch, b"%PDF-1.7\nhalf", exits=True)
    assert storyboard.main([str(project), "--pdf"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pdf"] is None and "print to PDF" in report["warnings"][0]
    assert (project / "STORYBOARD.html").is_file()


def test_cli_refuses_a_non_html_output_name(project, capsys):
    write_source(project, narrated_source())
    assert storyboard.main([str(project), "--out", "board.pdf"]) == 1
    assert "html" in json.loads(capsys.readouterr().err)["error"]


def test_the_bundled_example_board_builds(tmp_path):
    import shutil

    example = Path(__file__).resolve().parents[1] / "examples" / "wrong-door"
    project = tmp_path / "wrong-door"
    shutil.copytree(example, project)
    result = storyboard.build(project, project / "storyboard.yaml", project / "STORYBOARD.html", 15_000_000)
    page = (project / "STORYBOARD.html").read_text(encoding="utf-8")
    assert "Wrong Door (example)" in page and "Flat nine." in page
    assert (project / "STORYBOARD.txt").is_file()
    assert result
