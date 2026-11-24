---
name: ai-video-maker
description: "Make a long story ad (about three minutes) with AI from an approved script. Builds cast and room reference images, lays out a storyboard for approval, estimates the cost and sets a spend ceiling, generates each scene with the person's own API keys (Replicate, Kie, ElevenLabs), and assembles the film with ffmpeg. A person approves three times: the script, the reference pack plus storyboard, and the finished film. Use when someone has a script and wants a narrated or dialogue video ad made with AI models paid by the second. Needs the person's own provider accounts. It spends their money, so it never generates video before a ceiling is set."
---

# AI video maker

This skill turns an approved script into a finished film. It works for a voiceover film (a narrator over pictures) and for a dialogue film (characters who speak on screen).

The idea is simple. Settle what the film looks like in still images, which cost cents. Only then pay for video, which costs dollars. A person approves three times. You do the work in between.

Read [SETUP.md](SETUP.md) once before the first project.

## The rules that do not bend

- **Three approvals, by a person.** The script. The reference pack and storyboard together. The finished film. Stop at each one and wait. "Go ahead" on the project is not approval of something the person has not seen.
- **No video before a ceiling.** Estimate the film, agree a ceiling with the person, and write it to the ledger. `scripts/generate.py` refuses any job that would pass it. Only the person can raise it.
- **Their keys, their money.** Keys come from environment variables. Never ask the person to paste a key into the chat. Never print one, and never write one to a file.
- **Do not change the approved script.** If a scene cannot be made as written, say so and ask. Do not quietly rewrite a line to make a model behave.
- **Do not invent prices or model abilities.** Prices live in `data/models.json`. If a price is missing or marked unchecked, say so.

## What you need from the person

- The script, with the exact words. If it is not approved yet, that is approval one.
- Which kind of film it is (see step 1), if the script does not make it obvious.
- The shape (9:16 for most social ads) and rough length.

Do not ask for anything else up front. Make sensible choices for cast, rooms and look, label them as your choices, and show them at approval two.

## The project folder

Make one folder per film and run every command from inside it. The ledger lives there.

```
my-film/
  script.md            the approved words
  approvals.md         who approved what, when, in their words
  storyboard.yaml      the storyboard source
  shots.json           the shot list for the cost plan
  ledger.json          the ceiling and every job (written by generate.py)
  assets/cast/  assets/rooms/  assets/voices/  assets/narration.mp3
  storyboard/          one still per panel
  clips/               every take, never overwritten
  outputs/             assembled films, one file per version
```

In the commands below, `SKILL` is the path to this skill's folder.

## Step 1: the script (approval one)

Read the script and decide two things.

- **The look:** animated or photorealistic.
- **The sound:** a voiceover, or characters who speak on screen.

The sound matters more for cost. A voiceover film only needs a model that animates a picture, so cheaper models work and fewer takes are thrown away. A dialogue film needs a model that makes voice and picture together, and faces, voices and lips all have to be right at once. In our two example films, each finished second of the dialogue film ("Stephen?") cost about ten times what the voiceover film ("Front Row") did.

A film where anyone speaks on screen is a dialogue film, even if it also has a narrator.

Check the script before you show it back:

- Every reaction follows from something the viewer has seen or heard.
- Every payoff has a setup.
- You can say, scene by scene, what happens and what changes.

Then show the person the exact script and the route you will use, and wait for approval. If they gave you a script they already approved, do not ask again. Write the approval in `approvals.md`.

## Step 2: the reference pack

Make one image per character and one per room. Attach those same images every time you make a scene. Words alone give you a different person each time.

The pack has four parts: the cast, the rooms with nobody in them, the voices, and only the props the story depends on. Details and prompts are in [references/reference-pack.md](references/reference-pack.md).

```
python3 SKILL/scripts/generate.py image --prompt-file prompts/cast.txt \
  --aspect-ratio 9:16 --label cast-sheet --out assets/cast/cast-sheet.jpg
```

You need a ledger before the first image. For the stills, start with a small ceiling the person agrees to (the 73 stills for "Front Row" came to under eight dollars), then raise it at step 4 when the whole film is priced.

```
python3 SKILL/scripts/generate.py init --ceiling 10 --reason "stills only, until the film is priced"
```

Look at every image you make before you use it. Check hands, faces, and that two characters cannot be mistaken for each other.

## Step 3: the storyboard (approval two)

Make one still for each moment where something happens or changes. Not one per line. "Stephen?" has 53 lines and 11 frames.

To make a panel, attach the cast image and the room image and say which is which: "Image 1 is the cast. Image 2 is the room."

Write the panels into `storyboard.yaml` with the exact words beside each one, then build the page:

```
python3 SKILL/scripts/storyboard.py .
```

That writes `STORYBOARD.html` (one file, images inside it, easy to send) and `STORYBOARD.txt`. The format is in [references/storyboard-format.md](references/storyboard-format.md). A working example is in `examples/wrong-door/`. A finished dialogue film, with every prompt and its cost, is in `examples/ten-minutes/`.

Before you show it:

- **Every word has a panel.** For a voiceover, every sentence of narration is assigned to a panel, in order, in full.
- **Read the panels together.** People stay on the same side. The same person holds the same thing. The room has not changed.
- **Cover the words.** Look only at the pictures. If you cannot follow the story, fix the pictures. Many people watch ads with the sound off.
- **Fix it here.** A wrong hand, an empty room that should be full, a prop in the wrong place: each one is a still to redo for cents. Found later it is a scene to redo for dollars.

Show the person the storyboard page with the cast, rooms and voices in it, and wait. This one approval covers the look, the cast, the rooms, the voices and the panels. Do not ask for a separate approval on each.

## Step 4: the cost and the ceiling

Do this before any video.

1. Write `shots.json`: one entry per clip you expect to make. The fields are explained in [references/cost-and-ceiling.md](references/cost-and-ceiling.md).
2. Run the planner:

   ```
   python3 SKILL/scripts/plan.py shots.json
   ```

3. For a voiceover film, test before you choose. Take the two hardest shots and make each on two or three models. This cost us about three dollars on "Front Row", and the cheapest model won.
4. Tell the person the total with retakes, and what drives it. Agree the ceiling. The total with retakes is a sensible place to put it.
5. Write it down:

   ```
   python3 SKILL/scripts/generate.py ceiling 32 --reason "plan total with 30% retakes, agreed on 24 Nov"
   python3 SKILL/scripts/generate.py status
   ```

Prices in the table are list prices on the date in the file. Rows marked unchecked have not been confirmed. Tell the person to check the provider's page before a big run. Also check that their provider balance covers the run. A batch that runs out of credit halfway leaves a mess.

## Step 5: generate the scenes

Add `--dry-run` to any job to see the exact request and the estimate without sending anything. Do this for the first job on each model.

Give every job a `--label` (the scene name). Save every take under a new file name. Nothing is overwritten.

### Voiceover route

1. **Record the narration first.** You need its real length to know how long each clip must be. If you guess from the word count, the pictures run out before the sentence does.

   ```
   python3 SKILL/scripts/generate.py narration --voice-id VOICE_ID \
     --text-file narration.txt --out assets/narration.mp3
   ```

2. **Start each clip from its storyboard frame.** The frame becomes the first image, so the clip stays close to what was approved.
3. **Ask for closed mouths.** If lips move while the narrator talks, it looks like bad dubbing.
4. **Throw away the clip's own sound.** The narration is the soundtrack. Add sound effects yourself where you want them.

   ```
   python3 SKILL/scripts/generate.py video --route grok-video \
     --prompt-file prompts/scene-01.txt --start-image storyboard/01.jpg \
     --seconds 5 --label scene-01 --out clips/scene-01-take1.mp4
   ```

Prompt writing for this route: [references/prompts-voiceover.md](references/prompts-voiceover.md).

### Dialogue route

1. **Use a model that makes voice and picture together.** Here that is Seedance 2.5. Adding speech afterwards and matching lips to it is much harder to get right.
2. **Put the exact lines in the prompt, with the speaker named before each one.** Say everyone else keeps their mouth closed.
3. **Direct the listener.** Say what the other person does while they listen.
4. **Reuse each character's voice sample.** Attach it in every scene they speak in, the same way you attach their face.

   ```
   python3 SKILL/scripts/generate.py video --route seedance-2.5-kie \
     --prompt-file prompts/scene-01.txt --seconds 16 --resolution 720p \
     --ref https://.../cast.jpg --ref https://.../boardroom.jpg \
     --voice https://.../boss.mp3 --voice https://.../stephen.mp3 \
     --label scene-01 --out clips/scene-01-take1.mp4
   ```

Prompt writing for this route: [references/prompts-dialogue.md](references/prompts-dialogue.md).

### After every take

Look at it before you make the next one. Pull frames and check them against the storyboard:

```
ffmpeg -v error -i clips/scene-01-take1.mp4 -vf fps=2 review/scene-01-take1-%03d.jpg
```

You can check pictures. You cannot judge whether a voice sounds like the same person or whether lips match. Say that plainly and leave those for the person's review.

If a job times out or the connection drops, do not send it again. Run `generate.py resume JOB_ID`. The job is already in the ledger and may already be paid for.

## Step 6: fix what is wrong

Two rules, both from the article:

- **Say what is wrong before the retake.** "Try again" gets a different take with a different problem. "He should be dozing upright, not face down on the table" gets the fix. `generate.py` will not make a second take of a label without `--reason`.
- **Replace the smallest piece that fixes it.** If two seconds are bad, redo those two seconds and leave the rest alone. Often the fix is a trim or a cut, with no new video at all.

See [references/review-and-fixing.md](references/review-and-fixing.md).

## Step 7: assemble the film

- **Voiceover film:** write an edit plan and run `scripts/assemble_narrated.py`. It lays the narration under the clips, drops each clip's own sound, and can burn captions and add an end card.
- **Dialogue film:** bring every clip to one size and frame rate with a sound track as long as its picture, then join them with one ffmpeg command.

Both are in [references/assembly.md](references/assembly.md).

Each assembled film gets a new file name (`film-v1.mp4`, `film-v2.mp4`). Captions use the words from the script, not a transcript of what the model said.

## Step 8: the finished film (approval three)

Before you hand it over, check the whole file yourself: length, every scene present and in order, no silent stretch the script did not ask for, captions spelled as in the script.

Then ask the person to watch it at normal speed, with the sound on, from the first frame to the last. Give them a short list of anything you were unsure about, with times.

The approval covers only the file they watched. If a caption changes afterwards, someone watches it again. Write the approval and the file name in `approvals.md`.

Finish with the numbers from `generate.py status`: what was estimated, what the ledger says was committed, and how much video was generated for how much finished film. Tell them the ledger holds estimates and their provider bills are the real figures.

## Where things go wrong

A short list of things that have actually gone wrong, and what to do about each, is in [references/reliability-lessons.md](references/reliability-lessons.md). Read it before the first video job.

## What this skill does not do

- It does not write the script.
- It does not pick a model for taste. It filters by what a shot needs and sorts by price.
- It does not host files. The Kie route needs https links to your reference images and voice samples (see SETUP.md).
- It does not promise a result. Models miss. The method keeps the misses cheap.
