# yt-saved-playlists cookbook — exact commands

Windows/PowerShell. Run from `skills/yt-saved-playlists/`. yt-dlp must be **>= 2026.08.19**
(the 2026.07.04 default client broke with 403s; already satisfied on this machine).

## §0 — The two access paths, and when to use which

| Path | Covers | Risk | Use for |
|---|---|---|---|
| **Data API + OAuth** (`list_playlists.py`) | playlists you created (incl. private), Liked Videos | none — official API | the default; ~everything |
| **yt-dlp + cookies** | Watch Later, playlists saved from other channels | real but low for metadata-only | only the two gaps the API can't reach |

Google Takeout (takeout.google.com → YouTube → playlists) is the zero-risk manual fallback that covers
everything including Watch Later — use it for a one-off archive instead of the cookie path if the account
matters to you.

## §1 — API path (default)

One-time setup: [api-setup.md](api-setup.md) (Google Cloud project → OAuth Desktop client →
`client_secret.json` in the skill folder → **publish app to "In production"** to dodge the 7-day token expiry).

```powershell
python scripts/list_playlists.py         # all created playlists (incl. private) + Liked Videos id
```

Quota: 1 unit per 50-result page against a 10,000/day allowance — effectively unlimited for this use.

## §2 — yt-dlp cookie path (Watch Later + saved-from-others only)

### Automatic path (default) — cookies from the authenticated profile

`extract_playlist.py --playlist WL` needs **no manual cookie step**: it calls `yt_auth.export_cookies_txt()`, which
opens the already-signed-in `google-5` browser profile (`%USERPROFILE%\.secrets\profiles\google-5\`) and reads its
live cookies via Playwright's `context.cookies()`. Those come back **decrypted**, so Chrome's v20 App-Bound
Encryption — which makes `--cookies-from-browser chrome` return an empty jar (see below) — is bypassed entirely. The
result is written to `yt-cookies.txt` (gitignored) and passed to yt-dlp automatically.

```powershell
python scripts/yt_auth.py cookies      # manually refresh yt-cookies.txt from the profile, if ever needed
python scripts/yt_auth.py check        # is the profile session still authenticated? (True/False)
```

If the profile session ever dies (rare — months), `yt_auth.refresh_profile_session_interactive()` opens a visible
window, prefills the vault email/password (read in-process, never printed), and waits for you to clear any 2FA.

### Manual fallback — cookie export ritual (only if the profile path is unavailable)

YouTube rotates cookies on any live session, invalidating simple exports. The yt-dlp wiki's required procedure:

1. Open a **private/incognito** browser window → log into youtube.com.
2. In that same tab, navigate to `https://www.youtube.com/robots.txt` (a page that never rotates cookies).
3. Export youtube.com cookies with the **"Get cookies.txt LOCALLY"** extension (NOT the old "Get cookies.txt" —
   that one was removed from the store as malware).
4. **Close the private window without touching YouTube again** — the session is never resumed, so the exported
   cookies stay valid.

Save as `yt-cookies.txt` in this folder (gitignored via `*cookies*.txt`).

`--cookies-from-browser chrome/edge` does NOT work on Windows (Chrome 127+ App-Bound Encryption); Firefox works
but can't do the private-window ritual cleanly. The cookies.txt file is the reliable route.

### Commands (metadata-only — never downloads video, never hits the risky /player endpoint)

```powershell
# Watch Later (":ytwatchlater" also works; needs valid cookies or you get "playlist does not exist")
yt-dlp --flat-playlist --cookies "yt-cookies.txt" -J "https://www.youtube.com/playlist?list=WL" > wl.json

# Your channel's playlists tab -- surfaces created + SAVED-from-others playlists
# (the /feed/playlists page itself is broken in yt-dlp, issue #9863 -- use the channel tab instead)
yt-dlp --flat-playlist --cookies "yt-cookies.txt" -J "https://www.youtube.com/@YOURHANDLE/playlists" > all.json

# Enumerate one playlist's video ids/titles
yt-dlp --flat-playlist --cookies "yt-cookies.txt" --print "%(id)s %(title)s" "https://www.youtube.com/playlist?list=PLxxxx"
```

### Ban-risk hygiene (the yt-dlp wiki's own warning: accounts used with yt-dlp risk temporary or permanent bans)

- `--flat-playlist` metadata calls don't hit the `/player` endpoint and don't need PO tokens — this is the
  low-risk end of yt-dlp usage. Reported account blocks involved bulk video downloading, not enumeration.
- Still: never bulk-download videos with cookies from an account you can't afford to lose; prefer Takeout for
  Watch Later if the account matters.
- "WL: YouTube said: The playlist does not exist" = stale cookies → redo the export ritual.

## §3 — Extract captions/transcripts for a playlist

```powershell
# Smoke test first (always):
python scripts/extract_playlist.py --playlist PLxxxx --limit 5

# Full runs:
python scripts/extract_playlist.py --playlist PLxxxx --out yt_export            # API enumeration (default)
python scripts/extract_playlist.py --playlist WL --cookies yt-cookies.txt       # Watch Later
python scripts/extract_playlist.py --video "https://youtu.be/VIDEOID"           # one video
python scripts/extract_playlist.py --playlist PLxxxx --languages "hi,en"        # caption language priority
```

Output per playlist: one `index.jsonl` (junctioned to `D:\EditorPackArchive\yt-saved-playlists\yt_export\`, see
SKILL.md) — one JSON object per video (`position`, `video_id`, `title`, `channel`, `url`, `transcript_source`,
`transcript`), full transcript text inlined, unwrapped. Agent-readable raw archive, not a human-facing ledger.
Re-running skips any `video_id` already present in `index.jsonl` — safe to resume after any interruption.

### How the caption layer behaves (so failures make sense)

- Primary fetcher is **youtube-transcript-api 1.2.4** (the dedicated library; cleanest text; prefers creator-made
  captions over auto-generated). Its two 2026 gotchas: cookie auth is BROKEN upstream (age-restricted videos fail to
  the fallback), and a subset of videos hit `PoTokenRequired` (issue #592, unfixed since Apr 2026).
- Fallback is **yt-dlp --skip-download** with `--sub-langs "en.*,en-orig"` + `--extractor-args
  "youtube:skip=translated_subs"`. Two settings are **mandatory or it 429s** (proven live 2026-09-12): `--js-runtimes
  node` (yt-dlp needs a JS runtime in 2026; node is installed, deno isn't) and the profile `--cookies` (unauthenticated
  timedtext requests get "HTTP Error 429" immediately; with cookies the same videos succeed). Requesting ORIGINAL
  tracks + `skip=translated_subs` avoids the auto-TRANSLATED-caption rate limit (yt-dlp #13831). All of this is encoded
  in the `STRATEGY` dict at the top of `extract_playlist.py`, set from `benchmark_captions.py`'s measured ranking.
- `RequestBlocked` / `IpBlocked` from the primary = YouTube throttling this IP → the run STOPS deliberately.
  Wait ~24-48h and re-run (it resumes). Don't lower --item-sleep to "make up time" afterwards.
- If the yt-dlp fallback errors with "Did not get any data blocks" (open issue #17412, Aug 2026), retry that video
  with: `--extractor-args "youtube:player_client=default,web_embedded"`.
- NEVER run this from a cloud VM / VPS / CI — YouTube blocks datacenter ASNs on the first request. Residential
  Windows (this machine) is the environment where it works.

### Videos with no captions at all

Recorded as `none` in the index. These are typically music, shorts, or caption-disabled videos. If their spoken
content is actually needed, the existing stable-ts/Whisper toolchain (see ig-saved-collections / reel-maker) can
ASR them — but that's opt-in and slow; ask before re-transcribing a pile of them.

## §4 — Benchmark the caption tools (pick the fastest, with evidence)

```powershell
python scripts/benchmark_captions.py --seed 42 --n 20        # stratified sample of your own videos, all arms
python scripts/benchmark_captions.py --arms yta-serial,ytdlp-1w-vtt --cooldown 60
```

Draws a stratified ~20-video sample (by caption-available / duration / music, from `videos.list` — ~1 quota unit per
50), runs each arm over the identical set, and scores by **non-empty extracted text, never process exit code** (yt-dlp
exits 0 while writing nothing). Writes `benchmarks/<ISO>/{results.json, summary.md}`; `summary.md` ends with a literal
`STRATEGY = {...}` to paste into `extract_playlist.py`. Stops an arm on the first 429 and records `abort_at_index`.

## §5 — Publish a curated subset to the Second Brain

Selection happens at **publish time** (extraction is slow + resumable; curation is cheap + re-cuttable). One archive
can yield several notes with no re-fetching. Selectors AND together.

```powershell
# one step: extract, then publish positions 1-15 as a note
python scripts/extract_playlist.py --playlist PLxxxx --publish-to-second-brain --positions 1-15

# or publish separately from an already-extracted playlist
python scripts/to_second_brain.py --playlist PLxxxx --with-transcripts-only --limit 25
python scripts/to_second_brain.py --playlist PLxxxx --curation picks.txt --slug-suffix best
```

`picks.txt` is one video id (or URL) per line; `#` comments allowed; `VIDEOID | a curator note` appends that note to
the entry's header. With **no** selector on a playlist larger than `--max-unselected` (60) the script refuses and lists
the selectors — so you never inline 4,994 Liked Videos into one note. A 2nd subset of the same playlist needs
`--slug-suffix` (else it overwrites the first note). Notes land in the vault's `00_Inbox/` with parse-checked
frontmatter; re-running preserves the original `created:` date.
