# Prompts for a voiceover film

On this route the video model animates a still. Nobody's mouth has to match anything. The narration is recorded separately and laid under the pictures.

## The order of work

1. **Record the narration first.** One read of the whole script.
2. **Measure it.** Find where each sentence starts and ends in the real recording.

   ```
   ffprobe -v error -show_entries format=duration -of csv=p=0 assets/narration.mp3
   ffmpeg -hide_banner -i assets/narration.mp3 -af silencedetect=noise=-35dB:d=0.25 -f null - 2>&1 | grep silence_
   ```

   The second command lists the pauses. Match them to the sentences, then listen to check. If you have a transcription tool that gives word times, use that instead.
3. **Give each panel its time.** A panel's clip must cover its sentence, from the start of the sentence to the start of the next. Round up to a whole second the model allows. The edit trims the extra.
4. **Compare the spoken words with the script.** If the voice dropped or changed a word, record it again before you make any clips.

Never work out clip lengths from the word count.

## What goes in each job

- The storyboard frame as the start image.
- A prompt that mostly says what stays the same.
- The clip length from step 3.

```
python3 SKILL/scripts/generate.py video --route grok-video \
  --prompt-file prompts/scene-01.txt --start-image storyboard/01.jpg \
  --seconds 5 --label scene-01 --out clips/scene-01-take1.mp4
```

## How to write the prompt

This is the prompt for the shot in "Front Row" where the father falls asleep. The storyboard frame went in as the start image.

> Locked stage-facing camera, one continuous five-second shot. The exhausted man in the navy overshirt at the center of the front row slowly loses his struggle to stay awake. His heavy eyelids close fully, his chin sinks a little toward his chest and his shoulders slacken; he remains asleep through the end. His hands stay resting on his thighs. The seated audience retains its exact faces, clothes, positions and calm attentive behavior. The daughter in the foreground continues a restrained bow stroke. The man is asleep and does not speak; every other person keeps lips resting naturally. Preserve the dimensional animated-film look, camera framing, chair positions and existing props.

Most of it says what must stay the same. Only two sentences describe what moves.

Write yours the same way:

- **The camera.** Locked, or one named move. Say it is one continuous shot.
- **One action.** What moves, how far, and how it ends. Start from what the frame already shows.
- **Hands.** Say where they stay. Hands are where clips most often go wrong.
- **Everyone else.** They keep their faces, clothes and places.
- **Mouths.** Nobody speaks. Lips rest naturally.
- **The look.** Keep the style, the framing and the props.

Things to avoid:

- Two actions in one clip. Make two clips.
- Asking for an object to appear and be placed in the same clip. Start with it already in the frame.
- Readable text in the picture. Add words as captions in the edit.
- A moving vehicle with a moving camera in a clip that also carries something important. Give it its own short clip.

## Sound

Do not use the sound that comes with the clip. The assembler drops it. If you want a sound effect (a door, a cup), add it yourself as an `sfx` entry in the edit plan. See [assembly.md](assembly.md).

If the script says "music only" for a stretch, that is something you have to make and add. It is not a gap to leave.

## Checking a clip

Pull frames and look at all of them, first to last.

- Mouths closed all the way through.
- The action happened, at a believable speed.
- Hands and props stayed whole.
- Nothing in the background changed.
- No lettering appeared.
