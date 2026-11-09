# ig-saved-collections cookbook — exact commands

Windows/PowerShell. Run these from `skills/ig-saved-collections/`. `$sp` = scratch dir.

**Main account: use §5 (the browser collector) only.** §1–§4 are the older
instagrapi tools. On 2026-10-19 that path locked the main account (see SKILL.md,
"Account safety first"). They stay documented for the secondary-account archives.

## §0 — Install & credential setup

```powershell
python -m pip install --user --upgrade gallery-dl instagrapi python-dotenv playwright
python -m playwright install chromium   # one-time browser download, not a pip package
Copy-Item .env.example .env
notepad .env   # fill in IG_SESSIONID; optionally IG_USERNAME/IG_PASSWORD for auto-refresh
```

`.env` and the Playwright profile (`.pw-profile/`) are gitignored at the repo
root — neither will ever be committed. Credentials are read only by the
Python scripts in this folder, only on this machine.

**sessionid**, not a password, is what every script actually authenticates
with: DevTools (F12) → Application tab → Cookies → `https://www.instagram.com`
→ row `sessionid` → copy the Value column. This is a session token; logging
out of Instagram in that browser invalidates it instantly if it ever leaks.

**Optional auto-refresh** — set `IG_USERNAME`/`IG_PASSWORD` too, and
`ig_auth.get_client()` (used by both `list_collections.py` and
`extract_saved.py`) will automatically run `refresh_session.py` whenever the
stored sessionid has expired, instead of erroring out. That script drives a
**real Chromium browser** through Instagram's actual login page — the
password is only ever typed into that browser, never sent to instagrapi's
own login endpoint. See §1a for exactly what to expect when it runs.

## §1 — List collections by name

```powershell
python scripts/list_collections.py
```
Authenticates via `ig_auth.get_client()`: builds an authenticated instagrapi
client directly from `IG_SESSIONID` (no `login_by_sessionid()` — see the
gotcha below on why) and does one cheap real API call to confirm it's live.
Output: each collection's name, numeric id, item count, and a
`collections.json` dump. The entry with `type: "ALL_MEDIA_AUTO_COLLECTION"`
(printed name usually "All posts") represents the user's **entire** saved
section, not one folder.

### §1a — What happens when the session needs refreshing

If the sessionid is dead and `IG_USERNAME`/`IG_PASSWORD` are set in `.env`,
you'll see:
```
Session expired -- refreshing via browser login...
```
A **visible** Chromium window opens (never headless — headless can't clear a
live challenge), goes to instagram.com's login page, fills in the
credentials from `.env`, and submits. Two outcomes:
- **No challenge** (common on a device Instagram already recognizes, thanks
  to the persistent profile in `.pw-profile/`): the window logs in and closes
  on its own within a few seconds. Nothing for the user to do.
- **A challenge appears** (CAPTCHA, "confirm it's you", a 2FA code) — this
  happens on an unrecognized device, most likely the very first run. The
  script does **not** attempt to solve or bypass this; a human has to. It
  waits up to 10 minutes for the user to clear it in the visible window, then
  detects the resulting `sessionid` cookie and finishes on its own from
  there — no DevTools copy-paste needed even in this path.

Either way, `.env`'s `IG_SESSIONID` line gets overwritten with the fresh
value, and the calling script (`list_collections.py`/`extract_saved.py`)
retries automatically.

## §2 — Extract info out of every reel in a collection

```powershell
python scripts/extract_saved.py --collection "Bike Edits" --out "$sp\saved_export"
```

**How it actually walks the collection** (this is the literal mechanism, not
a summary): Instagram's API returns saved items in pages of ~50 (a "chunk"),
each with a cursor (`max_id`) pointing at the next page. The script:
1. Requests one chunk via `collection_medias_v1_chunk(pk, max_id)`.
2. Iterates that chunk's items **one at a time** — for each `Media` object it
   pulls `code` (shortcode), `caption_text`, `user.username`, `like_count`,
   `view_count`, `video_duration`, `clips_music_attribution_info` (audio
   title/artist if the reel used a licensed track), and builds the permalink.
3. Writes that single record to `index.jsonl` immediately (one JSON object per
   line, unwrapped raw text), then flushes to disk — so if the process is
   killed mid-run, everything up to that item is already saved on disk, not
   lost.
4. Sleeps `--item-sleep` seconds (default 2s), moves to the next item in the
   chunk.
5. Once the chunk is exhausted, asks for the next page's cursor and repeats.
   Stops when Instagram returns no more pages — meaning it has walked the
   **entire** collection, however many hundreds of items that is, not just
   the first page.
6. Re-running the same command skips any `pk` already present in
   `index.jsonl` (loaded into a `seen` set at start) — safe to resume or
   re-run periodically to pick up newly-saved items.

**Output** (`saved_export\<account>\<Collection>\` — the account level only
appears when `--account` was passed; `saved_export\` itself is a junction to
`D:\EditorPackArchive\ig-saved-collections\saved_export\`, see SKILL.md
"Where the data actually lives"):
- `index.jsonl` — one JSON object per reel: permalink, saved date, duration,
  likes/views, audio, post caption, spoken transcript. Agent-readable raw
  archive, NOT word-wrapped — this is bulk data an agent greps/jq's on
  demand, not a file a human opens to read. Also what backs resume/dedup
  (keyed on `pk`).
- `videos/` — only if `--download-video` was passed; one `<shortcode>.mp4`
  per reel (uses instagrapi's `clip_download`).

This is **staging**, not the deliverable — publish it to the Second Brain
with §2a, which renders `index.jsonl` into the actual wrapped, human-readable
note.

**Add spoken-word transcripts** (opt-in; downloads each video + runs Whisper,
much slower — budget roughly a minute per reel):
```powershell
python scripts/extract_saved.py --collection "Better" --account example_account --transcribe
```
Safe to interrupt: each reel is flushed to disk as it completes, and re-running
resumes from where it stopped instead of re-fetching.

**Whole saved section instead of one folder:**
```powershell
python scripts/extract_saved.py --collection "All posts" --out "$sp\saved_export"
```
(Use whatever name §1 printed next to `ALL_MEDIA_AUTO_COLLECTION` — usually
"All posts".)

**Test on a handful first:**
```powershell
python scripts/extract_saved.py --collection "Bike Edits" --out "$sp\saved_export" --limit 5
```

## §2a — Publish the extraction into the Second Brain (Google Drive)

The extracted *content* belongs in the user's Second Brain vault, not in this
repo. The scripts and `.env` files stay in editor-pack; `saved_export/` is
just staging.

```powershell
python scripts/to_second_brain.py --collection "Better" --account example_account
# or do it as part of the extraction run:
python scripts/extract_saved.py --collection "Better" --account example_account --transcribe --publish-to-second-brain
```

Writes one note to `<second-brain>\00_Inbox\` named
`YYYY-MM-DD-instagram-saved-<collection>-<account>.md`, and adds/refreshes its
bullet in that folder's `INDEX.md`. Set `$env:SECOND_BRAIN_DIR` to override the
vault path; the script refuses to run if the target has no `AGENTS.md` (i.e.
Google Drive for Desktop isn't mounted/signed in) rather than silently writing
to the wrong place.

Re-running after extracting more items **refreshes the same note in place** —
it keeps the note's original `created:` date and bumps only `updated:`, so
there's exactly one note per collection, never duplicates.

Everything about the note's shape (Inbox placement, filename pattern, required
frontmatter, INDEX.md upkeep, leaving `HOME.md` alone) comes from the vault's
own `<second-brain>\AGENTS.md`. **Read that contract before changing
this script** — it governs the vault, this repo doesn't.

**The one non-obvious trap:** every user-supplied value in the frontmatter goes
through `yaml_str()`. An unquoted `": "` inside a YAML value — say a collection
named `Tips: Advanced`, or the old `(account: example_account)` phrasing — makes the
parser read a nested mapping and the *entire* frontmatter block silently fails
to load. The note still looks perfect in an editor, but every field including
`summary` is gone, so it's invisible to exactly the Drive/ChatGPT/Claude
connectors the vault exists to feed. `assert_frontmatter_parses()` now hard-
fails the write if that ever regresses.

## §3 — Alternative: bulk video download via gallery-dl

If `extract_saved.py --download-video` gets rate-limited on a very large
collection, gallery-dl's cookie-based scraping is a lower-risk bulk path
(it doesn't touch instagrapi's private API at all):
```powershell
gallery-dl --cookies-from-browser firefox `
  -o sleep-request=5-10 `
  -o "directory=[`"ig_saved`",`"{collection_name}`"]" `
  "https://www.instagram.com/<user>/saved/_/<collection_id>/"
```
Get `<collection_id>` from §1's output. `--cookies-from-browser chrome`/`edge`
breaks on Windows (App-Bound Encryption) — use Firefox, or export a
`cookies.txt` via a "Get cookies.txt LOCALLY"-style extension and pass
`--cookies "$sp\instagram-cookies.txt"` instead.

## §4 — Verify

```powershell
# peek at the first few records (each line is a standalone JSON object)
Get-Content ".\saved_export\example_account\Better\index.jsonl" -TotalCount 3

# pretty-print one record
python -c "import json; print(json.dumps(json.loads(open(r'saved_export\example_account\Better\index.jsonl',encoding='utf-8').readline()), indent=2, ensure_ascii=False))"

# coverage: how many videos still lack a transcript?
python -c "
import json
rows=[json.loads(l) for l in open(r'saved_export\example_account\Better\index.jsonl',encoding='utf-8')]
v=[r for r in rows if r['media_type']==2]
print(len(rows),'rows |',len(v),'videos |',sum(1 for r in v if not (r['spoken_transcript'] or '').strip()),'missing transcript')"

# reconcile: does Instagram's advertised count match what pagination actually serves?
python -c "
from ig_auth import get_client
cl=get_client(account='example_account'); c={x.name:x for x in cl.collections()}['Better']
n=0; cur=''
while True:
    it,cur=cl.collection_medias_v1_chunk(c.id,max_id=cur)
    if not it: break
    n+=len(it)
    if not cur: break
print('reported',c.media_count,'| actually served',n)"

# confirm the published note's frontmatter actually parses (the silent-failure check)
python -c "import yaml,sys; p=r'<second-brain>\00_Inbox\2026-08-29-instagram-saved-better-example-account.md'; print(yaml.safe_load(open(p,encoding='utf-8').read().split('---')[1]))"
```

## Gotchas

- **401 / login errors, no auto-refresh configured** → `IG_SESSIONID` in
  `.env` has expired and `IG_USERNAME`/`IG_PASSWORD` aren't set. Get a fresh
  sessionid from the browser and update `.env` by hand, or add the
  username/password for automatic refresh going forward.
- **instagrapi's own `login_by_sessionid()` is deliberately NOT used** —
  `ig_auth.client_from_sessionid()` replicates only its cookie/header setup.
  The reason: `login_by_sessionid()` follows that with a noisy user-info
  lookup (`users/{id}/info` → `info_stream` → a public GraphQL call) that
  Instagram soft-blocks after a few calls in a session — it can 403 and, via
  a GraphQL redirect loop, throw `TooManyRedirects`, killing the whole login
  even though the sessionid itself is perfectly valid. Skipping that lookup
  avoids the failure entirely, at the cost of not having `cl.username`
  populated (harmless — nothing here depends on it).
- **Instagram's login form field names are `email`/`pass`**, not
  `username`/`password`, despite the placeholder text saying "Mobile number,
  username or email" — `refresh_session.py` hard-codes the real names. If a
  future Instagram redesign changes them again, the symptom is a Playwright
  `TimeoutError` waiting for `input[name="email"]`.
- **Never send a password to instagrapi's own login** — highest-risk path for
  a temp account restriction. The only password path here is a real browser
  login (`refresh_session.py`), and even that is never automated past a live
  CAPTCHA/checkpoint — see SKILL.md's "hard line" note.
- **Don't lower `--item-sleep`/`--page-sleep`** on a big collection just to go
  faster — sustained rapid private-API calls are what trigger restrictions,
  not the total volume.
- **Extracted count < the collection's item count is EXPECTED, not a bug.**
  Measured on "Better" (example_account): Instagram's own `media_count` reports
  **148**, but walking every page until the cursor is exhausted yields
  **111**. The ~25% gap is posts that were deleted, or whose authors went
  private/deactivated, after being saved — Instagram keeps them in the saved
  counter but no longer serves them through the feed endpoint. Before
  investigating this as a pagination bug, confirm with the reconciliation
  snippet in §4; if pagination ran out of cursors, you already have
  everything Instagram will hand over.
- **Transcript failures used to be silent-ish and common** (~30 of 110 on the
  first full run) because instagrapi's `clip_download` inherits
  `request_timeout = 1` — one second to pull a whole video. `extract_saved.py`
  now downloads via its own `download_video()` (10s connect / 120s read, 3
  retries, straight from `m.video_url`). If a run still reports failures, use
  `--retry-missing-transcripts`, which reprocesses ONLY the empty-transcript
  rows and leaves good ones untouched.
- gallery-dl's collection support needs a reasonably current version — if
  `InstagramCollectionExtractor` 404s, `pip install --upgrade gallery-dl`.

## §5 — Browser collector pipeline (main account, since 2026-10-22)

`$py = 'C:\Python314\python.exe'`, run from `skills\ig-saved-collections\scripts\`. Archive root:
`D:\EditorPackArchive\ig-saved-collections\saved_export\` (the `saved_export` junction).

**Before the first request:** the user has said the account is unlocked. `_state\STOPPED.json` must not exist; if it
does, read its reason and pass `--clear-stop` only after the user says so.

```powershell
# 1. Pilot, about 3 requests: home page, collection list, the "Business" collection.
#    Signs in once via refresh_session.py if needed.
& $py saved_collector.py --stage pilot
Get-Content D:\EditorPackArchive\ig-saved-collections\saved_export\_state\pilot.json  # page size, saved_collection_ids, video_versions

# 2. Full listing (<=150 requests per IST day; pauses cleanly at the cap, rerun tomorrow to resume)
#    with the CDN downloader following it in a second process.
Start-Process $py -ArgumentList 'saved_media.py','--follow' -RedirectStandardOutput media.log -RedirectStandardError media.err -WindowStyle Hidden
& $py saved_collector.py --stage auto *> collector.log

# 3. Content (offline). Resumable; rerun as more media arrives. Keep CPU-heavy runs outside 04:00-10:00 IST.
& $py reel_content.py --manifest D:\EditorPackArchive\ig-saved-collections\saved_export\_state\content_manifest.jsonl --threads auto

# 4. One folder per collection, plus the root README.md with counts.
& $py render_collection.py

# 5. One text note per collection in the vault (30_Resources/instagram), then the vault check.
& $py to_second_brain.py --all
& $py %USERPROFILE%\.agents\skills\second-brain\scripts\brain.py doctor --path 30_Resources/instagram
```

**What each file in `_state\` is:**
- `requests.jsonl`: every request with its IST time and status. Count today's lines for the budget.
- `pages\<feed>.jsonl`: the raw listing pages (`all` = All posts, otherwise a collection ID).
- `state.json`: the cursors.
- `collections.json`, `pilot.json`, `membership.json`.
- `media.jsonl`: ok / expired / failed per file.
- `content_manifest.jsonl`: the input of `reel_content.py`.

**Expired links.** Rows marked `expired` in `media.jsonl` need fresh links from a new listing of their feed. That
costs requests, so count them and ask the user before re-listing.

**Download gotcha (2026-10-22):** curl's `--retry` together with `-C -` resumes from the offset it worked out when it
started, so every retry rewound a 42 MB partial download to 0.4 MB. Use one fresh `curl -C -` per attempt in a loop.
On exFAT, Git Bash `stat` shows a stale size while another process has the file open; Python `os.stat` is correct.
