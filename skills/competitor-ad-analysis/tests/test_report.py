import csv
import json
import sys
from pathlib import Path
from html.parser import HTMLParser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import report


def fixture(tmp_path):
    (tmp_path / "good.jpg").write_bytes(b"fake image")
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"outside")
    payload = {
        "schema_version": 1, "model": "demo", "cohort": "supplied", "capture": {"status": "partial", "reason": "source limit <b>hit</b>", "completeness": "partial: 3 of 20 available", "available_records": 20, "raw_record_cap": 3, "unique_target": 3, "date_from": "2026-01-01", "date_to": "2026-01-31"},
        "coverage": {"selected": 3, "reviewed": 2, "unavailable": 1, "unique_executions": 2, "ranking_verified": False},
        "ads": [
            {"ad_id": "A1", "execution_id": "x1", "source_url": "javascript:alert(1)", "launch_date": "2026-01-01", "ad_copy": "=2+2 <script>alert(1)</script>", "status": "reviewed", "error": "", "media_file": "good.jpg", "frames": [{"path": "good.jpg", "seconds": 1.5}], "analysis": {"summary": "safe", "mechanic": "Demo", "setting": "Home", "emotion": "Calm", "limitations": ["check crop"], "beats": [{"start_seconds": 0, "end_seconds": 1, "role": "hook", "observation": "look", "dialogue": "hi", "overlay": ""}]}},
            {"ad_id": "A2", "execution_id": "x2", "source_url": "https://example.com/ad", "launch_date": "2026-01-02", "ad_copy": "unavailable", "status": "unavailable", "error": "=not reviewed", "media_file": "", "frames": [], "analysis": {}},
            {"ad_id": "A3", "execution_id": "x2", "source_url": "http://example.com", "launch_date": "", "ad_copy": "another", "status": "reviewed", "error": "", "media_file": "", "frames": [], "analysis": {"mechanic": "Demo", "setting": "Office", "emotion": "Calm"}},
        ],
        "families": [{"name": "One", "definition": "A family", "ad_ids": ["A1", "A3"], "shared_structure": ["same"], "exceptions": []}],
        "limitations": ["sample limited"],
    }
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    return path


def test_render_escapes_html_and_rejects_unsafe_links(tmp_path):
    path = fixture(tmp_path)
    out = tmp_path / "report"
    report.write_report(path, out)
    doc = (out / "index.html").read_text()
    assert "&lt;script&gt;" in doc
    assert "javascript:" not in doc
    assert "outside.png" not in doc
    assert "good.jpg" in doc
    assert "Ranking was not verified" in doc
    assert "Selected records 3" in doc and "Reviewed records 2" in doc
    assert "Source capture" in doc and "partial" in doc
    assert "3 of 20 available" in doc and "&lt;b&gt;hit&lt;/b&gt;" in doc
    assert "Mechanic by setting" in doc and "Mechanic by emotion" in doc
    assert "Analysis unavailable" in doc and "=not reviewed" in doc
    assert '<img class="media-image"' in doc
    assert "object-fit:contain" in doc
    assert "object-fit:cover" not in doc
    assert (out / "analysis.json").exists()
    assert (out / "report.md").exists()


def test_csv_formula_neutralization_and_exact_matrix_counts(tmp_path):
    path = fixture(tmp_path)
    out = tmp_path / "report"
    report.write_report(path, out)
    with (out / "ads.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["ad_copy"].startswith("'=2+2")
    assert rows[1]["error"].startswith("'=not reviewed")
    with (out / "mechanic-setting.csv").open(encoding="utf-8-sig", newline="") as f:
        matrix = list(csv.reader(f))
    assert matrix == [["Mechanic / Setting", "Home", "Office"], ["Demo", "1", "1"]]
    with (out / "mechanic-emotion.csv").open(encoding="utf-8-sig", newline="") as f:
        emotion = list(csv.reader(f))
    assert emotion == [["Mechanic / Emotion", "Calm"], ["Demo", "2"]]


def test_same_input_output_json_is_not_copied(tmp_path):
    path = fixture(tmp_path)
    report.write_report(path, path.parent)
    assert path.exists()


def test_rejects_inconsistent_coverage_and_family_membership(tmp_path):
    path = fixture(tmp_path)
    payload = json.loads(path.read_text())
    payload["coverage"]["reviewed"] = 1
    path.write_text(json.dumps(payload))
    try:
        report.write_report(path, tmp_path / "bad-coverage")
    except ValueError as exc:
        assert "coverage.reviewed" in str(exc)
    else:
        raise AssertionError("inconsistent coverage was accepted")
    payload["coverage"]["reviewed"] = 2
    payload["families"][0]["ad_ids"] = ["A1"]
    path.write_text(json.dumps(payload))
    try:
        report.write_report(path, tmp_path / "bad-families")
    except ValueError as exc:
        assert "each reviewed ad_id exactly once" in str(exc)
    else:
        raise AssertionError("incomplete family membership was accepted")


def test_family_matrix_cells_are_column_specific_and_markdown_escapes_html(tmp_path):
    path = fixture(tmp_path)
    payload = json.loads(path.read_text())
    payload["ads"][0]["ad_copy"] = "<img src=x onerror=alert(1)>"
    payload["families"] = [
        {"name": "Family A", "definition": "A", "ad_ids": ["A1"], "shared_structure": [], "exceptions": []},
        {"name": "Family B", "definition": "B", "ad_ids": ["A3"], "shared_structure": [], "exceptions": []},
    ]
    path.write_text(json.dumps(payload))
    out = tmp_path / "report"
    report.render(path, out)
    doc = (out / "index.html").read_text()
    assert "<tr><th>A1</th><td>1</td><td>0</td></tr>" in doc
    assert "<tr><th>A3</th><td>0</td><td>1</td></tr>" in doc
    md = (out / "report.md").read_text()
    assert "&lt;img src=x onerror=alert(1)&gt;" in md
    assert "<img src=x" not in md
    assert "## Source capture" in md
    assert "**Status:** partial" in md
    assert "**Completeness:** partial: 3 of 20 available" in md
    assert "**Reason:** source limit &lt;b&gt;hit&lt;/b&gt;" in md


def test_separate_output_copies_assets_and_markdown_has_complete_beats(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    path = fixture(source)
    out = tmp_path / "shareable"
    report.render(path, out)
    assert (out / "good.jpg").is_file()
    md = (out / "report.md").read_text()
    assert "| Start | End | Role |" in md
    assert "| 0 | 1 | hook |" in md
    assert "Analysis limitations:" in md and "check crop" in md
    (source / "good.jpg").unlink()
    rerender = tmp_path / "rerendered"
    report.render(out / "analysis.json", rerender)
    assert (rerender / "good.jpg").is_file()
    assert "good.jpg" in (rerender / "index.html").read_text()


class GalleryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.section_stack = []
        self.groups = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            name = attrs.get("id") if attrs.get("class") == "cluster" else self.section_stack[-1] if self.section_stack else None
            self.section_stack.append(name)
        if tag == "article" and attrs.get("class") == "member":
            group = self.section_stack[-1] if self.section_stack else None
            self.groups.setdefault(group, []).append(attrs["data-ad-id"])

    def handle_endtag(self, tag):
        if tag == "section":
            self.section_stack.pop()


def test_primary_gallery_shows_every_member_in_its_family(tmp_path):
    path = fixture(tmp_path)
    data = json.loads(path.read_text())
    data["families"] = [
        {"name": "First", "definition": "First definition", "ad_ids": ["A1"], "representative_ad_ids": ["A1"]},
        {"name": "Second", "definition": "Second definition", "ad_ids": ["A3"], "representative_ad_ids": ["A1"]},
    ]
    data["title"] = "Example <brand>"
    path.write_text(json.dumps(data))
    report.render(path, tmp_path / "out")
    doc = (tmp_path / "out/index.html").read_text()
    parser = GalleryParser()
    parser.feed(doc)
    assert parser.groups == {"cluster-1": ["A1"], "cluster-2": ["A3"]}
    assert "First · 1 ads" in doc and "Second definition" in doc
    assert doc.count('class="badge">Representative') == 1
    assert "Thumbnail unavailable" in doc
    assert "Example &lt;brand&gt;" in doc
    assert doc.index("Cluster overview") < doc.index('id="cluster-1"') < doc.index("Classification matrices and membership audit")
    assert "@page{size:A4 landscape" in doc
    assert "page-break-before:always" in doc


def top_fixture(tmp_path, verified=True):
    tmp_path.mkdir()
    path = fixture(tmp_path)
    data = json.loads(path.read_text())
    data["cohort"] = "top"
    data["coverage"]["ranking_verified"] = verified
    # Non-sequential stored ranks catch accidental enumeration or sorting.
    data["ads"][0]["source_rank"] = 7
    data["ads"][1]["source_rank"] = 2
    data["ads"][0]["media_file"] = "clip.mp4"
    (tmp_path / "clip.mp4").write_bytes(b"video")
    (tmp_path / "good.jpg").write_bytes(b"different top frame")
    path.write_text(json.dumps(data))
    return path


def test_separate_top_assets_rank_fidelity_and_standalone_rerender(tmp_path):
    main_path = fixture(tmp_path)
    top_path = top_fixture(tmp_path / "top-source")
    out = tmp_path / "out"
    report.render(main_path, out, top_path)
    assert (out / "main/good.jpg").read_bytes() == b"fake image"
    assert (out / "top/good.jpg").read_bytes() == b"different top frame"
    doc = (out / "index.html").read_text()
    top_doc = doc.split('<section class="top-sample">', 1)[1].split('</section>', 1)[0]
    assert 'src="top/good.jpg"' in top_doc
    assert "Source rank 7" in top_doc and "Source rank 2" in top_doc
    assert top_doc.index("Source rank 7") < top_doc.index("Source rank 2")
    assert "Recorded order 3" in top_doc
    saved = json.loads((out / "analysis.json").read_text())
    assert saved["top_sample"]["ads"][0]["source_rank"] == 7
    assert saved["top_sample"]["ads"][0]["media_file"] == "top/clip.mp4"
    # Original sources can disappear: the merged report retains all references.
    (top_path.parent / "good.jpg").unlink()
    (main_path.parent / "good.jpg").unlink()
    again = tmp_path / "again"
    report.render(out / "analysis.json", again)
    assert (again / "top/good.jpg").is_file()
    assert (again / "main/good.jpg").is_file()
    assert (again / "index.html").read_text() == doc


def test_unverified_sample_has_no_rank_claim_and_nested_coverage_is_checked(tmp_path):
    main_path = fixture(tmp_path)
    top_path = top_fixture(tmp_path / "top-source", verified=False)
    out = tmp_path / "out"
    report.render(main_path, out, top_path)
    doc = (out / "index.html").read_text()
    assert "Top 10" not in doc and "Source rank 7" not in doc
    assert "Recorded order 1" in doc
    data = json.loads((out / "analysis.json").read_text())
    data["top_sample"]["coverage"]["selected"] = 10
    try:
        report.validate_data(data)
    except ValueError as exc:
        assert "coverage.selected" in str(exc)
    else:
        raise AssertionError("Invalid nested coverage was accepted")


def test_archived_selection_discloses_saved_rank_without_verification(tmp_path):
    main_path = fixture(tmp_path)
    top_path = top_fixture(tmp_path / "top-source", verified=False)
    data = json.loads(top_path.read_text())
    data["capture"].update(ranking_basis="archived_selection", ranking_note="Saved sort receipt; current order unverified <b>note</b>")
    top_path.write_text(json.dumps(data))
    report.render(main_path, tmp_path / "out", top_path)
    doc = (tmp_path / "out/index.html").read_text()
    assert "Archived selection" in doc and "Archived #7" in doc
    assert "current order unverified &lt;b&gt;note&lt;/b&gt;" in doc
    assert "Source rank 7" not in doc
    saved = json.loads((tmp_path / "out/analysis.json").read_text())
    assert saved["top_sample"]["coverage"]["ranking_verified"] is False


def test_wrong_cohort_cannot_claim_verified_source_rank(tmp_path):
    top_path = top_fixture(tmp_path / "top-source")
    data = json.loads(top_path.read_text())
    data["cohort"] = "supplied"
    doc = report.top_gallery(data, top_path.parent, top_path.parent)
    assert "Source rank" not in doc and "Top 10" not in doc
    assert "Recorded order 1" in doc


def test_ten_archived_ads_are_labeled_as_archived_not_verified(tmp_path):
    top_path = top_fixture(tmp_path / "top-source", verified=False)
    data = json.loads(top_path.read_text())
    data["capture"]["ranking_basis"] = "archived_selection"
    original = data["ads"][0]
    data["ads"] = [{**original, "ad_id": str(i), "source_rank": i} for i in range(1, 11)]
    doc = report.top_gallery(data, top_path.parent, top_path.parent)
    assert "Archived top-ten selection" in doc and "Archived #10" in doc
    assert "Source rank" not in doc
    assert doc.count('class="member"') == 10


def test_merging_does_not_reinterpret_absolute_assets_as_relative(tmp_path):
    main_path = fixture(tmp_path)
    top_path = top_fixture(tmp_path / "top-source")
    data = json.loads(top_path.read_text())
    data["ads"][0]["media_file"] = "/untrusted.jpg"
    data["ads"][0]["frames"] = []
    top_path.write_text(json.dumps(data))
    out = tmp_path / "out"
    (out / "top").mkdir(parents=True)
    (out / "top/untrusted.jpg").write_bytes(b"should not render")
    report.render(main_path, out, top_path)
    doc = (out / "index.html").read_text()
    assert 'src="top/untrusted.jpg"' not in doc


def test_concise_overview_and_empty_family_notes_are_omitted(tmp_path):
    path = fixture(tmp_path)
    data = json.loads(path.read_text())
    data["families"][0]["shared_structure"] = []
    data["families"][0]["exceptions"] = []
    path.write_text(json.dumps(data))
    out = tmp_path / "out"
    report.render(path, out)
    doc = (out / "index.html").read_text()
    overview = doc.split('<h2>Cluster overview</h2>', 1)[1].split('<section class="cluster"', 1)[0]
    assert 'href="#cluster-1">See all 2 ads</a>' in overview
    assert "Ads: A1, A3" not in overview
    assert "Shared structure" not in overview and "Exceptions" not in overview
    cluster = doc.split('<section class="cluster"', 1)[1].split('<details class="secondary">', 1)[0]
    assert "Shared structure" not in cluster and "Exceptions" not in cluster
    assert "None supplied" not in cluster
    parser = GalleryParser()
    parser.feed(doc)
    assert parser.groups == {"cluster-1": ["A1", "A3"]}


def test_unverified_deduplication_does_not_claim_distinct_creatives(tmp_path):
    path = fixture(tmp_path)
    data = json.loads(path.read_text())
    data["coverage"]["deduplication_verified"] = False
    path.write_text(json.dumps(data))
    out = tmp_path / "out"
    report.render(path, out)
    doc = (out / "index.html").read_text()
    md = (out / "report.md").read_text()
    assert "Distinct creative count not established" in doc
    assert "Distinct creative count not established" in md
    assert "Unique reviewed executions" not in doc
    assert "unique executions 2" not in md
    del data["coverage"]["deduplication_verified"]
    assert "Unique reviewed executions 2" in report.render_html(data, out, out)
    assert "unique executions 2" in report.markdown(data, out, out)
