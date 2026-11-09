---
name: reel-transcribe
description: >-
  Transcribe ONE reel / short video: the spoken words (closed-caption-style
  text) plus the post caption, given an Instagram reel URL/shortcode, "my
  latest saved reel", or any yt-dlp-supported link. Use for single-video
  transcription requests — "transcribe this reel", "what is said in this
  video", "get the transcript of my latest save". For a whole saved
  collection or YouTube playlist, use collection-transcribe instead.
---

# reel-transcribe

**One command, runnable from any directory — no navigation needed:**

```powershell
python %USERPROFILE%\GitHub\editor-pack\skills\reel-transcribe\scripts\transcribe_reel.py --latest-saved
python %USERPROFILE%\GitHub\editor-pack\skills\reel-transcribe\scripts\transcribe_reel.py --url "https://www.instagram.com/reel/SHORTCODE/"
python %USERPROFILE%\GitHub\editor-pack\skills\reel-transcribe\scripts\transcribe_reel.py --url "https://youtu.be/..."
```

Folder map: this script imports auth from `..\..\ig-saved-collections\scripts\ig_auth.py` (credentials in that
skill's `.env` / `.env.<account>`); output lands in `reel_transcripts\<shortcode>.txt` relative to the cwd (pass
`--out` for a specific folder). For batch jobs use **collection-transcribe**'s `run.py` instead.

## How it works

- **Instagram links / `--latest-saved`**: uses the already-authenticated instagrapi session from
  `../ig-saved-collections` (its `.env` / `.env.<account>` credentials; auto-refreshes via its Playwright login
  when stale). Gets the post caption for free. No yt-dlp cookie dance.
- **Everything else**: yt-dlp download; `.info.json` description serves as the caption.
- **YouTube exception**: don't Whisper YouTube videos — YouTube serves real captions; route those to the
  **yt-saved-playlists** skill instead (100-1000x faster).
- Transcription = ffmpeg audio extract → stable-ts Whisper (`--whisper-model base` default, `--language en`;
  pass `--language ""` to auto-detect for non-English reels).

## Output conventions (standing user preferences — keep them)

- One wrapped `.txt` per reel in `--out` (default `reel_transcripts/`): header (owner, URL, duration), post
  caption, spoken transcript, timing breakdown — **everything wrapped at 120 chars/line**, never one long line.
- The transcript AND a stage-by-stage timing summary (model load, metadata fetch, download, audio extract,
  whisper, total) print to stdout — relay the timings to the user when they ask how long it took.
- Video is deleted after transcription unless `--keep-video`.

## Honest-reporting rules

- Whisper is reliable on spoken dialogue/monologue, garbled on singing/music-heavy or very short clips — say so
  when a transcript looks like noise instead of presenting it as clean data (verified across 20+ reels).
- Expect ~40s end-to-end for a ~1-min reel on this machine (~13s of that is one-time Whisper model load;
  ~17s is transcription — scales with video length).
