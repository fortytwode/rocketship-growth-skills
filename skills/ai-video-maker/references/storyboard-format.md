# The storyboard

The storyboard is one still per moment that matters, in order, with the exact words beside each. It is what the person approves before any video is made.

One script builds the page from one source file, so every storyboard looks the same. Do not write the page by hand.

```
python3 SKILL/scripts/storyboard.py PROJECT_FOLDER [--source storyboard.yaml] [--out STORYBOARD.html] [--pdf]
```

It writes:

- `STORYBOARD.html`: one file with every image inside it as a small JPEG. It can be sent on its own. It stays under 15 MB.
- `STORYBOARD.txt`: the same content as plain text.
- `STORYBOARD.pdf` with `--pdf`, if Chrome, Chromium or Edge is installed. Without one, open the page in a browser and print it.

Building the page checks the format. It does not judge the pictures. That is your job, and then the person's.

## What makes a good storyboard

- **Each panel adds something.** An action, a reaction or a change of place. Do not make one panel per line of script.
- **The words go beside the picture, in full.** For a voiceover, every word of narration is assigned to a panel. No shortened cues, no "...". If a sentence has no panel, you find out now.
- **Read the panels together.** Check sides of the room, who holds what, and that the room has not changed.
- **Cover the words.** If the pictures alone do not tell the story, fix the pictures.

## The source file

`storyboard.yaml` in the project folder. Image paths are relative to the project and must stay inside it.

```yaml
title: Working title                 # required
client: Brand name                   # optional
format: dialogue                     # required: dialogue | narrated
script_version: v3                   # required: the approved script this follows
board_version: "2"                   # optional; raise it on every revision
summary: One or two sentences on the look and any fixed rules.

cast:                                # optional; name required, image and/or note
  - name: Lead
    image: assets/cast/lead.jpg
    note: always on the left of the frame
props:                               # optional; same shape as cast
  - name: Violin case
    image: assets/props/violin-case.jpg
settings:                            # optional; same shape as cast
  - name: Kitchen
    image: assets/rooms/kitchen.jpg

panels:                              # required, in film order
  - id: "01"                         # unique; quote ids that start with zero
    scene: "1"                       # optional
    image: storyboard/01.jpg         # or `frame: black` for a deliberate blank
    action: What the picture shows and what happens.
    lines:
      - {type: vo, speaker: Lead, text: The narration over this panel.}
      - {type: dialogue, speaker: Friend, text: A line spoken on screen.}
      - {type: caption, text: Words placed over the picture in the edit.}
      - {type: app, speaker: Phone, text: Sound from a device in the scene.}
      - {type: text, speaker: Friend, text: A message in a text thread.}

continuity:                          # optional; one row per cut that carries something over
  - cut: 01 -> 02                    # required
    where: Where everyone is at the cut
    in_hand: Who holds what
    state: Doors, lights and objects that must stay as they are

notes:                               # optional; text or a list
  - A fixed rule, an open question or a deliberate change.
```

## Line types

| `type` | Shown as | Use for | Speaker |
|---|---|---|---|
| `vo` | `NAME (V.O.)` | narration that nobody speaks on screen | optional |
| `dialogue` | `NAME` | a line spoken by someone visible | required |
| `caption` | `CAPTION` | words placed over the picture | optional |
| `app` | `NAME` or `APP` | sound from a device in the scene | optional |
| `text` | `NAME (text)` | a message shown in a text thread | required (the sender) |

## The two formats

- `narrated`: one voice over the pictures. Nobody speaks on screen, so `dialogue` lines are refused.
- `dialogue`: characters speak on screen. All five line types are allowed. Add a continuity row for every cut that carries people, objects or doors across.

## What the build refuses

The build stops before writing anything and lists every problem, for example `panel 04 is empty`. It refuses:

- a missing `title`, `format`, `script_version` or `panels`, and any key it does not know (so a misspelt key is not silently dropped);
- a panel with no image, or with neither action nor lines;
- two panels with the same id;
- an image that is missing, outside the project, or not a readable image;
- an unknown line type, a line with no text, or a `dialogue` or `text` line with no speaker;
- a cast, prop or setting entry with no name, or with neither image nor note;
- a continuity row with only `cut` filled in.

## Revisions

Keep panel ids the same when you revise. Raise `board_version`. When a revision shows a frame taken from a real clip, say so in the action text, so nobody mistakes it for a proposal.

"Front Row" went through four storyboard versions. Every fix was one still: an empty hall that should have had an audience, a hand not attached to an arm, a flyer that needed to start flat on the table, props that had wandered into the wrong room.
