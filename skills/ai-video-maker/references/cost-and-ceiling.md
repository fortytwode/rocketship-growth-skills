# The cost estimate and the spend ceiling

Work the cost out before any video. Then write down a number you will not go past. Without one, nothing stops an afternoon of retakes.

## Write the shot list

`shots.json` has one entry per clip. See `examples/wrong-door/shots.json`.

| Field | What to decide |
|---|---|
| `seconds` | How long the clip runs in the finished film. |
| `dialogue` | True if someone speaks on screen and lips must match. A voiceover is not dialogue. |
| `photoreal_faces` | True if a realistic human face is clearly visible. Some routes refuse these. |
| `reference_images` | How many images the clip must stay consistent with. A start frame counts as one. |
| `resolution` | `480p`, `720p` or `1080p`. Use 720p unless asked for more. |
| `only` | Optional list of model ids, when you have already chosen the model. |

Also fill in:

- `stills`: how many reference and storyboard images, and how many revisions of each you expect. "Front Row" used 73 stills across four storyboard versions.
- `voiceover.characters`: the number of characters in the narration.
- `retake_rate`: the share of extra video you expect to pay for.

## What retake rate to use

These are from two films, not a rule.

- Voiceover film: we planned 30% extra. "Front Row" generated 4 min 54 s of video for a film of 3 min 34 s.
- Dialogue film: plan for much more. "Stephen?" generated 7 min 11 s for about 3 minutes. That is a retake rate of about 1.4.

## Run the planner

```
python3 SKILL/scripts/plan.py shots.json
python3 SKILL/scripts/plan.py shots.json --json --out plan.json
```

It picks the cheapest model in `data/models.json` that can do what each clip needs, splits long clips, and adds the retake allowance.

The planner knows more models than `generate.py` can call. `generate.py` calls these: Nano Banana Pro, Grok video, Kling 3.0, Seedance 2.5 on Kie, Seedance 2.5 on Replicate, and ElevenLabs. If the plan picks something else, either limit the shot with `only`, or buy that model another way.

## Test before you choose (voiceover films)

Once the storyboard is approved, take the two hardest shots and make each on two or three models. Pick the cheapest one that holds up. On "Front Row" this cost about three dollars and the cheapest model won.

Count the test in the ceiling.

## Set the ceiling

The plan total with retakes is a sensible ceiling. Agree it with the person, then:

```
python3 SKILL/scripts/generate.py ceiling 32 --reason "plan total with 30% retakes"
python3 SKILL/scripts/generate.py status
```

How the ledger works:

- Every job is written to `ledger.json` before it is sent, with its model, seconds, estimated cost and output file.
- A job that would take the total past the ceiling is refused. Nothing is sent.
- A job the provider rejected or reported as failed is kept in the ledger but not counted.
- A job whose fate is unknown (the connection dropped while sending) is counted, because it may have been accepted.
- Raising the ceiling is the person's decision. Ask. Record their reason.

## What to tell the person

- The total, and the two or three things that drive it.
- That the prices are list prices on the date in the table, and which rows are unchecked.
- That the ledger holds estimates. Their provider bills are the real numbers.
- That Kling 3.0 on Replicate has no row in the table. Read the price on the model's page and pass `--rate`.
- That they should check their provider balance covers the run plus retakes before a batch.
