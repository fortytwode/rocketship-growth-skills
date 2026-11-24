# Prompts for a dialogue film

On this route one model makes the picture and the voices together, with lips that match. Here that model is Seedance 2.5, bought through Kie or Replicate.

Everything that can go wrong on the voiceover route can go wrong here too. On top of that: the wrong voice, the wrong person speaking, and lips that drift.

## What goes in each job

- The cast image, the room image, and a still of this scene if you have one.
- A voice sample for each character who speaks in the scene.
- A prompt with the action, the seating, and the exact lines.

Reference images and voice samples are numbered in the order you pass them. Name them in the prompt as `[Image1]`, `[Image2]`, `[Audio1]`, `[Audio2]`.

```
[Image1] is the opening picture and the state of the scene.
[Image2] is the cast. [Image3] is the boardroom.
[Audio1] is the boss's voice. [Audio2] is Stephen's voice.
Begin from the composition of [Image1].
```

A voice belongs to a character. It does not follow who sits on the left or who speaks first. Say whose voice each sample is.

You cannot combine a start frame with reference images or voice samples on this model. For a dialogue scene, put the opening picture first in the reference list and tell the model to begin from it. The model may reinterpret that opening a little. Check the first second of the take.

## The four rules

**1. Put the exact line in the prompt.** Keep stage directions away from it. A note like "quietly, almost to himself" written next to a line tends to get spoken aloud. Put how a line is said in its own sentence, before the lines. Say that the quoted lines are the only words spoken.

**2. Say who speaks each line, in order.** Name the speaker before every line. Say that everyone else keeps their mouth closed.

**3. Direct the listener too.** Say what the other person does while they listen. Give them something to do you can see: a blink, a glance down, checking notes. "Shocked" is not a direction.

**4. Reuse each character's voice sample.** Attach it in every scene they speak in, the same way you attach their face.

## A worked prompt

This is what went to the model for the retake of the opening of "Stephen?", a 16-second scene. Four faces, the boardroom, and voice samples for the boss and Stephen were attached.

> **Action.** 0 to 1 second: tight shot of the cup only, a small trickle of coffee onto a printed report with tiny correction marks, all print blurred and illegible. The boss sharply says "Stephen!" straight away, off screen. 1 to 2 seconds: Stephen opens his eyes and jolts upright. He was dozing upright, not lying on the table. Then the question, a hesitant answer, a cold rebuke, "Page six." David silently checks his notes. End on an embarrassed Stephen. The cup stays tipped and the spill remains.
>
> **Seating.** All four stay seated: Stephen in the foreground on the left, David beside him further up the left side, the boss at the far head of the table, the client opposite Stephen on the right.
>
> **Lines.**
> BOSS: Stephen!
> BOSS: Your thoughts on Q4?
> STEPHEN: I think... yes. The projections look good.
> BOSS: We've spent ten minutes discussing why they don't.
> BOSS: David. Page six.

"Dozing upright, not lying on the table" is there because the first take got exactly that wrong.

Three blocks: what happens and when, where everyone is, and the lines. Keep that shape.

## More things that help

- **Seating and sides.** Say where each person is and that they stay there. If where someone stands matters to the story (overhearing, watching), put that position into the scene still. A sentence asking them to "stand closer" on a retake rarely works.
- **Time enough for the words.** A clip must be long enough for its lines at a natural pace, with a beat of quiet before the first word and after the last. A first word that starts at zero is often clipped.
- **What must not happen.** If a take got something wrong, name it in the retake: "He does not write on the report."
- **Props stay put.** Say what stays in whose hand, and what state it ends in.
- **Phone screens.** Keep the screen turned away from the camera unless you will add a real screen in the edit.
- **Fragile actions get their own clip.** A car pulling away, hands with small objects, anything with readable text. If it fails, it should not take good dialogue down with it.
- **Long scenes.** A scene usually needs several clips. "Stephen?" has 11 scenes and took 52 clips, retakes included.

## Checking a take

Pull frames and compare them with the cast and room images.

- The right person's mouth moves on each line. Listeners' mouths stay closed.
- Faces match the cast image, all the way through.
- People are on the sides the prompt gave them.
- Props are in the right hands and the right state.
- The first and last words are whole.

What you cannot check from frames: whether the voice is the same voice as last scene, whether a word is pronounced right, whether the lips are tight to the sound. Do not claim those. List them for the person's review, with times.

## Choosing between Kie and Replicate for Seedance

- Kie accepted realistic faces when we tested it. The price table has its rate at 720p.
- Replicate was cheaper per second in our table. The table marks realistic faces on that route as untested.
- Kie needs https links for every image and voice sample. Replicate takes local files from this script.

Run one short clip on the route you plan to use before you commit a whole film to it.
