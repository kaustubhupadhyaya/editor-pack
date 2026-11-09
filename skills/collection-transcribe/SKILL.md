---
name: collection-transcribe
description: >-
  Transcribe EVERY reel/video in a whole Instagram saved collection or a whole
  YouTube playlist — batch spoken-word transcripts plus captions, stored one
  folder per collection/playlist. Use for "transcribe my [collection name]
  collection", "transcripts for this playlist", "transcribe all my saved
  reels". For a single reel/video, use reel-transcribe instead.
---

# collection-transcribe

**Get straight to transcribing — one command, runnable from any directory, no navigation needed:**

```powershell
python %USERPROFILE%\GitHub\editor-pack\skills\collection-transcribe\scripts\run.py ig "ai hustle"
python %USERPROFILE%\GitHub\editor-pack\skills\collection-transcribe\scripts\run.py ig "Better" --account example_account
python %USERPROFILE%\GitHub\editor-pack\skills\collection-transcribe\scripts\run.py yt PLxxxxxxxx
```

`run.py` routes to the right sibling extractor with the right working directory, and prints total wall-clock time
at the end (divide by the item count it reports for the per-item average). Add `--limit 5` to smoke-test first.
Long runs: launch in the background — a full IG collection takes ~20-30s per reel (n × ~25s), so 60+ items is
30+ minutes; both extractors are **resume-safe** (re-run the identical command after any interruption and it
skips finished items — never restart from scratch).

## Complete folder map (so no exploration is needed)

```
%USERPROFILE%\GitHub\editor-pack\skills\
├── collection-transcribe\
│   └── scripts\run.py                  <- THE entry point (this skill)
├── reel-transcribe\
│   └── scripts\transcribe_reel.py      <- single-reel variant (has --latest-saved)
├── ig-saved-collections\               <- IG machinery (run.py calls into here)
│   ├── .env / .env.<account>           <- IG session credentials (gitignored)
│   └── scripts\
│       ├── list_collections.py         <- names+ids of every saved collection [--account X]
│       ├── extract_saved.py            <- the IG walker (run.py adds --transcribe)
│       ├── to_second_brain.py          <- publish a finished extraction to the Drive vault
│       ├── ig_auth.py                  <- shared auth (auto-refreshes via Playwright login)
│       └── saved_export\[<acct>\]<Collection>\   <- OUTPUT: index.txt + index.csv per collection
└── yt-saved-playlists\                 <- YouTube machinery (run.py calls into here)
    ├── client_secret.json / token.json <- OAuth for private playlists (gitignored)
    └── scripts\
        ├── list_playlists.py           <- your playlists incl. private (Data API)
        ├── extract_playlist.py         <- captions fetcher (no Whisper needed)
        └── yt_export\<Playlist>\       <- OUTPUT: transcripts\NNN [id].txt + index.txt + index.csv
```

## Routing rules

1. **Instagram → Whisper**: IG serves no captions, so each reel is downloaded and ASR-transcribed
   (~20-30s/reel; model loads once per run). Confirm with the user before a 100+ item collection.
2. **YouTube → served captions, NOT Whisper**: ~1-2s/video. Whisper only for videos reported as `none`, and
   only if asked.
3. Auth problems: IG session auto-refreshes (a browser window may open for a human CAPTCHA step — see
   ig-saved-collections SKILL.md's hard line about never bypassing it). YT private playlists need the one-time
   OAuth setup in yt-saved-playlists/references/api-setup.md.

## Storage conventions (standing user preferences — encapsulated here on purpose)

- **One folder per collection/playlist**, named after it, in the output locations shown in the map above.
  Collections are never mixed into one file or folder.
- **All prose wrapped at 120 chars/line** — readable in an editor with no horizontal scrolling; never one long
  line per record. `index.txt` = human ledger, `index.csv` = spreadsheet view. JSONL is banned (JSON can't hold
  literal newlines).
- Every record carries owner/channel, URL, and transcript source (manual/auto captions vs Whisper) so quality
  is traceable.
- Staging is local; the content's real destination is the Second Brain vault — offer `to_second_brain.py`
  after a full IG collection run.

## Honest-reporting rules

- Whisper is reliable on spoken dialogue, garbled on singing/music-heavy clips — flag garbled transcripts
  rather than presenting them as clean data.
- Report timing honestly: total wall-clock (run.py prints it) and per-item average = total / items processed.
- IG runs pace themselves (~2s between reels); don't shrink sleeps. A YouTube `RequestBlocked` stop means wait
  ~24-48h, then re-run to resume.
