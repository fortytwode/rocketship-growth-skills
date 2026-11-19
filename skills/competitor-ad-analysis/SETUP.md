# Set up Competitor Ad Analysis

This is a local research tool for a coding agent with terminal access. You can use it without RocketShip Launcher, a RocketShip login, Google Drive or a private template. Browser-only chat environments may not provide Chrome, a local shell or persistent files.

## 1. Unzip and install

Download and unzip the skill. Ask your coding agent to read `SKILL.md` and perform setup in the extracted folder. You need Python 3.10 or newer. Chrome or Chromium is needed for live Meta collection; importing your own media does not require a browser.

On macOS/Linux, from this folder:

```bash
python3 scripts/setup.py --install
```

On Windows, use `py -3 scripts/setup.py --install`. Setup creates `.venv`, installs `requirements.txt`, creates `.env` from `env.example`, and adds local ignore rules. It preserves an existing `.env`. The non-hidden template filename is intentional: the distribution service rejects credential-like dotfiles. No real key is included.

Use `.venv/bin/python` for subsequent commands on macOS/Linux, or `.venv\Scripts\python.exe` on Windows. You may activate the environment and use `python` instead.

## 2. Add your own Gemini key locally

Create your key in [Google AI Studio](https://aistudio.google.com/apikey). Edit the `.env` file on your own machine:

```dotenv
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=
```

Do not put a real key in `env.example`, a shared ZIP, a report or a chat message. The scripts explicitly load this skill's `.env`; they do not search parent directories. An existing shell `GEMINI_API_KEY` takes precedence. You may choose another model that supports video/audio and structured output through `GEMINI_MODEL` or `--model`.

Leave `GEMINI_MODEL` blank to use the default, `gemini-3.5-flash`, with automatic fallback. If the default model's metadata is unavailable to your key, the script checks `gemini-3.1-flash-lite`, `gemini-flash-latest` and `gemini-2.5-flash` in that order. A model you choose explicitly stays fixed. To restore automatic fallback, clear any model override in the shell or `.env` and omit `--model`. Availability, quotas and prices depend on your Google project. Use your existing coding agent and your own Gemini API key.

## 3. Check setup

```bash
.venv/bin/python scripts/setup.py --check
```

For imported media only, add `--media-only`. This checks local packages, key presence and, when needed, Chrome. It does not contact Gemini. To check model metadata access before the first run, use:

```bash
.venv/bin/python scripts/setup.py --check-model
```

That makes a free metadata call, sends no media, and prints the model metadata accessible to the key, or the HTTP error code if none is available. It does not make a generation request or verify capacity to analyze media. Missing dependencies, Chrome, key, access and quota errors should be resolved before a larger run.

ChromeDriver may need a network download on first capture. Collection uses a fresh anonymous browser session, without your existing logins or cookies. If Meta blocks the page, use permitted downloaded media through an import manifest. Do not bypass the block.

## 4. Run and inspect a pilot

Follow the capture and analysis commands in `SKILL.md`, initially with `--limit 3`. Use `--dry-run` to inspect selected counts before a model call. For imported media, copy and edit `examples/import-manifest.json`: media paths are relative to the manifest and must stay inside that directory.

The model receives each selected media file and, during family comparison, the ad observations and captured ad copy. It can process audio as well as visuals. The local downloader accepts public Meta media URLs only; arbitrary remote URLs should be downloaded by the user through their normal permitted tools and supplied as local files.

Each asset is limited to 100 MB; video analysis is limited to ten minutes per asset. Larger or longer files must be prepared outside this workflow, with any trimming stated. The fallback frame exporter caps exports at 300 frames and does not silently skip parts to meet that cap.

## Outputs and reruns

- `index.html`: local research report with media, frame evidence, family comparisons and matrices.
- `analysis.json`: editable structured results and coverage metadata.
- `report.md`: written report for reuse.
- `ads.csv`, `mechanic-setting.csv`, `mechanic-emotion.csv`: auditable classifications and counts.
- `media/`: captured/imported assets and actual screenshots.
- `cache/`: completed reads keyed by media hash, model and prompt version.
- `usage.jsonl`: actual input, output and thinking-token counts. No fixed cost claim is made.

Share the entire output folder, not only its HTML file. The report uses local media and makes no external script requests. It is AI-assisted research, not proof of an advertiser's performance.

Reruns reuse verified local media for an unchanged captured source and matching model reads. If an ad's source changes, it is fetched again. An expired source with no saved media is marked unavailable. Each generation request has at most four attempts for HTTP 429, 500 or 503. If the default model returns two consecutive HTTP 503s on a request and Flash-Lite is accessible, the next attempt uses Flash-Lite with the same uploaded media. The rest of the run stays on Flash-Lite. SDK retries are disabled.

After exhausted retries or a provider access, timeout or network error, the run stops new requests and checks remaining ads and family comparisons against the cache. Public media download failures affect only the item. The report distinguishes failed items from items it could not attempt and records which models produced the existing reads. Rerun in the same output directory to reuse completed reads from the default and its fallback models. With an explicit model choice, only that model's cached reads are reused. Partial and blocked runs exit nonzero.

Each request has a 600 second limit. A timeout alone does not show whether the cause is model latency, capacity or a network problem; do not keep increasing it for an unchanged failure. Change it with `--timeout-seconds` or `GEMINI_TIMEOUT_SECONDS` (30 to 3600).

Uploaded Gemini files are deleted after each read. If deletion fails, `cleanup-warnings.jsonl` identifies the file in your Gemini account so it can be removed. Local reports and media remain on your machine until you choose to share or remove them. None are sent to RocketShip services by these scripts.

## Checks for people modifying the package

Install `pytest` into the same virtual environment and run `python -m pytest tests`. Tests use small generated media and mocked model calls; they do not require a real Gemini key or incur model usage. Use a fresh environment for testing dependency changes.

Primary documentation: [Agent Skills format](https://agentskills.io/specification), [Gemini keys](https://ai.google.dev/gemini-api/docs/api-key), [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding), [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/).
