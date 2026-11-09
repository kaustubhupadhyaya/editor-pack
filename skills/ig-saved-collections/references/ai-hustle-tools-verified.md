# "ai hustle" collection — AI video-editing/content tools, verified

Source: the 69-reel `ai hustle` Instagram saved collection (see
`../scripts/saved_export/ai hustle/index.jsonl`). This note tracks just the
**AI video-editing & content-creation** category pulled from that
collection, cross-checked against GitHub/web search and, where the
auto-transcribed audio was too garbled to trust, against frames pulled
directly from the reel video itself.

## Method

1. Every tool name below was first taken from the reel's spoken-audio
   transcript (Whisper, `--transcribe`) or post caption.
2. Each was checked against GitHub/web search for an actual matching repo.
3. Where the transcript was garbled enough that the name was in doubt (e.g.
   "Paul Mir pro", "Monic Pinter Turbo") or where search alone couldn't
   confirm a match (Open Montage decoys, OpenScreen vs. "Cap"), the reel was
   downloaded with `yt-dlp` and sampled at ~2 fps with `ffmpeg`; frames were
   read directly to find on-screen app names, GitHub repo pages, or terminal
   commands. This resolved every doubtful case with high confidence.

## Verified tools

| # | Reel-transcript name | Verdict | Actual GitHub repo | Stars (approx.) | Notes |
|---|---|---|---|---|---|
| 1 | Claude + Premiere Pro MCP + "HyperFrames" | ⚠️ Partial | `heygen-com/hyperframes` (MIT) | ~6.6k | HyperFrames is real. Claude itself is proprietary. "Premiere Pro MCP" wasn't pinned to one specific repo. |
| 2 | "Claude Code clipping skill" | ✅ Found via frames | `op7418/Youtube-clipper-skill` | ~2.1k | MIT, bilingual docs. Install: `npx skills add https://github.com/op7418/Youtube-clipper-skill` |
| 3 | "Free Opus Clip alternative" (reel A) | ✅ Found via frames | **ClipForge** (local web app) | n/a | Runs locally (`127.0.0.1:8000`), no-account/no-watermark. Underlying package may be `autoclip` (seen in a `pip install` command); exact GitHub URL not shown on screen. |
| 4 | "Free Opus Clip alternative" (reel B) | ✅ Found via frames | `openshorts` org → **openshorts.app** | ~3k | MIT, Docker self-host, AI moment detection + face tracking + dubbing. |
| 5 | Descript | ❌ Not open source | — | — | Commercial SaaS, $0–$65/mo tiers, no public repo. |
| 6 | **"Paul Mir Pro"** | ✅ Corrected via frames | `palmier-io` org → **Palmier / Palmier Pro** | — | Real name is "Palmier," not "Paul Mir." GPL-3.0, Swift, **macOS-only**. |
| 7 | **/brag + "HyperFrames"** | ✅ Both confirmed real | `latent-spaces/brag` (skill) on `heygen-com/hyperframes` (engine) | ~6.6k (engine) | "HyperFrames" sounded like a mishearing but checked out — HeyGen's real open-source HTML→video engine. `/brag` is its own separate repo. |
| 8 | Autoshorts | ✅ Confirmed | `JayWebtech/autoshorts` | ~775 | Tauri/React/Rust, DeepSeek/Claude/Anthropic APIs + Deepgram. |
| 9 | video-use | ✅ Confirmed | `browser-use/video-use` | ~21.6k | MIT. Built by the browser-use org. |
| 10 | Open Montage | ✅ Confirmed | `calesthio/OpenMontage` | ~54k (search) | ⚠️ Many decoy/clone-farm forks exist under unrelated orgs with identical marketing copy — this is the one to trust. |
| 11 | Remotion | ⚠️ Confirmed, caveat | `remotion-dev/remotion` | ~57.7k | **Not fully permissive** — source-available, requires a paid commercial license above a revenue/seat threshold. |
| 12 | **"MoneyPrinterTurbo"** ("Monic Pinter Turbo") | ✅ Confirmed | `harry0703/MoneyPrinterTurbo` | ~118.7k | Needs external LLM/TTS API keys (free defaults exist, e.g. Edge-TTS). |
| 13 | OpenScreen | ✅ Confirmed real (not "Cap") | Original: `siddharthvaddem/openscreen` (shown on-screen, ~17k★, now archived) → continued at `getopenscreen/openscreen` (~2.2k★) | 17k / 2.2k | Genuinely distinct from Cap — positions itself as a **Screen Studio** alternative. |
| 14 | Manim | ✅ Confirmed, two repos | `3b1b/manim` (original) or `ManimCommunity/manim` (maintained fork) | 92.2k / 40.5k | The community fork is what most people should actually use. |

**Bottom line:** all 14 resolved tools are real. 11 of 14 are genuinely open
source; Descript is commercial SaaS and Remotion is source-available (not
fully permissive). Two names were garbled by ASR and corrected from
on-screen video evidence: "Paul Mir Pro" → **Palmier**, "Monic Pinter
Turbo" → **MoneyPrinterTurbo**.

## Editing suitability — which of these are actual on-device, agent-driven video editors

(Not clipping tools, not content/shot generators — genuine editing of your
own existing raw footage, on your own machine, with an AI agent doing the
work.)

| Tool | Verdict | Why |
|---|---|---|
| **video-use** (`browser-use/video-use`) | ✅ **Best fit** | Cross-platform, drives itself entirely from Claude Code: transcribes, cuts filler words/dead air, color grades, assembles an EDL via editor sub-agents — actual editing of footage you already shot, not generation. |
| **Palmier / Palmier Pro** (`palmier-io`) | ✅ Strong, but blocked | A genuine agentic NLE — Premiere-style timeline an agent can edit directly via MCP from Claude Code/Codex/Cursor. **macOS-only** (this machine is Windows), and the creator's own words: early, unpolished. |
| **Descript** | ✅ Practical fallback | Not open source, but a mature, full desktop editor with agent-drivable editing (Agent Underlord), works on Windows today. A Descript MCP is already connected in this session. |
| HyperFrames / `/brag` | ❌ Not general editing | Renders HTML/CSS to video via headless browser + ffmpeg — a compositing/generation engine for template-driven promo videos, not an editor for arbitrary raw footage. |
| Open Montage | ❌ Generation-first | Researches, scripts, and *generates* footage/voiceovers end-to-end — the opposite of editing footage you already have, even though it has an editing stage internally. |
| Remotion | ❌ Not general editing | A React framework for programmatically *rendering* data-driven video, not for editing existing footage; also not fully open source (see above). |
| MoneyPrinterTurbo | ❌ Generation | Topic-to-video generator (script + stock/AI footage + TTS), not an editor. |
| Autoshorts / ClipForge / OpenShorts / Youtube-clipper-skill | ❌ Clipping | Explicitly excluded by definition — these find/cut moments, they don't do general editing. |
| OpenScreen | ❌ Not an editor | A screen-capture tool, not editing software. |
| Manim | ❌ Not general editing | Animation/visualization library for generated math graphics, not for editing arbitrary raw footage. |

**Recommendation:** start with **video-use** — it's cross-platform, already
partly set up on this machine (see [[video-use-tool]]), and is the only tool
in the list that's both open source and genuinely built for agent-driven
editing of your own footage rather than clipping or generating new content.
Keep **Descript** (with its MCP) as the pragmatic option for anything
video-use's opinionated pipeline doesn't cover. **Palmier** is the one to
watch, but it needs a Mac.
