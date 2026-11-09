# Saved Event-Recite Recipe (Edits Inspiration Feed Card)

**Extracted from Instagram Edits (`com.instagram.basel`) — Inspiration feed card by `rashadventure`**  
**Date:** 2026-09-15  
**Target Engine:** `reel-maker` (PC-side via `editor-pack`)

---

## 1. Observed Inspiration Template

### Template: "Event Recite" (High save rate)
- **Creator Reference:** `rashadventure`
- **Content:** Conference audience + speaker on stage; bold kinetic text "speaker 30+ talks" driving the recap.
- **Pacing Pattern:** Event b-roll in 2–3s cuts — crowd reaction, stage wide, speaker close-up — under kinetic title cards.
- **Usage Metrics:** 42.7K (uses/saves per card), High save rate badge.
- **Emotion:** Professional aspirational event-recap energy, motivational.
- **Aspect Ratio:** 9:16 vertical (1080x1920).
- **Best Suited For:** Stage / event / speaker b-roll, crowd energy, talk-recap sizzle cuts.

---

## 2. Audio (from Inspiration Card)

| Rank | Audio Title | Creator | Popularity / Reach | Best Template Pairing |
|---|---|---|---|---|
| **#1** | *Winter Letters · Krytex* (shown on card) | Krytex | Inspiration-card track | Event Recite (atmospheric bed under ducked live audio) |

---

## 3. Matched Favorites (verified in `_index.merged.jsonl`)

Primary plus two confirmed stage/event matches, all present in
`D:\PersonalArchive-backup\GooglePhotos\Favorites\videos\_index.merged.jsonl` with `quality: ok`. No
filenames dropped.

| File | Duration | Merged-index description / reel use |
|---|---|---|
| `account__a75a7281daca4651b036242ebae70dfd.mp4` (primary) | 91.16s | Suit + violin under spotlight, dark stage — yes: spotlight violin performance |
| `account__6f84526a937648769f35329f26101c10.mp4` | 9.06s | Suited man, blue placard, dark stage — yes: brief stage excerpt cutaway |
| `account__692e7edda39b46d4b7aea95f11830266.mp4` | 61.30s | Indoor gathering, crowd faces in warm light — yes: lively indoor crowd energy |

---

## 4. PC-Side Execution Recipe for `reel-maker`

```bash
# Recommended reel-maker / ffmpeg parameters derived from this recipe:
# - Aspect: 1080x1920 (9:16), aspect-fill center crop:
#   -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p"
# - Pacing: event b-roll in 2-3s cuts; alternate crowd reaction / stage wide / speaker close-up via clip-picker
# - Title cards: bold kinetic cards, white extrabold with black stroke, centered, e.g.
#   -vf "...,drawtext=fontfile=/path/to/ExtraBold.ttf:text='speaker 30+ talks':fontsize=96:fontcolor=white:borderw=6:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2"
# - Audio: Winter Letters-style atmospheric bed UNDER ducked original live audio (sidechaincompress); note: source
#   the audio track separately — do not ship without an audio rights check per reel-maker Phase 5 rules
# - HDR guard: tone-map HDR/HLG sources per video-tool-router HLG guard before the crop chain
```

*Generated via `ig-edits-inspiration` skill from a phone survey of the Inspiration feed card.*
