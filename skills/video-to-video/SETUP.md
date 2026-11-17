# Setup and production boundary

Use Python 3.10+, FFmpeg and ffprobe on `PATH`, and an assistant that can inspect the required video/audio. Install the Python helpers in a virtual environment with `python -m pip install -r requirements.txt`.

Give your assistant `SKILL.md` and your reference file, requested changes and available assets. The assistant should run the commands, inspect the evidence and carry out supported production work. Editor-specific skill installation flows have not been certified.

## Prepare a local reference

From this skill folder, using your own paths:

```bash
python scripts/prepare_reference.py --input /path/to/reference.mp4 --out /path/to/project/reference-evidence
```

The destination must be new. This saves timestamped JPEG samples, `media.json`, `probe.json` and, when present, the first audio stream as `audio.wav`. It preserves the original file. The media helper supports short references up to ten minutes and at most 300 sampled frames; increase the interval with `--every-seconds` for a longer reference. Audio and frame extraction are preparation, not a completed review.

For closer inspection, use a fresh output folder and `--at 1.2 1.5 1.8` instead of regular sampling. The manifest records the actual decoded frame timestamps, which may differ from requested times in variable-rate footage.

## Assemble prepared clips

The included renderer reuses the existing RocketShip editorial assembly method with ordinary local media tools. It makes no provider calls. Supply a manifest like `examples/assembly.json`, with paths relative to the working directory from which you run the command.

```bash
python /path/to/skill/scripts/render_video.py validate --manifest scenes.json
python /path/to/skill/scripts/render_video.py storyboard --manifest scenes.json --output storyboard.md
python /path/to/skill/scripts/render_video.py render --manifest scenes.json --output remake.mp4
```

Prepare the clips before assembly. The renderer does not trim, generate new scenes, recreate text, add overlays, mix audio or normalize mismatched clips. It checks stream compatibility and refuses an existing output. Use a new filename for each revision.

For a single clip, keep its original filename extension: the renderer preserves the original container byte for byte. Multi-clip assembly supports video-only clips or compatible PCM audio in a container that supports those codecs. It refuses display transforms/rotation and mismatched aspect ratios; normalize those in your editor first. It also refuses compressed multi-clip audio, including AAC, because independently encoded segments can introduce priming or timing changes. Use your editor with the continuous original soundtrack for that case. Matching stream metadata alone does not establish gapless sound or narrative fidelity; review the actual finished film.

## Generated or substantially edited scenes

Use the video-generation or editing capabilities already available to your assistant, with your own authorized accounts and any required budget. This package includes no hosted generator, API client, provider credits or RocketShip service access. If those tools are unavailable, local evidence preparation and compatible-clip assembly remain available; new generated characters, scenes or voices do not.

Do not label prepared notes or a storyboard as a rendered video. Do not upload private source media to a new provider without the user's authorization.
