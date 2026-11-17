#!/usr/bin/env python3
"""Prepare and diagnose a local competitor-ad-analysis installation."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
GITIGNORE_ENTRIES = (".env", ".env.*", "!.env.example", ".venv/", "output/", "__pycache__/")
REQUIRED_DISTRIBUTIONS = (
    "selenium", "webdriver-manager", "Pillow", "opencv-python-headless",
    "PyYAML", "tzdata", "google-genai", "python-dotenv", "pydantic", "av",
)


def initialize_config(project_dir: Path) -> list[str]:
    """Create local config templates without replacing user files."""
    messages: list[str] = []
    env_file = project_dir / ".env"
    if env_file.exists():
        messages.append("Kept existing .env")
    else:
        shutil.copyfile(project_dir / "env.example", env_file)
        if os.name != 'nt':
            env_file.chmod(0o600)
        messages.append("Created .env from env.example")

    gitignore = project_dir / ".gitignore"
    content = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    existing = set(content.splitlines())
    missing = [entry for entry in GITIGNORE_ENTRIES if entry not in existing]
    if missing:
        separator = "" if not content or content.endswith("\n") else "\n"
        with gitignore.open("a", encoding="utf-8") as handle:
            handle.write(separator + "\n".join(missing) + "\n")
        messages.append("Added local config/output ignore rules to .gitignore")
    else:
        messages.append("Kept existing .gitignore rules")
    return messages


def find_chrome() -> str | None:
    """Find a locally installed Chrome/Chromium executable without downloading."""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    system = platform.system()
    candidates: list[Path] = []
    if system == "Darwin":
        candidates.extend((
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ))
    elif system == "Windows":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "Google/Chrome/Application/chrome.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


def check(project_dir: Path, *, media_only: bool = False) -> int:
    """Report local prerequisites only; this function performs no network calls."""
    problems: list[str] = []
    if sys.version_info < (3, 10):
        problems.append("Python 3.10 or newer is required.")

    missing = []
    for distribution in REQUIRED_DISTRIBUTIONS:
        try:
            importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            missing.append(distribution)
    if missing:
        problems.append("Missing Python packages: " + ", ".join(missing) + ". Run `python scripts/setup.py --install`.")

    key = os.environ.get("GEMINI_API_KEY", "")
    if "python-dotenv" not in missing:
        try:
            from dotenv import load_dotenv
            load_dotenv(project_dir / ".env", override=False)
            key = os.environ.get("GEMINI_API_KEY", "")
        except Exception:
            # A malformed local file should produce a safe, actionable diagnosis.
            problems.append("Could not read .env. Check its KEY=value format; the key value was not displayed.")
    if not key.strip():
        problems.append("GEMINI_API_KEY is missing or blank. Add it to this skill's .env (copy env.example); its value will not be displayed.")

    if not media_only:
        chrome = find_chrome()
        if chrome:
            print(f"Chrome found: {chrome}")
        else:
            problems.append("Chrome or Chromium was not found. Install a supported browser and ensure it is on PATH or in its standard application location.")

    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    print("Local prerequisites look ready. This did not contact Gemini: run `python scripts/setup.py --check-model` to confirm your key can call a model.")
    return 0


def check_model(project_dir: Path) -> int:
    """Confirm the key can reach a usable model. One free metadata call per model tried."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import analyze
        from google import genai
        from google.genai import types
        key, requested = analyze.read_config(project_dir / ".env")
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=60000))
    except Exception as exc:
        print(f"ERROR: Could not start the model check ({type(exc).__name__}). Run `python scripts/setup.py --check` first.", file=sys.stderr)
        return 1
    try:
        model, note = analyze.resolve_model(client, requested)
    except Exception as exc:
        print(f"ERROR: Model check failed for {requested or analyze.MODEL_DEFAULT}. {analyze.provider_error(exc)}. The key value was not displayed.", file=sys.stderr)
        return 1
    finally:
        client.close()
    if note:
        print(note)
        print(f"To make this permanent, set GEMINI_MODEL={model} in .env.")
    print(f"Model ready: {model}")
    return 0


def install(project_dir: Path) -> int:
    """Create a project venv and install requirements with bounded subprocesses."""
    for message in initialize_config(project_dir):
        print(message)
    venv_python = project_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        if not venv_python.exists():
            subprocess.run([sys.executable, "-m", "venv", str(project_dir / ".venv")],
                           check=True, timeout=300)
        subprocess.run([str(venv_python), "-m", "pip", "install", "-r", str(project_dir / "requirements.txt")],
                       check=True, timeout=300)
    except subprocess.TimeoutExpired:
        print("ERROR: Installation exceeded the 300 second limit.", file=sys.stderr)
        return 1
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"ERROR: Installation failed ({type(exc).__name__}). See the pip output above.", file=sys.stderr)
        return 1
    print(f"Installed requirements into {project_dir / '.venv'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=SKILL_ROOT, help="Skill directory (default: this script's skill root)")
    parser.add_argument("--media-only", action="store_true", help="With --check, skip the Chrome requirement for imported-media review")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="Check local packages, API key presence, and Chrome; no network calls")
    actions.add_argument("--check-model", action="store_true", help="Confirm the key can call a model (free metadata call to Gemini; no media is sent)")
    actions.add_argument("--install", action="store_true", help="Create .venv and install requirements (uses network)")
    actions.add_argument("--init-config", action="store_true", help="Create .env and append required .gitignore rules")
    args = parser.parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    if args.check:
        return check(project_dir, media_only=args.media_only)
    if args.check_model:
        return check_model(project_dir)
    if args.install:
        return install(project_dir)
    for message in initialize_config(project_dir):
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
