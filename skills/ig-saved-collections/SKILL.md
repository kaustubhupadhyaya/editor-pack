---
name: ig-saved-collections
description: >-
  Extract everything from the user's OWN Instagram saved collections: one
  folder per collection, every saved reel/post with its caption, a transcript
  when someone speaks, and screenshots plus their on-screen text when the
  audio is only music. Lists through the account's own signed-in browser
  (never instagrapi on the main account), downloads from the CDN, processes
  offline, publishes one text note per collection to the Second Brain. Use for
  "my saved reels", "my [CollectionName] collection", or a local archive of
  what they've saved. Not for a single public reel by link (reel-maker) and
  not for anyone else's account.
---

# ig-saved-collections

## Account safety first (read before running anything)

On **2026-10-19 the main account (kaustubh__upadhyaya) was locked** after a run
through instagrapi:
- it followed a scripted login;
- it replayed the browser's session cookie from instagrapi's fake Android phone,
  with a new device ID on every process start;
- it made about 1,000 requests in a few hours.

The rules since then:

- **Nothing runs against an account the user has not said is unlocked.**
- **Listing goes through `saved_collector.py`.** It sends the website's own JSON
  requests from inside the account's persistent browser profile (`.pw-profile/`),
  so the cookie, device and network fingerprint never change. Do not list with
  instagrapi (`extract_saved.py`, `list_collections.py`, `ig_dm_export.py`) on
  the main account.
- **At most 150 requests per IST day**, 6–12 s apart, counted in
  `_state/requests.jsonl`. State the expected count to the user before a run.
- **Stop at the first push-back.** A non-200 status, a redirect to the login or
  challenge page, or `checkpoint_required` / `challenge_required` /
  `feedback_required` / `login_required` / `spam` all count.
  - The collector exits with code 76 and writes `_state/STOPPED.json`.
  - It refuses to start again until the user says so (`--clear-stop`).
  - Nothing is retried, and there is no second login.
- **Sign-in:** when the profile is signed out, the collector runs
  `refresh_session.py` once (the user's choice, 2026-10-22). It types the stored
  username and password into the same visible browser profile.
  - It reads the secure-vault entry `instagram-main` (fields `email` = username,
    `password`) and only then `.env`.
  - CAPTCHAs and challenges are never solved or evaded. The window stays open
    for the user.
- **Never ask the user to paste credentials into chat.** A changed password goes
  in through the vault popup:
  `python %USERPROFILE%\.agents\skills\secure-vault\vault.py gui --request "instagram-main:account"`.

## Scripts

| Script | What it does | Contacts Instagram? |
|---|---|---|
| `saved_collector.py` | lists collections + every saved item, from inside the browser; resumable pages in `_state/` | yes, ≤150 requests/day |
| `saved_media.py` | downloads each item's video/photos once into `_media/` from the signed CDN links | CDN only |
| `reel_content.py` | per media file: speech or music? transcript (faster-whisper) and/or screenshots + on-screen text (ffmpeg + RapidOCR) | no |
| `render_collection.py` | one folder per collection: `README.md`, `index.jsonl`, `items/<NNN>_<code>/` | no |
| `to_second_brain.py` | one text note per collection into the vault's `30_Resources/instagram/` via `brain.py file` | no |
| `refresh_session.py` | scripted sign-in of the browser profile (vault first, `.env` second) | yes (one login) |
| `ig_auth.py`, `list_collections.py`, `extract_saved.py`, `ig_dm_export.py` | older instagrapi tools; not for the main account | yes |

Exact commands: [references/cookbook.md](references/cookbook.md) §5.

## Workflow (main account)

**0. Prereqs.**
- Run everything with `C:\Python314\python.exe` (plain `python` is a different venv).
- Playwright browsers live in `D:\DevCaches\ms-playwright` (the user env var
  `PLAYWRIGHT_BROWSERS_PATH`).
- Models:
  - faster-whisper `small` in `D:\DevCaches\faster-whisper\small`;
  - the AudioSet classifier AST in `D:\DevCaches\hf-models\ast`, which needs `transformers`.
- D: is exFAT: no symlinks or hard links, and HuggingFace downloads need `local_dir`.

**1. Pilot.** `saved_collector.py --stage pilot` costs about 3 requests: the home
page, the collection list and the "Business" collection. Check
`_state/pilot.json` for three things:
- the real page size;
- whether items carry `saved_collection_ids`;
- whether `video_versions` links are present.

**2. Listing.** `saved_collector.py --stage auto` runs beside
`saved_media.py --follow`. Links expire within days, so media is fetched while
the listing runs.
- About 50 pages for All posts, plus about 99 for the named collections at 50
  items per page.
- The named walks are skipped when the pilot proves that `saved_collection_ids`
  carries membership.
- At the daily cap it pauses cleanly; the same command resumes the next day.

**3. Content.** `reel_content.py --manifest _state/content_manifest.jsonl`. It
works out a mode for each file and produces the matching output:

| Mode | Output |
|---|---|
| speech | transcript |
| music_only or no_audio | screenshots + on-screen text |
| singing, mixed or unsure | both |
| unsupported_language (kn/te/ml/ta, where the small model loops) | screenshots + flag |

- CPU-heavy work stays outside the job-application bots' window, 04:00–10:00 IST.
- It holds one model in memory at a time.

**4. Folders.** `render_collection.py` writes:
- one folder per collection;
- `_Not in any collection`;
- a root `README.md` with counts.

Clashing names get the end of the collection ID (`For her - 4278110`). An older
`index.jsonl` is kept as `index.<date>.jsonl`.

**5. Vault.** `to_second_brain.py --all` writes one note per collection to
`30_Resources/instagram/`. Rerunning refreshes the same notes.

**6. Report.** Per collection: Instagram's count against items served (about 25%
of saved posts are no longer served because they were deleted or made private),
transcripts, screenshot sets, failures. Also report the total number of requests
made.

Every long job has request timeouts, checkpoints, and a watchdog: exit 75 after
5 minutes without progress, with a supervising parent that restarts from the
checkpoint. Watch each job with a monitor that raises a no-progress alarm at
6 minutes.

## Where the data lives

- `saved_export/` in this skill is a **directory junction** to
  `D:\EditorPackArchive\ig-saved-collections\saved_export\`. It was restored on
  2026-10-22 after it had turned into a real folder.
  - Re-create it with
    `cmd /c mklink /J skills\ig-saved-collections\saved_export D:\EditorPackArchive\ig-saved-collections\saved_export`.
- The main account's root is that folder. Other accounts use `saved_export\<account>\`.
- Inside the root:
  - `_state/` (listing pages, request log, checkpoints);
  - `_media/` (masters, never deleted);
  - `_items/` (content results);
  - one folder per collection.
- The vault gets text only. Screenshots and videos stay on D:.

## Notes

- `media_count` overstates what Instagram serves. On "Better", 148 were reported
  and 111 were served. The gap is posts deleted or made private after saving;
  it is not a pagination bug.
- instaloader does not support named collections, so it is not used here.
- The older instagrapi notes (timeouts, `login_by_sessionid` soft-blocks, the
  1-second `clip_download` timeout) are in cookbook §1–§4 for the
  secondary-account tools.
