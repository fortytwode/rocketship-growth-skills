# The reference pack

The reference pack is the set of images and sounds you attach to every scene so the film stays the same film. Make it once. Get it approved with the storyboard. Then reuse the same files every time.

## The four parts

**The cast.** One image per character, or one sheet with all of them. Fix the age, the look and the clothes.

- If someone changes during the story, show both states. "Stephen?" has a tired face and a rested face for the lead on the same sheet, and the story depends on both being clearly the same man.
- Put the characters side by side and look at them small. If two of them could be confused, change more than the clothes: face shape, hair, build.
- Add a side or back view only if the storyboard needs one.

**The rooms.** One image per location, with nobody in it. Fix where the windows, the furniture and the light are. A room that appears twice has to be the same room. Day and night can be the same room image with different light.

**The voices.**

- Voiceover film: pick one narrator from your voice library. Note the voice id.
- Dialogue film: pick a voice for each speaking character, make a few seconds of speech in it, and keep that clip. You attach it in every scene the character speaks in.

```
python3 SKILL/scripts/generate.py narration --voice-id VOICE_ID \
  --text "I think, yes. The projections look good." \
  --label voice-stephen --out assets/voices/stephen.mp3
```

A cast image cannot give a character a voice. A voice sample belongs to the character, not to where they sit or who speaks first.

**Props the story depends on, and no others.** In "Front Row" that is the violin case. An ordinary mug on a desk belongs in the room image. For each prop that carries the plot, ask three things:

- Can you read it at the size it will be on screen?
- Is it simple enough to come out the same every time?
- Does the story still need it once the words are right?

## Writing the prompts

Fix the style here. Every later image is made by attaching these, so the look carries forward.

A cast prompt names:

- the look of the film (for example "dimensional animated-film look" or "natural photograph, soft daylight");
- each person: age, build, face, hair, clothes;
- a plain background and neutral expressions;
- what must not appear: text, logos, extra people.

Name only the people who should be in the picture. Do not describe a person who is not there.

A room prompt names the room, what is in it and where, the light, and "no people".

For a face that will be animated later, start from a neutral, natural still. Put the expression in the video prompt, not in the still.

## Making a scene still from the pack

Attach the cast image and the room image, in that order, and say which is which.

```
Image 1 is the cast. Image 2 is the room.
The father from Image 1 sits in the centre of the front row of the hall in Image 2.
His eyes are closed and his chin has dropped. The seats around him are full.
Keep the look, the clothes and the room exactly as in the images.
```

```
python3 SKILL/scripts/generate.py image --prompt-file prompts/panel-01.txt \
  --ref assets/cast/cast-sheet.jpg --ref assets/rooms/hall.jpg \
  --aspect-ratio 9:16 --label panel-01 --out storyboard/01.jpg
```

## Before you use an image

Look at the image itself, not the prompt.

- Hands attached to arms. The right number of fingers.
- Objects built the way real ones are.
- No stray text or lettering.
- The face matches the cast image.
- Nothing from another scene has wandered in.

A tighter crop does not repair a bad hand. Make the still again.

If the cast changes after approval, update every panel that shows the old face before you show the storyboard again. A new cast sheet does not make old panels current.

## If the files need a link

The Kie route downloads your images and voice samples itself, so it needs https links. Upload the approved files to storage you control and use those links. Use the same links for the whole film. See SETUP.md.
