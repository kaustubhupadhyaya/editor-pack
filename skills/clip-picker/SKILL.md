---
name: clip-picker
description: >-
  Pull the good short clips out of long, unstructured video. Use whenever the user dumps one or more
  lengthy videos and wants the highlights / best moments found and cut - "find the good clips", "pick
  the best bits", "get highlights from this footage", "what's worth keeping", "clip this ride/vlog/gameplay".
  Works by sampling frames into timestamped contact sheets Claude can actually read (it cannot stream
  video), scoring candidates for sharpness, and returning a ranked pick-sheet + cut clips. Ships a default
  POV-motorcycle-ride rubric and generalizes to any footage by restating the rubric. Trigger words: find
  clips, best clips, highlights, good moments, worth keeping, montage sources, b-roll, cut the good parts,
  scan footage, pick from this video, review reel.
---

# Clip Picker

Find the keepers in long footage. The whole method exists to work around one hard limit:

> **Claude cannot watch video.** It reads still images. So "picking good clips" is really: sample the
> footage into images densely enough to see what happens, judge those against a rubric, confirm at full
> resolution, then cut. Trimming is trivial and near-instant; *seeing 30+ minutes of footage* is the
> real cost — budget for it and don't apologize for it.

Scripts live in `scripts/` (`survey.py`, `score.py`, `board.py`). They handle the mechanical parts;
Claude does the visual judgment in between. Deps: ffmpeg/ffprobe on PATH, numpy, pillow (all installed
on this machine — see [[editor-pack-video-editing-stack]]).

## Pipeline

Pick a WORKDIR in the scratchpad (e.g. `.../scratchpad/clippick`). Then:

**1. Confirm the rubric.** If the footage isn't POV motorcycle/cycling (the built-in default below),
ask the user 1-2 questions: what makes a clip good *to them*, desired clip length, output shape
(16:9 vs 9:16), and how selective (a few gems vs everything). One good sentence of criteria changes
every pick — get it before surveying.

**2. Survey — turn footage into readable contact sheets.**
```
python scripts/survey.py "C:\path\to\videos_or_dir" --out WORKDIR --interval 4
```
One thumbnail every 4s, tiled 6x6, source timecode burned on each. For fast/eventful footage use
`--interval 2-3`; for slow footage `--interval 6-8`. It prints each clip's specs and **flags HDR
(HLG/PQ)** sources. On many/long HEVC clips this is slow (software decode is the bottleneck) — run it
in the background and view sheets as they land.

**3. Visually survey every sheet.** `Read` each `WORKDIR/montages/*_sheet_*.jpg`. Map candidate windows
by timecode against the rubric. A blink-and-miss event *between* samples can be missed — if a sheet
hints at something (a flash of a corner, a figure), note it and sample that span denser later.

**4. Author `WORKDIR/candidates.json`** — one object per candidate window (schema below). Keep each
window 15-30s (or the user's length). Give each a `pick` code (P01, P02, ... in your ranked order), a
one-line `title` (why it qualifies), and `tags`.

**5. Confirm at full-res + sharpness.**
```
python scripts/score.py --work WORKDIR
```
Pulls a full-res hero frame per candidate, scores variance-of-Laplacian (a focus metric), and tiles
them into `review.jpg`. `Read` it. Drop any that are genuinely soft/uninteresting; nudge `start`/`end`
to land the best beat. **Low sharpness = whole-frame motion streak — desired for flow shots, not a
reject.** Re-run after edits.

**6. Present a ranked pick-sheet (the user chooses).**
```
python scripts/board.py --work WORKDIR --mode thumbs     # -> WORKDIR/pickboard.html (publishable as an Artifact)
```
Or, when they want to *watch* before choosing, a playable board that also cuts the clips:
```
python scripts/board.py --work WORKDIR --mode play --out "%USERPROFILE%\Downloads\clips"
```
`play` cuts each candidate to browser-friendly H.264 (auto HDR->SDR tone-map for HLG/PQ), writes
`review_player.html` with native `<video>` players next to the mp4s (open in Chrome/Edge for local
playback). Default output is **16:9, trim-only, no crop.**

**7. Cut / finish the winners** the user picks. For pixel-exact or format choices, route through the
[[editor-pack-video-editing-stack]] `video-tool-router` skill (stream-copy vs re-encode, the HLG guard,
Resolve for grade/stabilize/upscale). Offer follow-ups: motion-blur boost on flow clips, 9:16 verticals,
color, captions, a montage.

## The selection rubric (this is the actual "how it picks")

A clip is **worth keeping** when it clears the quality gates AND carries at least one "special" signal.

### Quality gates (all must hold)
- **Subset, 15-30s** of the source (default; honor the user's length).
- **Subject clear and stable** — the main subject (bike/handlebars, face, player) is sharp and steadily
  framed. Reject *global* blur from bumps/shake, out-of-focus, blown-out or too-dark, lens obstructed.
- **Something is happening** — motion or change. Reject idle/parked/waiting and monotonous nothing.

### "Special" signals (need >=1; more + stronger = higher rank)
- **Speed / motion** — the surroundings streak with motion-blur while the subject stays sharp. This is a
  *positive*, and prime raw material for a motion-blur-boost edit.
- **Interaction** — other people/riders/vehicles/animals; overtakes, group riding, someone reacting.
- **Action / dynamics** — cornering & lean, turns, elevation change, a trick, a near-moment.
- **Light & environment** — sunrise/sunset, golden hour, dramatic clouds, mist/fog, tunnels, a vista or
  reveal, water. Time-of-day light is the single biggest "cinematic" lever — always flag it.
- **Novelty** — a scene/terrain change, an entrance/exit, anything visually new vs the surrounding minutes.

### Reconciling "clear, not blurred" with "motion blur is good"
Keep the **subject sharp and the moment intentional**; treat **peripheral speed-blur as a feature**.
Reject only *global* blur (whole frame smeared by a bump). A chest/bar-mounted camera keeps the bike
sharp while the world streaks — that is the wanted look, not a defect.

### Ranking
Order by: strength & rarity of the special signal, then clarity, then variety (don't rank five
near-identical windows in the top slots — spread the vibes: a vista, a blur-flow, an overtake, an
atmospheric one). Be honest when a criterion is simply **absent** (e.g. no sunrise/sunset anywhere) —
say so rather than forcing a weak pick.

### POV-ride profile (built-in default for this user's footage)
Motorcycle/cycling helmet-or-bar cam. Concretely: keep speed-blur sweeps, cornering/lean, overtakes &
group riding, roadside life, mist/fog and ghat vistas, tunnels/tree avenues. Skip: stops, signals,
parking, phone-fiddling, long straight nothing. (This is exactly the rubric that produced the clip set
the user liked.)

## candidates.json schema

```json
[
  {"pick":"P01","clip":"clip_6318","file":"%USERPROFILE%/Downloads/clip_6318.mov",
   "start":96,"end":120,"hero":104,"title":"Tall-tree tunnel, riders ahead, heavy speed-blur",
   "tags":["speed-blur","scenery"]}
]
```
- `start`/`end`/`hero` are **seconds**. `hero` is a representative instant inside the window (used for
  the thumbnail and sharpness score) — pick the most characteristic beat, not necessarily the midpoint.
- `pick` codes double as the user's handle for choosing ("cut P01, P03, P10").
- `tags` (free, but these get pretty labels): speed-blur, corner, vista, interaction, overtake,
  atmosphere, scenery, action, light, wildlife.

## Efficiency & gotchas

- **HEVC/HDR decode is the bottleneck**, not the editing. A survey of ~30 min of HEVC is minutes of
  decode. Sample only as densely as the footage warrants; background the survey and the play-mode encode.
- **HLG/HDR footage greys out** on a naive SDR H.264 export — `board.py play` auto-detects and
  tone-maps, but any manual cut of this footage must too. See the HLG guard in `video-tool-router`.
- **Go faster on request:** if the user names the vibe up front ("just the mountain corners and
  tunnel-blur"), survey at a coarser interval, shortlist only those, and encode just the handful they
  want — collapses a 20-minute pass to a few minutes.
- **Don't fabricate.** Only claim what a sampled frame actually shows. If unsure whether a 1-2s event
  exists, sample that span denser before asserting it.
- **Windows font path** is hard-coded in the scripts (`C:/Windows/Fonts/...`); adjust on other OSes.
