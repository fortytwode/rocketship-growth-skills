# Reviewing and fixing

## Who checks what

**You (the assistant) check every take as it arrives.** Pull frames and look at all of them.

```
ffmpeg -v error -i clips/scene-03-take1.mp4 -vf fps=2 review/scene-03-take1-%03d.jpg
ffmpeg -v error -ss 4 -to 7 -i clips/scene-03-take1.mp4 -vf fps=10 review/scene-03-action-%03d.jpg
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate,duration -of csv=p=0 clips/scene-03-take1.mp4
```

The first command gives an overview. The second looks closely at one action. The third tells you the size, the frame rate and whether there is a sound track.

Look for:

- faces that match the cast image, start to end;
- people on the right sides, props in the right hands;
- the action the prompt asked for, at a believable speed;
- hands and objects that stay whole;
- no lettering that appeared by itself;
- on a voiceover clip, closed mouths;
- on a dialogue clip, the right mouth moving on each line.

**The person checks the finished film.** At normal speed, with the sound on, from the first frame to the last.

A transcript that matches the script only tells you the words are there. It does not tell you whether the voice still sounds like the same person, whether the lips match, or whether a hand goes through a table at second forty. Frames do not tell you about sound at all. Be plain about what you could not check, and give the person a short list with times.

## Before a retake: say what is wrong

"Try again" gets you a different take with a different problem.

Write down, in one or two sentences:

- what the take did;
- what it should have done.

"He is face down on the table and writing on the report. He should be dozing upright, and he does not write."

Then put the fix in the prompt as a plain statement, and pass the reason to the script. It will not make a second take of the same label without one.

```
python3 SKILL/scripts/generate.py video --route seedance-2.5-kie ... \
  --label scene-01 --out clips/scene-01-take2.mp4 \
  --reason "Take 1 put him face down on the table and writing. He should doze upright."
```

When the person gives you a note, that note is the reason. Use their words.

Check the retake for everything, not only the thing you fixed. A retake that fixes the pose can lose the prop.

## Replace the smallest piece that fixes it

Ask in this order.

1. **Can an edit fix it?** Trim the bad second. Cut away earlier. Cover a moment with a frame from another take. No new video.
2. **Can a short new clip fix it?** If two seconds are bad, make those two seconds (or the shortest clip the model allows) and cut it in at an existing cut.
3. **Does the whole clip need redoing?** Only if the problem runs through it.
4. **Does the scene need rethinking?** If two takes failed the same way, the prompt or the still is the problem. Change that. Do not buy a third take of the same thing.

Keep everything that was good. Do not remake scenes that nobody complained about.

A worked case, from the opening of "Stephen?". The first take ran 24 seconds. The model put Stephen face down on the table and had him writing on his report, which was not in the script. Both problems were written down. The retake asked for him dozing upright with a smaller spill, and the scene was shortened to 16 seconds. The second take worked. The first was estimated at $7.56 and the retake at $5.04.

## After a fix

- Build a new film file with a new name. Do not overwrite the last one.
- If a clip's length changed, move the captions and sound effects that come after it.
- Watch both joins around the new piece.
- If a later clip began from the last frame of the one you replaced, check that it still matches.
- The approval the person gave covers the file they watched. A new file needs a new look, even for a caption change. They only need to re-check what changed and the film as a whole. Do not reopen the script or the storyboard unless the change touches them.

## Before you hand over the film

Check the file itself.

- Length is what you expect.
- Every scene is there, in order.
- No silent stretch longer than about three seconds that the script did not ask for:

  ```
  ffmpeg -hide_banner -i outputs/film-v1.mp4 -af silencedetect=noise=-50dB:d=3 -f null - 2>&1 | grep silence_
  ```

- On a dialogue film, lips still match at the first line after every silent clip.
- Captions read as the script does, and no two captions overlap.
- Where a caption and an end card show the same words at the same time, keep one.
