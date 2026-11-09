---
name: yt-saved-playlists
description: >-
  Extract the closed captions / transcripts (what's actually said) of every
  video in the user's own YouTube playlists — including private playlists,
  Liked Videos, Watch Later, and playlists saved from other channels — or of a
  single video/public playlist by URL. Use when the user wants transcripts,
  captions, or a content dump of "my YouTube playlists / Watch Later / liked
  videos" or any YouTube playlist. Not for downloading the video files
  themselves.
---

# yt-saved-playlists

The YouTube sibling of **ig-saved-collections** — but the architecture differs in two happy ways: YouTube has an
**official free API** for your own playlists (no ban-risk private-API scraping), and YouTube **serves its captions**
(no Whisper needed for videos that have any caption track, which is most talking videos).

Scripts: **`yt_auth.py`** is the shared, self-healing auth spine (token load/refresh, autonomous regrant through the
authenticated browser profile, and a profile→`cookies.txt` bridge for Watch Later — see **Autonomous auth** below);
**`list_playlists.py`** enumerates your created playlists (incl. private) + Liked Videos via the Data API;
**`extract_playlist.py`** walks one playlist (or single video) and writes one `index.jsonl` per playlist — one JSON
object per video (title, channel, URL, caption source, and the full transcript inlined), and optionally publishes a
curated note with `--publish-to-second-brain`; **`to_second_brain.py`** renders a chosen subset of an extracted
playlist into one vault note; **`benchmark_captions.py`** ranks caption tools on your own videos. Full commands:
[references/cookbook.md](references/cookbook.md). One-time Google Cloud OAuth setup:
[references/api-setup.md](references/api-setup.md).

## Where the data actually lives

`./yt_export` (the `--out` default) is a **directory junction**, not a real folder — it points at
`D:\EditorPackArchive\yt-saved-playlists\yt_export\` on the external HDD, the YouTube sibling of
[[editor-pack-video-editing-stack]]'s ig-saved-collections archive. The scripts stay in this repo; the raw
transcript data (potentially large, rarely opened directly) lives on external storage. This is transparent to
`extract_playlist.py` — it just reads/writes `yt_export/...` normally.

Re-create the junction on a fresh clone (junctions don't survive `git clone`, and this path is gitignored):
```powershell
mkdir D:\EditorPackArchive\yt-saved-playlists\yt_export -Force
cmd /c mklink /J skills\yt-saved-playlists\scripts\yt_export D:\EditorPackArchive\yt-saved-playlists\yt_export
```
`mklink /J` (junction, not `/D` symlink) needs no admin privilege/Developer Mode and works targeting an
exFAT-formatted drive. If the archive drive isn't attached, `yt_export/` will appear empty/missing rather than
erroring — reattach it before extracting.

## Output format — agent-readable, not human-wrapped

`index.jsonl` is bulk archival raw data: one JSON object per video
(`position`, `video_id`, `title`, `channel`, `url`, `transcript_source`,
`transcript`), full transcript text inlined as a normal JSON string — no
per-video `.txt` file, no 120-char word-wrapping. It's meant to be
grepped/jq'd by an agent on demand, not opened and read directly. (This
supersedes the old wrapped-`index.txt` + per-video-file convention that
ig-saved-collections' sibling skill still documents in
[[wrapped-text-output-preference]] for its *published* Second Brain notes —
that convention now applies one layer up, at the point something renders
this raw data for a human, not at the raw-archive layer itself.)

## Access matrix — which auth reaches what

| Target | Path |
|---|---|
| Playlists you created (public/unlisted/**private**), Liked Videos | **Data API + OAuth** (default; zero risk, free, 10k quota/day vs ~400 used) |
| **Watch Later**, playlists **saved from other channels** | **yt-dlp + cookies.txt** only — Google killed WL API access in 2016 and never built a saved-playlists endpoint |
| Any public playlist or video | either; works with no auth at all |

Google Takeout (manual, zero-risk) is the fallback archive path covering everything incl. Watch Later.

## Autonomous auth (how the skill runs hands-off)

All auth lives in `yt_auth.py` and follows the secure-vault's own doctrine: **prefer the already-authenticated
browser profile (session reuse) over re-supplying a password.**

1. **Data API (owned/private/Liked):** `token.json` loads and **auto-refreshes** silently — this is the normal path,
   zero credentials, zero prompts.
2. **If the refresh token dies** (a Testing-mode app revokes it every 7 days), `get_service()` **auto-regrants** by
   driving the OAuth consent screen through the authenticated `google-5` profile — no human step.
3. **Watch Later / saved-from-others:** `export_cookies_txt()` reads the profile's live cookies via Playwright's
   `context.cookies()` (decrypted values, so Chrome's **v20 App-Bound Encryption is bypassed entirely**) and writes
   `yt-cookies.txt` for yt-dlp. No login, no console.
4. **Only if the profile session itself is dead** (rare — months): `refresh_profile_session_interactive()` opens a
   VISIBLE window, prefills the vault email/password (read in-process from the DPAPI vault, never printed), and the
   human clears any 2FA. One popup, never a per-run step.

**Residual risk:** Google may flag automation-driven session reuse/re-login. Rare (only on session expiry) and
mitigated by reusing the established session. The Data API path (owned + Liked) is zero-risk; only WL/saved-from-others
touches the profile. **Publishing the Cloud app to "In production" is optional but recommended** — it makes the token
permanent so step 2 ~never fires; it CANNOT be automated (no API/CLI, confirmed), so it's a one-time 30-second click.

## Credentials

- `client_secret.json` + `token.json` (OAuth; skill folder, gitignored). Scope is **read-only**. Setup:
  `references/api-setup.md`. The token auto-refreshes and self-regrants (see Autonomous auth) — no weekly re-login.
- The authenticated **`google-5` browser profile** at `%USERPROFILE%\.secrets\profiles\google-5\` (managed by
  `secure-vault`) is what reaches Watch Later and self-heals a dead token. `yt-cookies.txt` is auto-exported from it
  (gitignored). Never bulk-download videos with a logged-in cookie jar — metadata + subtitle pulls are the low-risk end.
- Secrets are read **in-process** from the DPAPI vault; never printed, logged, put on argv, or pasted into chat.

## Caption strategy (how extract_playlist.py decides — `STRATEGY` dict, set by benchmark_captions.py)

1. **youtube-transcript-api** first — fastest and cleanest, prefers creator-made captions, no auth. (Benchmarked at
   ~15-60 videos/min vs yt-dlp's ~8-12; it wins the primary slot.)
2. On its failure modes (`PoTokenRequired`, parse errors), falls back to **yt-dlp subtitle-only pull**. This fallback
   is configured with **`--js-runtimes node` + the profile cookies + `youtube:skip=translated_subs`** — proven live:
   without a JS runtime and an authenticated cookie jar it 429s on the timedtext endpoint; with both it succeeds.
   Requests ORIGINAL-language tracks only (auto-TRANSLATED subs are 429-rate-limited, yt-dlp #13831).
3. A 429 / bot-check from **either** path **stops the whole run** (`RequestBlocked` from yta, or a stderr 429 from
   yt-dlp → `RateLimitTripped`) — continuing extends the block (~24-48h). Resume later; written records are skipped.
4. Videos with no caption track are recorded as `none` — offer Whisper (stable-ts) only if asked; 100-1000× slower.
5. Runs stay on this residential machine — YouTube insta-blocks datacenter IPs.

## Workflow

1. **Phase 0**: deps (`google-api-python-client`, `youtube-transcript-api`, `yt-dlp ≥ 2026.08.19`, `playwright` +
   chromium, `node`). Auth is already set up (token + `google-5` profile); `api-setup.md` is only for a fresh machine.
2. **Phase 1**: `python scripts/list_playlists.py` → writes `playlists.json`; confirm which playlist(s) they want.
   (Optional: `python scripts/benchmark_captions.py` to re-verify the caption-tool ranking.)
3. **Phase 2**: `python scripts/extract_playlist.py --playlist <id> --limit 10` smoke test, then the full run. WL:
   `--playlist WL` (cookies auto-export). Single videos: `--video <url>`.
4. **Phase 3 (publish)**: `--publish-to-second-brain` with a curation selector (`--positions`, `--videos`, `--match`,
   `--with-transcripts-only`, `--curation`, `--publish-limit`; `--slug-suffix` for a 2nd subset of one playlist), or
   run `to_second_brain.py` separately. A note lands in the vault's `00_Inbox/`. **Confirm which playlist to publish —
   several titles are personal, and the note's `summary:` is what Drive search surfaces.**
5. **Phase 4**: report the note path and which videos had no captions.
