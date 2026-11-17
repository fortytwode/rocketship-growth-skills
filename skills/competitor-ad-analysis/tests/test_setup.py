import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup.py"
SPEC = importlib.util.spec_from_file_location("competitor_ad_setup", SCRIPT)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def test_init_config_preserves_existing_files_and_adds_only_missing_rules(tmp_path):
    (tmp_path / "env.example").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=example-existing\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("custom-rule\n.env\n", encoding="utf-8")

    setup.initialize_config(tmp_path)

    assert (tmp_path / ".env").read_text(encoding="utf-8") == "GEMINI_API_KEY=example-existing\n"
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines.count(".env") == 1
    assert "custom-rule" in lines
    assert set(setup.GITIGNORE_ENTRIES).issubset(lines)


def test_check_reports_blank_key_without_disclosing_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(setup, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(setup.importlib.metadata, "version", lambda _name: "1.0")
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("check must not run subprocesses")))
    # Explicitly blank the key after import; there is no .env in this temp project.
    monkeypatch.setenv("GEMINI_API_KEY", "")

    result = setup.check(tmp_path)
    output = capsys.readouterr()

    assert result == 1
    assert "GEMINI_API_KEY is missing or blank" in output.err
    assert "example-secret" not in output.out + output.err


def test_check_reads_only_project_env_and_keeps_parent_environment(monkeypatch, tmp_path, capsys):
    # Simulate dotenv being installed and load_dotenv obeying override=False.
    monkeypatch.setattr(setup.importlib.metadata, "version", lambda _name: "1.0")
    monkeypatch.setattr(setup, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setenv("GEMINI_API_KEY", "already-present")
    monkeypatch.setitem(__import__("sys").modules, "dotenv", type("Dotenv", (), {
        "load_dotenv": staticmethod(lambda path, override=False: (path == tmp_path / ".env") and not override)
    }))
    assert setup.check(tmp_path) == 0
    assert "Local prerequisites look ready" in capsys.readouterr().out


def test_media_only_check_does_not_require_chrome(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(setup.importlib.metadata, "version", lambda _name: "1.0")
    monkeypatch.setattr(setup, "find_chrome", lambda: None)
    monkeypatch.setenv("GEMINI_API_KEY", "example-key")
    assert setup.check(tmp_path, media_only=True) == 0
    assert "Chrome" not in capsys.readouterr().out


def test_install_initializes_config_before_pip(monkeypatch, tmp_path):
    (tmp_path / "env.example").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "venv" in argv:
            venv = Path(argv[-1])
            python = venv / "bin/python"
            python.parent.mkdir(parents=True)
            python.touch()

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    assert setup.install(tmp_path) == 0
    assert (tmp_path / ".env").exists()
    assert (tmp_path / ".gitignore").exists()
    assert any("pip" in call for call in calls)
