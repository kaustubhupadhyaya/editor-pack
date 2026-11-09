---
name: reel-maker
description: >-
  Recreate a short-form video / reel from a reference link using the user's own
  footage. Use when the user gives an Instagram, YouTube, TikTok, or any video
  link and wants a "similar" reel built from their clips. The skill downloads the
  reference, reverse-engineers its cut timings / transitions / on-screen text /
  layout / audio into a recipe, motion-picks the best (non-shaky) moments from the
  user's footage, and assembles version 1 matching the reference. Triggers:
  "make a reel like this", "copy this reel/video", "recreate this with my footage",
  "reel-maker", or any reference-link + footage-folder pair.
---

# reel-maker

Turn a reference short-form video (from any link) into **version 1** of a similar
reel built from the user's own footage. The goal is a faithful *structural* copy —
same rhythm, layout, caption style, and energy — using the user's clips.

Detailed copy-paste commands live in [references/cookbook.md](references/cookbook.md).
Read it before running ffmpeg/yt-dlp steps. All ffmpeg work is deterministic — do
it directly; don't delegate the signal analysis to sub-agents.

**Sibling skills in this repo** — reel-maker orchestrates them, don't duplicate them:
use **clip-picker** to survey/rank the good windows out of long raw footage (rubric +
contact sheets + sharpness), and **video-tool-router** to choose the right
encoder/tool (stream-copy vs re-encode, the HLG→SDR guard, Resolve for grade/stabilize).
See [[editor-pack-video-editing-stack]] for the installed toolchain.

## Core principles (what "copying a reel" means here)

1. **Reference = a recipe, not pixels.** Extract its *structure* (cut timings,
   transition style, layout geometry, caption script+timing+style, audio) and
   rebuild that structure with the user's footage.
2. **Match the cut timings exactly.** Transitions go at the *same timestamps* as
   the reference. This is what makes it feel like the same edit.
3. **Pick by motion, avoid shake.** From each source clip choose the FASTEST /
   most vivid window (high per-second motion), but AVOID jittery/jarring parts
   (high frame-to-frame variance). Never default to the calm middle of a clip.
4. **Keep every hard cut hidden by motion.** Reference reels almost never dissolve
   — they hard-cut on whip-pans, sun-flares, and speed-blur. Replicate with hard
   cuts; the user's motion-blur footage hides them naturally.
5. **Confirm only the few decisions that change the whole render** (layout/aspect,
   border, caption source, audio source). Everything else, decide and proceed.
6. **Verify visually at every stage** with contact sheets / frame reads. Flag
   shaky frames rather than shipping them.

## Workflow

### Phase 0 — Prereqs
- Find `ffmpeg`/`ffprobe` (PATH, else the WinGet Gyan build). Confirm `yt-dlp`
  (`python -m yt_dlp`). Install yt-dlp with `pip install --user yt-dlp` if missing.
  ffmpeg is required for DASH merge. See cookbook §0.
- For caption timing, install **stable-ts** (`pip install --user stable-ts`) — a
  captioning-grade Whisper wrapper that yields word-level timestamps. WhisperX is
  more accurate but often won't install (no wheels on the newest Python); stable-ts
  is the reliable choice. It shells out to ffmpeg, so ffmpeg MUST be on PATH.

### Phase 1 — Acquire the reference
- Download with `yt-dlp` (anonymous first; `--cookies-from-browser` only if it's
  login-walled — and that fails while the browser holds the cookie DB lock).
- Instagram/TikTok reels are served as `blob:`/MSE streams — never try to grab the
  blob URL; `yt-dlp` pulls the DASH video+audio and merges them. Cookbook §1.
- Also grab `--write-info-json` alongside the download — its `description` field is
  the reel's **written caption** (hashtags, context) as posted, distinct from any
  burned-in on-screen text handled in Phase 2c. Useful context, and occasionally
  contains the exact wording of on-screen text the poster typed out. Cookbook §1.
- Save to a known path; keep it as the audio/timing reference.

### Phase 2 — Reverse-engineer the reference into a recipe
Produce a structured recipe with:
- **Duration & fps** (ffprobe).
- **Cut timestamps** → scene detection (`select='gt(scene,0.25)',showinfo`).
  Note the count and the per-shot durations. Cookbook §2a.
- **Layout geometry** → `cropdetect` to find the active picture area (e.g. a
  ~square band centered on a 9:16 canvas with black bars; or full-bleed; note any
  border). Cookbook §2b.
- **On-screen text** → build a 2 fps contact sheet, READ it (Read the image), and
  reconstruct the caption script + styling (font weight, color, ALL-CAPS keyword
  emphasis, position). Zoom hi-res frames for hard-to-read lines. Cookbook §2c.
- **Captions = combine two tools (do this, it's the tested method):**
  1. **TIMING from stable-ts** — extract the reference audio to 16 kHz mono WAV, run
     stable-ts for word-level timestamps, anchor every cue's start/end to those word
     times. This is the single biggest fix for caption drift. Cookbook §2d.
  2. **WORDS from the reference's burned-in captions** — stable-ts (ASR) MISHEARS
     (e.g. it heard "owned in my life" as "are only in my life"). So read the
     reference's on-screen caption text (crop the caption band, upscale, Read it —
     Cookbook §2c) and use THOSE words, matched to the stable-ts timings. Never trust
     the ASR transcript for wording; trust it only for timing.
- **Audio** → ffprobe the audio stream; note whether it's voiceover, music, or both.
- **Transition style** → note that cuts coincide with motion/flare (hard cuts).

### Phase 3 — Gather & probe the user's footage
- Locate the footage folder the user named. Probe every clip (w×h, fps, duration).
- Read any playlist (`.m3u`) for the user's intended clip order / descriptions.

### Phase 4 — Clip-pick by motion (the main lever)
- If the user hands you **long raw footage** (not pre-cut clips), first run the
  **clip-picker** skill to survey and rank the keeper windows against its rubric —
  that is the canonical selection method. reel-maker then places the chosen clips
  into the reference's fixed cut slots with the motion-window picker below.
- For each clip compute per-frame MAFD via `scdet`, then per-1-second buckets:
  **mean MAFD = motion/vividness**, **stdev of MAFD = shake/jitter**. Cookbook §4.
- For each segment (duration set by the reference's cut list) pick the window with
  the highest mean that fits the clip, EXCLUDING high-jitter windows (stdev
  > ~0.6). Center on the peak. Reused clips must take a *non-overlapping* window.
- If a chosen window is shaky/jarring, shift ±2–3s to a steadier window; flag the
  bad region (see Phase 7).

### Phase 5 — Confirm the render decisions
Ask the user (AskUserQuestion) only these, with visual previews where useful:
- **Layout / aspect** — match reference exactly (e.g. square band on 9:16 + bars),
  full-bleed vertical 9:16, 4:5/3:4 box, or keep 16:9. Center-cropping 16:9 →
  vertical loses the sides; say so.
- **Border** — none / hairline / thicker; give px.
- **Captions** — reuse the reference's exact script, a new script in the same
  style, or none.
- **Audio** — user-provided track, reference audio (flag: personal-draft only, not
  for reposting someone else's audio), or silent.

### Phase 6 — Assemble version 1
- Build one segment per reference cut, from the picked window, applying the chosen
  layout filter (square-in-9:16 / full-bleed / border — Cookbook §6a). Re-encode
  each, then concat via the demuxer. Cookbook §6b.
- Captions: write an **ASS** subtitle file (word-by-word cues, styled: base +
  Key/emphasis, CAPS keywords, lower-third). **Anchor every cue's start/end to the
  stable-ts word timestamps** (Phase 2d) so captions are frame-accurate to the VO —
  never hand-time them. Burn with the `subtitles` filter. Cookbook §6c. (ASS is far
  cleaner than chained drawtext.)
- Add audio (chosen source) and any end fade (`fade`/`afade`, e.g. 3s to black).
- Output to the user's project folder as `reel_<name>_v1.mp4`; keep versions.

### Phase 7 — Verify & flag
- Contact-sheet the montage; Read it to confirm layout/border/picks.
- Frame-check caption moments and the fade tail.
- If any pick is shaky, replace it AND save a flagged reference frame (black banner
  + red border, source range labelled) to a `_flagged_shaky_frames/` folder so
  those source ranges are avoided next time. Cookbook §7.

## Output
Report: the deliverable path + specs, the recipe you extracted (cut list, layout,
caption script, audio), which clips/windows you used, honest caveats (aggressive
crop, all-POV vs alternating, borrowed-audio repost caveat), and easy next-tweak
dials (border, crop, swap picks, caption timing, fade length).

## Gotchas (Windows ffmpeg) — see cookbook for fixes
- `yt-dlp --cookies-from-browser` fails while the browser locks its cookie DB.
- `drawtext` needs the fontfile path **quoted**: `fontfile='C\:/Windows/Fonts/arialbd.ttf'`.
- Filled `drawbox` with `@alpha` throws `-22`; use opaque, and render boxes and
  text in **separate passes**.
- Prefer `subtitles=` (ASS) over stacked `drawtext` for captions.
- `tile` contact sheets warn about "image sequence pattern" but still write the file.
