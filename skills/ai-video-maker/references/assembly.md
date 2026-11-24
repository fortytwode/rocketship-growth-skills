# Assembling the film

Both routes need ffmpeg and ffprobe. Captions and end card text need an ffmpeg build with the `drawtext` filter (see SETUP.md).

Before you plan the edit, check every clip's size and frame rate. Clips from different models come back at different sizes and frame rates.

```
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate,duration -of csv=p=0 clips/scene-01-take1.mp4
```

## Voiceover film: `assemble_narrated.py`

The script takes one narration file and a list of clips, in order. It uses the narration as the soundtrack and drops each clip's own sound. It can burn captions, mix in sound effects and add an end card.

You describe the edit in a JSON file, the edit plan. A template is in `examples/wrong-door/edit-plan.example.json`.

```
python3 SKILL/scripts/assemble_narrated.py plan     --project-root . --edit-plan edit-plan.json
python3 SKILL/scripts/assemble_narrated.py check    --project-root . --edit-plan edit-plan.json
python3 SKILL/scripts/assemble_narrated.py assemble --project-root . --edit-plan edit-plan.json
```

`plan` prints the timeline. `check` runs every test without writing anything. `assemble` makes the film.

### The edit plan

```json
{
  "schema_version": 1,
  "canvas": {"width": 720, "height": 1280, "fps": 30},
  "narration": {"path": "assets/narration.mp3", "sha256": "..."},
  "segments": [
    {"id": "01", "source": "clips/scene-01-take1.mp4", "sha256": "...",
     "in_seconds": 0, "duration_seconds": 3.5}
  ],
  "captions": {"events": [{"text": "...", "start": 0.2, "end": 3.3}],
               "font": {"path": "assets/caption-font.ttf", "sha256": "..."},
               "style": {"max_width": 620, "max_lines": 3, "min_font_size": 34, "max_font_size": 54,
                         "x": "center", "bottom_margin": 330, "font_color": "#FFFFFF",
                         "border_color": "#000000", "border_width": 6, "line_spacing": 8}},
  "sfx": [{"source": "assets/sfx/door.wav", "sha256": "...", "in_seconds": 0,
           "duration_seconds": 0.5, "timeline_start_seconds": 3.6, "gain_db": -6}],
  "endcard": {"seconds": 3, "background": "#171512",
              "logo": {"path": "assets/logo.png", "sha256": "..."},
              "logo_width": 320, "logo_x": "center", "logo_y": 260, "text": null},
  "output": {"path": "outputs/film-v1.mp4", "receipt": "outputs/film-v1.json"}
}
```

What to know:

- **Paths** are relative to the project folder and must stay inside it.
- **Every file has its checksum beside it.** This makes sure the film is built from exactly the files you reviewed. If a clip is replaced, the old checksum no longer matches and the build stops. Get a checksum with:

  ```
  python3 SKILL/scripts/filehash.py clips/scene-01-take1.mp4
  ```

- **Segments play in list order.** `in_seconds` is where to start inside the clip. `duration_seconds` is how much of it to use. Use these to trim.
- **Times sit on the frame grid.** At 30 frames a second, every time is a multiple of 1/30. Times like 3.5 or 4.2 are fine. 3.33 is not.
- **The picture must be at least as long as the narration.** If the narration is longer, the build refuses, so no words are cut off. If the picture is longer, the end is padded with silence.
- **Size.** Each clip is scaled to fill the canvas and cut from the centre. To use a different part of the picture, add `crop` to the segment: `{"x": 0, "y": 0, "width": 720, "height": 1280}`, measured on the clip.
- **Captions** are optional. Give `events` in the plan, or a `path` and `sha256` to a JSON file of events. Use the words from the script. Take the times from the real narration. One caption ends before the next begins.
- **`sfx`** is optional. Each entry is laid over the narration at `timeline_start_seconds`. `gain_db` and `fade_seconds` are optional.
- **The end card** is optional. Its `text` is `null` unless you set it. The script never invents a line for you. End card text needs a `font` unless captions already have one.
- **Output.** The film and a small JSON record of what it was built from. Both must be new names. Nothing is overwritten.

If the build stops after the film is written but before its record is, it tells you the name of a recovery file. Run `assemble_narrated.py recover --project-root . --recovery-receipt THAT_FILE`. Do not build again.

"Assembled" means the file was built. It does not mean the film is good. Watch it.

## Dialogue film: join the clips with ffmpeg

Dialogue clips keep their own sound, so the join has to keep sound and picture together.

The thing that goes wrong: a silent clip with no sound track, or a clip whose sound is a little shorter than its picture. Join those as they are and every later line plays early. By the end the words are seconds ahead of the faces.

So do it in two steps.

**Step 1. Bring every clip to one size, one frame rate, and a sound track exactly as long as its picture.** Pick the canvas (here 720 by 1280 at 24 frames a second) and use it for every clip.

For a clip that has sound:

```
ffmpeg -i clips/scene-01-take2.mp4 -filter_complex \
  "[0:v]fps=24,scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1[v];[0:a]aresample=48000,apad[a]" \
  -map "[v]" -map "[a]" -shortest -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac -ar 48000 -ac 2 \
  work/n-scene-01.mp4
```

For a clip with no sound track:

```
ffmpeg -i clips/scene-06-take1.mp4 -f lavfi -i anullsrc=r=48000:cl=stereo -filter_complex \
  "[0:v]fps=24,scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1[v]" \
  -map "[v]" -map 1:a -shortest -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac -ar 48000 -ac 2 \
  work/n-scene-06.mp4
```

To use only part of a clip, add `-ss START -to END` before `-i`. Cut the sound and the picture at the same points. Never cut inside a word.

**Step 2. Join them with one command that re-encodes.** Do not use `-c copy` to join clips from different takes.

```
ffmpeg -i work/n-scene-01.mp4 -i work/n-scene-02.mp4 -i work/n-scene-03.mp4 -filter_complex \
  "[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac outputs/film-v1.mp4
```

Add one `-i` and one `[N:v][N:a]` pair per clip, and set `n=` to the number of clips.

Then check:

- the film's length equals the sum of the clips;
- lips match at the first line after every silent clip;
- there is no silent stretch the script did not ask for.

**Captions on a dialogue film.** Write the words from the script, not from a transcript. A transcript copies the model's mistakes. Take the times from the real clips. The simplest way is a subtitle file (`.srt`) burned in with ffmpeg's `subtitles` filter, if your ffmpeg has it, or added in any video editor.

**Music.** If the script asks for music, make it and mix it in. Keep it well under the voices and bring it up only where nobody speaks. Check that its loudest part lands where the film needs it.
