# What goes wrong, and what to do about it

Each of these happened on a real film. None is a rule about taste. Read the list before the first video job, and again when a take surprises you.

## Cast and look

1. **Two characters who look alike.** Each one can be fine alone and still be hard to tell apart in the film. Put them side by side, small, before the storyboard goes out. Change more than the clothes.
2. **A recast that did not reach every panel.** After a cast change, update every panel, including people half in frame and faces inside a "room" image. A new cast sheet does not make old panels current.
3. **A face that warps when animated.** Start a realistic face from a neutral, natural still. Put the expression in the video prompt. Use a model chosen for realism for that shot, not the cheapest one. Check the eyes and mouth in the first second.

## Script and plan

4. **The script and the plan drift apart.** Before generating, check the latest approved script against the storyboard and your prompts. Account for every spoken line, every silent action the story needs, every on-screen text and the ending.
5. **A reaction with no cause.** If a character reacts, decides or asks something, the viewer must have seen or heard the reason first.

## Voices and lines

6. **A stage direction gets spoken.** "She says softly, stricken:" next to a quoted line came out as extra words. Put how a line is said in its own sentence. Keep the quoted line alone. Say the quoted words are the only words spoken.
7. **The wrong person seems to speak.** Name the speaker before every line and tell listeners to keep their mouths closed. Check the frames for whose mouth moves. Frames cannot tell you whose voice it is. Leave that for listening.
8. **A clipped first or last word.** Leave a beat of quiet before the first word and after the last. Adding silence in front of a word that is already cut does not bring it back.
9. **A voice that is right for speech and wrong for singing.** A speaking sample does not make a character sing well. If a scene needs singing, test that on its own.

## Rooms, props and positions

10. **Rooms and props that drift between scenes.** Text does not hold a room in place. Attach a still of the scene as it is now, as well as the cast image.
11. **A small prop that changes shape.** A hair tie became a coil in one take and a bracelet in another. Before approval, ask whether each plot prop reads at screen size, survives being regenerated, and is needed at all.
12. **Someone standing in the wrong place.** Where a person stands is set by the scene still. Asking for "closer" in a retake prompt rarely moves them.
13. **A car that turns round mid-shot.** A moving vehicle with a moving camera is fragile. So are hands with small objects, and readable text. Give each of these its own short clip so a failure does not cost you good dialogue.
14. **A phone or app screen that looks wrong.** Right words on a made-up screen still look fake. Use a real screen recording added in the edit, or keep the screen out of view.

## Sound and joins

15. **Words that play ahead of the faces.** Silent clips had no sound track, and joining them as they were pushed every later line early. Give every clip a sound track exactly as long as its picture, then join with one command that re-encodes. See [assembly.md](assembly.md).
16. **A silent stretch nobody planned.** "Music only" in a script means music has to be made. Scan the finished film for silence longer than about three seconds.
17. **Music that fades before the big moment.** Generated music has its own shape. Listen to where it swells, and move or trim it so that lands where the film needs it. Keep it under the voices.
18. **Clips at different sizes and frame rates.** One model returned 24 frames a second at an odd size, while other footage was 30. Check every clip, bring them all to one size and rate, and review the converted clip.
19. **Captions with the model's mistakes in them.** Captions built from a transcript copied a mispronounced word and a soundalike. Write captions from the script. Make each one end before the next begins.

## Reviewing

20. **A hold that hid in a busy frame.** One background person froze while everything else moved. Look at each person in the frame over time, not only the one speaking.
21. **A check that proved less than it seemed.** A matching transcript shows the words are there. Frames show the picture. Neither shows that a voice is the same person or that lips are tight. Say which things you checked and which you could not.
22. **A retake that fixed one thing and broke another.** Check a retake for everything.
23. **Remaking more than needed.** A four-second replacement cut in at an existing cut fixed a scene. Replace the smallest piece, then re-time the captions after it.

## Running jobs and money

24. **Sending a job twice.** If a job times out or the connection drops, it may still be running and paid for. Collect it with `generate.py resume JOB_ID`. Do not send it again.
25. **Running out of credit halfway.** Before a batch, check the provider balance covers it plus retakes.
26. **Result links that expire.** Download each result at once. `generate.py` does this for you.
27. **A refusal on realistic faces.** Some routes refuse some images of realistic people. A refusal is about that request on that route. Do not try to dodge it by rephrasing. Tell the person, and try another route only if they agree.
28. **One good clip taken as proof.** A short test that works shows that one test worked. Say what you actually showed: one clip, one route, one film.
29. **Counting only the first pass.** Report the first pass, the retakes and the fixes separately. A low retake count means little if the film needed heavy trimming to work.
