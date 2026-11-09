# editor-pack

A collection of [Claude Code](https://claude.com/claude-code) **skills** for video editing.

The goal of this repo is to author reusable skills that automate and speed up
video-editing workflows — trimming, filler-word removal, captioning, multi-cam
assembly, silence removal, batch exports, and anything else worth turning into a
repeatable recipe.

## What's a skill?

A skill is a folder under `skills/` containing a `SKILL.md` file. That file has
YAML frontmatter (`name` + `description`) followed by instructions. Skills are
discovered and invoked by path (repo-only, no auto-registration): point an agent
at this README or directly at a skill's `SKILL.md` path. The `description` is
still the most important part — it's the trigger summary that tells an agent (or
a router skill) when to reach for it. Skills can ship supporting files
(scripts, templates, references) alongside `SKILL.md`.

## Structure

```
editor-pack/
├── skills/
│   ├── _template/          # copy this folder to start a new skill
│   ├── video-tool-router/  # pick the right tool for a video task (benchmark-backed)
│   ├── clip-picker/        # find & cut the good clips out of long footage
│   ├── reel-maker/         # recreate a reel from a reference link using your footage
│   ├── ig-saved-collections/  # list/download your own Instagram saved collections by name
│   ├── yt-saved-playlists/    # transcripts of your own YouTube playlists (incl. private/WL)
│   ├── reel-transcribe/       # transcribe ONE reel (spoken words + caption + timings)
│   └── collection-transcribe/ # batch-transcribe a whole IG collection / YT playlist (router)
├── requirements.txt        # pip deps for all skills
├── setup.ps1               # installs ffmpeg (winget) + pip deps, then verifies
├── .gitignore
└── README.md
```

## Skills in this repo

| Skill | What it does |
|---|---|
| **video-tool-router** | Routes any video task (cut/transcode/grade/tone-map/…) to the cheapest tool that fits — FFmpeg, auto-editor, MoviePy, DaVinci Resolve, Blender, or Descript. Includes the HLG→SDR guard. |
| **clip-picker** | Pulls the good short clips out of long, unstructured footage: samples it into timestamped contact sheets Claude can read, scores candidates, and returns a ranked pick-sheet + cut clips. Ships a POV-ride rubric; generalizes to any footage. |
| **reel-maker** | Recreates a short-form reel from a reference link (Instagram/YouTube/any) using your own footage: downloads the reference, reverse-engineers its cut timings / layout / on-screen text / audio into a recipe, motion-picks the best non-shaky moments, and assembles version 1 with word-accurate captions (stable-ts). Orchestrates `clip-picker` + `video-tool-router`. |
| **ig-saved-collections** | Lists your own Instagram saved collections **by name** and extracts per-reel info (caption, owner, stats, audio, permalink) one reel at a time across a whole collection. Authenticates via a session cookie in a local `.env`; can auto-refresh that cookie through a real browser login (Playwright) when it expires. |
| **yt-saved-playlists** | Extracts the **closed captions/transcripts** of every video in your own YouTube playlists — private ones via the official Data API (OAuth, free), Watch Later / saved-from-others via yt-dlp cookies. Primary fetcher is youtube-transcript-api with a yt-dlp subtitle fallback; no Whisper needed for videos that have captions. |
| **reel-transcribe** | Transcribes **one** reel/short video (spoken words + post caption) from an IG URL/shortcode, "my latest saved reel", or any yt-dlp link — with a per-stage timing report. ~40s per 1-min reel. |
| **collection-transcribe** | Router for **batch** transcription of a whole IG saved collection (Whisper) or YouTube playlist (served captions) — encodes the storage conventions: one folder per collection, 120-char wrapped text, resume-safe. |
| **phone-media-bridge** | Inventory, pull, and push media between a connected Android phone and the PC editing archive; open Instagram on-device for hand-posting. |
| **ig-edits-inspiration** | Read-only inspiration/roadmap extraction from the Instagram Edits app; encodes a reel-maker recipe; never drives the Edits editor UI. |
| **phone-reel-pipeline** | End-to-end orchestrator: phone media → optional Edits inspiration → PC reel build → push back → hand off to user to post. |

## Setup

One-shot (installs ffmpeg via winget + all pip packages, then prints a verification report):

```powershell
pwsh -File setup.ps1
```

Or manually:

```powershell
winget install --id Gyan.FFmpeg -e          # ffmpeg + ffprobe (external; also gives libass for captions)
pip install --user -r requirements.txt       # yt-dlp, stable-ts (captions), moviepy, auto-editor, numpy, pillow, gallery-dl, instagrapi, playwright
python -m playwright install chromium        # browser binary for ig-saved-collections' session refresh
```

**What needs what:**

| Tool | Kind | Used by | For |
|---|---|---|---|
| **ffmpeg / ffprobe** (+libass) | external (winget) | all skills | cut/concat/overlay/transcode, contact sheets, **burning captions** |
| **yt-dlp** | pip | reel-maker | download the reference reel/video from any link |
| **gallery-dl** | pip | ig-saved-collections | bulk video download fallback for a named saved collection |
| **instagrapi** | pip | ig-saved-collections | list collections by name + extract per-reel info (session-cookie login) |
| **python-dotenv** | pip | ig-saved-collections | loads the Instagram sessionid from a local, gitignored `.env` |
| **playwright** (+Chromium) | pip | ig-saved-collections | auto-refreshes the sessionid via a real browser login when it expires |
| **google-api-python-client / google-auth-oauthlib** | pip | yt-saved-playlists | official YouTube Data API access to own/private playlists (OAuth read-only) |
| **youtube-transcript-api** | pip | yt-saved-playlists | dedicated caption/transcript fetcher (primary; yt-dlp subs as fallback) |
| **stable-ts** (→ torch, openai-whisper) | pip | reel-maker | **captioning** — word-level timestamps |
| **numpy / pillow** | pip | clip-picker | sharpness scoring, contact-sheet assembly |
| **moviepy / auto-editor** | pip | video-tool-router | scripted composites, silence/filler cutting |
| **Blender, DaVinci Resolve** | external app (manual) | video-tool-router | Blender-scripted tasks; grade/stabilize/timeline (Resolve needs `py -3.11`) |
| **Descript** | MCP connector (no install) | any | AI/text-native editing |
| **secure-vault** ([kaustubhupadhyaya/secure-vault](https://github.com/kaustubhupadhyaya/secure-vault), private) | external, machine-local (`%USERPROFILE%\.agents\skills\secure-vault`) | yt-saved-playlists (`scripts/yt_auth.py`) | in-process DPAPI credential read for YouTube OAuth — not bundled in this repo by design; see that repo for setup |

Captions specifically are produced by **stable-ts** (timing) + reading the reference's on-screen text (words) + **ffmpeg/libass** (burning an ASS file). WhisperX is intentionally excluded — no Python 3.14 wheels.

## Creating a new skill

1. Copy `skills/_template/` to `skills/<your-skill-name>/`.
2. Edit `SKILL.md`: set `name`, write a precise `description` (this is what
   triggers the skill), then add step-by-step instructions.
3. Drop any supporting scripts/assets in the same folder.

## Tooling notes

The **Descript** integration (available in this environment as an MCP connector)
is the primary lever for video/audio editing — it can import media, trim,
rearrange, remove filler words, add captions, and publish. Skills here should
lean on it where it fits. Key tools: `import_media`, `prompt_project_agent`,
`get_project`, `list_projects`, `publish_project`.

## Backlog / ideas

- [ ] Remove filler words ("um", "uh") from a cut
- [x] Auto-generate captions / subtitles — `reel-maker` (word-accurate via stable-ts)
- [ ] Trim silence and dead air
- [x] Assemble a rough cut from a folder of raw clips — `reel-maker`
- [ ] Batch-export to platform presets (YouTube, Shorts, Reels)
