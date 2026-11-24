# Ten Minutes: a whole film made with this skill

A 25-second animated film with dialogue, made end to end on 24 November 2026 to test the skill. Three characters, two rooms, six spoken lines and an end card. The app in it is made up.

Watch it: `outputs/ten-minutes-v1.mp4`.

## What is in this folder

- `script.md`: the approved script.
- `prompts/`: every prompt that was sent. `cast.txt` and `room-*.txt` made the reference pack, `sb-*.txt` made the storyboard frames, `v-*.txt` made the two scenes.
- `assets/`: the cast sheet, the two rooms and a voice sample for each character.
- `storyboard/` and `storyboard.yaml`: the two frames and the board they were approved on. Build the page with `python3 scripts/storyboard.py examples/ten-minutes`.
- `shots.json`: the shot list used for the cost plan. Run `python3 scripts/plan.py examples/ten-minutes/shots.json`.
- `ledger.json`: every paid job, with its estimate.

## What it cost

Estimates at list price, from the ledger. Provider bills are the real numbers.

| Step | Jobs | Estimate |
| --- | --- | --- |
| Cast sheet, two rooms, two storyboard frames | 5 images | $0.75 |
| Three voice samples | 3 | $0.02 |
| Two 12-second scenes on Seedance 2.5 (Replicate, 720p) | 2 clips | $5.55 |
| **Total** | | **$6.32** |

The plan allowed one full retake of each scene, $12.60 in all, and the ceiling was $15. Both scenes were accepted on the first take.

## How each scene was sent

Each scene was one take with three reference images, in this order: the storyboard frame, the cast sheet, the room. Then two voice samples: the other speaker first, Tom second.

```
python3 scripts/generate.py video --route seedance-2.5-replicate --seconds 12 \
  --resolution 720p --aspect-ratio 9:16 --prompt-file prompts/v-01.txt \
  --ref storyboard/01.jpg --ref assets/cast/cast-sheet.jpg --ref assets/rooms/kitchen.jpg \
  --voice assets/voices/maya.mp3 --voice assets/voices/tom.mp3 \
  --label scene-01 --out clips/scene-01-t1.mp4
```

The clips were joined with the two ffmpeg steps in `references/assembly.md`. Scene 1 was trimmed to 11.5 seconds and scene 2 to 11, and a 3-second end card was added.

## What the model got wrong

- Scene 2 asked for a beat of quiet before the first line. The first line starts about 0.4 seconds in.
- Scene 2 asked for the phone screen to stay turned away from the camera. It turns face up in the last second. Nothing on it is readable, so the take was kept.

## What this does not show

- Retakes. Neither scene needed one.
- Realistic faces. This film is animated.
- Seedance through Kie with reference images. Kie takes links only, so local files went through Replicate.
- The voiceover route. See `examples/wrong-door/` for that format.
