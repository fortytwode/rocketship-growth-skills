# Setup

You need four things: Python, ffmpeg, accounts with the model providers, and an AI assistant that can run a skill.

## 1. Python

Python 3.10 or later. Two packages, used only by the storyboard builder:

```
python3 -m pip install pyyaml pillow
```

Everything else uses the standard library. To run the tests you also need `pytest`.

## 2. ffmpeg

ffmpeg and ffprobe join the clips, lay the sound and burn captions.

- macOS: `brew install ffmpeg`
- Windows: `winget install ffmpeg`
- Linux: use your package manager.

Captions and end card text need an ffmpeg build with the `drawtext` filter. Check with:

```
ffmpeg -hide_banner -filters | grep drawtext
```

If nothing prints, install a fuller build (on macOS, `brew install ffmpeg-full` is one). If your ffmpeg is somewhere unusual, set `NARRATED_FFMPEG` to its full path.

Captions also need a font file (`.ttf`) that you have the right to use. Copy it into the project's `assets` folder.

## 3. Accounts and keys

You pay each provider directly, by the second or by the image. There is no subscription in this skill.

| Provider | What it is used for here | Environment variable |
|---|---|---|
| Replicate | Nano Banana Pro stills, Grok video, Kling 3.0, Seedance 2.5 | `REPLICATE_API_TOKEN` |
| Kie | Seedance 2.5 | `KIE_API_KEY` |
| ElevenLabs | Narration and voice samples | `ELEVENLABS_API_KEY` |

You do not need all three. A voiceover film needs Replicate and ElevenLabs. A dialogue film needs Replicate or Kie for Seedance, plus ElevenLabs for voice samples.

Create a key on each provider's site, add some credit, and set the variable in your shell:

```
export REPLICATE_API_TOKEN="paste-your-token-here"
```

Rules for keys:

- Set them in your own terminal or shell profile. Do not paste a key into a chat with an assistant.
- Do not put keys in the project folder or in any file you share.
- `generate.py` reads keys from the environment. It never prints or saves them. `--dry-run` shows the variable's name in place of the key.
- Where a provider lets you set a spending limit on the account, set one. The ledger in this skill is a second check, not the only one.

## 4. Files that need a link (Kie only)

Kie downloads your reference images and voice samples itself. It needs an https link for each one. A file on your computer will not work on that route.

Upload the approved files to storage you control and use those links. If your storage can make links that expire, use them. Do not post unreleased work somewhere public just to get a link.

The Replicate routes take local files. The script sends them inline, so keep each one small (a few megabytes at most).

## 5. Check the install

From the skill folder:

```
python3 -m pytest tests -q
python3 scripts/storyboard.py examples/wrong-door
python3 scripts/plan.py examples/wrong-door/shots.json
python3 scripts/generate.py narration --voice-id EXAMPLE --text "Hello." --out x.mp3 --dry-run
```

The tests make no network calls and need no keys. The last command prints a request without sending it.

The storyboard command writes `STORYBOARD.html` and `STORYBOARD.txt` into the example folder. Delete them when you have looked.

## 6. Use it

Ask your assistant to read `SKILL.md`, then give it your approved script.

## What is in the folder

- `SKILL.md`: the steps your assistant follows.
- `references/`: the reference pack, the storyboard format, cost, prompts for each route, review and fixing, assembly, and a list of things that go wrong.
- `scripts/generate.py`: sends one image, video or narration job and keeps the ledger.
- `scripts/storyboard.py`: builds the storyboard page.
- `scripts/plan.py` and `data/models.json`: the cost estimate and the price table.
- `scripts/assemble_narrated.py` and `scripts/filehash.py`: build a voiceover film from clips.
- `examples/wrong-door/`: a tiny made-up project. Its pictures are placeholder drawings.
- `examples/ten-minutes/`: a real 25-second dialogue film made with this skill, with its prompts, reference pack, storyboard, ledger and the finished film.
- `tests/`: checks for all of the above.

## Limits you should know

- **Nothing in `generate.py` has been run against a live provider from this package.** The requests follow what worked in our own tools on the dates we used them. Providers change their fields. Run `--dry-run`, then one short, cheap job on each route before a real run.
- **Prices are list prices on the date in `data/models.json`.** Check the provider's page. Kling 3.0 on Replicate has no price in the table, so you pass `--rate` yourself.
- **The ledger holds estimates.** Your provider bills are the real numbers. A failed job is not counted in the ledger. Check whether your provider billed it.
- **Models refuse some requests**, most often realistic faces. That is the provider's decision.
- **You are responsible for what you make.** Use only faces, voices, logos and music you have the right to use. Do not clone a real person's voice or likeness without their consent.
