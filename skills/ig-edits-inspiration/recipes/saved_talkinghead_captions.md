# Saved Talking-Head Captions Recipe (Edits Ideas — Saved Family)

**Extracted from Instagram Edits (`com.instagram.basel`) — Ideas tab, user's SAVED templates (6 saved items, dominant family)**  
**Date:** 2026-09-15  
**Target Engine:** `reel-maker` (PC-side via `editor-pack`)

---

## 1. Observed Saved Templates

### Template family: "Direct-Address Talking-Head + Bold Burned-In Captions"
- **Source:** User's SAVED row in the Edits Ideas tab — 6 saved items, the dominant saved family.
- **Pattern:** Confessional direct-address talking-head to camera with BOLD burned-in word captions: top hook line +
  bottom punchline/body styling. One serif-caption variant ("the cleanest", podcast-style).
- **Observed saved examples (verbatim from phone survey):**
  - "Not rich enough to pay for Claude" / "setup under 10$" — tech frustration humor.
  - "don't like paying $20 or" — tech frustration humor (same cluster as above).
  - "My T3 endurance YouTube channels" + YouTube logo card — listicle / recommendation.
  - "...t running shoes" sweaty outdoor run selfie — fitness talk.
  - "What my indian parents wants me to play" guitar skit — comedy skit.
  - "the cleanest" podcast serif caption — serif-caption variant.
- **Emotion:** Relatable, humorous, confessional direct-address.
- **Caption treatment:** Bold white with black stroke; top hook zone + bottom body zone; serif face only on
  the "cleanest" variant.
- **Aspect Ratio:** 9:16 vertical (1080x1920).

---

## 2. Audio (from Saved Cards)

| # | Audio | Source card | Notes |
|---|---|---|---|
| 1 | Speech-led (original voice) | All 6 saved items | Keep original speech audio; trending bed optional underneath |
| 2 | Trending bed (optional, low) | (inferred: pick in-app before build) | Duck under speech, never replace it |

---

## 3. Matched Favorites (verified in `_index.merged.jsonl`)

All five filenames below were confirmed present in
`D:\PersonalArchive-backup\GooglePhotos\Favorites\videos\_index.merged.jsonl` with `quality: ok`. No
filenames dropped.

| File | Duration | Merged-index description / reel use |
|---|---|---|
| `account__b537163c957d436a8ca1ade78853d4ea.mp4` | 49.92s | Captioned commentary speaker vs yellow-teal graphic bg — yes: captioned commentary speaker |
| `account__bd825c03712d45dd88cac0d072a8b53c.mp4` | 61.63s | Indoor vlog-style monologue, warm room light — yes: expressive vlog-style monologue |
| `account__aff8142a087c42b788912a30b3147e1f.mp4` | 9.29s | Bearded speaker close-up, dark bg — yes: bearded speaker close-up |
| `account__a76916c098fb40708dad2d802bdfdd4d.mp4` | 35.67s | Night direct-address speaker, black graphic tee — yes: direct-address speaker cutaway |
| `account__b7a6e00d12e341758a9d35733c23d56d.mp4` | 37.66s | Seated indoor interview at table — yes: seated interview speaker |

---

## 4. PC-Side Execution Recipe for `reel-maker`

```bash
# Recommended reel-maker / ffmpeg parameters derived from this recipe:
# - Aspect: 1080x1920 (9:16), aspect-fill center crop:
#   -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p"
# - Pacing: 2-4s cuts; select hero beats via clip-picker from the matched favorites above
# - Captions: transcribe speech with stable-ts per reel-maker cookbook, then burn word captions:
#   libass preferred; or drawtext bold white with black stroke, e.g.
#   -vf "...,drawtext=fontfile=/path/to/Bold.ttf:fontsize=72:fontcolor=white:borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.72"
#   Top hook zone (y ~ h*0.12) for hook line; bottom body zone (y ~ h*0.70-0.78) for punchline/body
# - Serif variant: swap to elegant serif face only for the "cleanest"-style card
# - Audio: keep original speech (-c:a aac -b:a 160k); optional trending bed ducked to -22 LUFS under voice
# - HDR guard: tone-map HDR/HLG sources per video-tool-router HLG guard, e.g.
#   -vf "tonemap=hable:desat=0,zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709:t=bt709:m=bt709,format=yuv420p"
```

*Generated via `ig-edits-inspiration` skill from a phone survey of the user's SAVED templates.*
