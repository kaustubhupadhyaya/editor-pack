# Saved Night-Drive Serif Recipe (Edits Templates — Trending Card)

**Extracted from Instagram Edits (`com.instagram.basel`) — Templates tab trending card by `v4suu.7`**  
**Date:** 2026-09-15  
**Target Engine:** `reel-maker` (PC-side via `editor-pack`)

---

## 1. Observed Trending Template

### Template: "Night-Drive Serif" (High save rate)
- **Creator Reference:** `v4suu.7`
- **Total Duration:** 0:08 (8.0 seconds)
- **Cut Count:** 3 clips (~2.6s each)
- **Pacing Pattern:** Slow, lingering holds; straight cuts (cross-dissolves acceptable) landing gently rather than on hard beats.
- **Visual:** Dark moody night-drive — red taillight glow, wet-street reflections, car-interior / road POV; elegant cream
  serif text overlay reading "in this lifetime", centered in the upper third.
- **Usage Metrics:** 50.8K uses, 3,241 likes, High save rate badge.
- **Emotion:** Cinematic melancholic romantic longing.
- **Aspect Ratio:** 9:16 vertical (1080x1920).
- **Best Suited For:** Night street / drive b-roll, moody solitude cutaways, slow romantic beats.

---

## 2. Audio (from Template Card)

| Rank | Audio Title | Creator | Popularity / Reach | Best Template Pairing |
|---|---|---|---|---|
| **#1** | *Slow atmospheric track (inferred — verify exact track in-app before build)* | Unknown (verify in Edits app) | Shown on trending card (verify) | Night-Drive Serif (slow holds) |

---

## 3. Matched Favorites (verified in `_index.merged.jsonl`)

Three night/dark-moody `usable=yes`, `quality: ok` clips confirmed present in
`D:\PersonalArchive-backup\GooglePhotos\Favorites\videos\_index.merged.jsonl`. No filenames dropped. (Note:
several night clips in per-sheet desc files read `quality: dark`; only `quality: ok` rows from the merged index
are listed here per the brief. The night-ride POV `account__6f1d617526a14b26943ee60da1ba299e.mp4` is
present in the merged index but reads `quality: dark`, so it was excluded.)

| File | Duration | Merged-index description / reel use |
|---|---|---|
| `account__e6806c976d3c4771b51cd7ef39ed41fe.mp4` | 13.44s | Teal night-street car scene (2nd half of cinematic cut) — yes: moody cinematic cutaways |
| `account__ce06644802964ce0a8a46ee6080deb48.mp4` | 17.07s | Dusk mountain silhouette, lone figure, dark water glimpse — yes: dusk landscape cutaway |
| `account__clip_8155.MOV` | 2.37s | Night portrait under tree, warm lamp glow (landscape source — crop to fill) — yes: calm night-portrait cutaway |

---

## 4. PC-Side Execution Recipe for `reel-maker`

```bash
# Recommended reel-maker / ffmpeg parameters derived from this recipe:
# - Aspect: 1080x1920 (9:16), aspect-fill center crop (note: clip_8155.MOV is 1920x1080 landscape — crop hard):
#   -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p"
# - Pacing: 8.0s total; 3 hero cuts (~2.6s each) via clip-picker from the matched favorites above
# - Transitions: straight cuts on beat preferred; or slow 12-frame cross-dissolves, e.g. xfade=duration=0.5:offset=2.1
# - Serif caption burn: elegant serif face, cream #F5EFE0, centered upper third (y ~ h*0.28), e.g.
#   -vf "...,drawtext=fontfile=/path/to/ElegantSerif.ttf:text='in this lifetime':fontsize=64:fontcolor=#F5EFE0:x=(w-text_w)/2:y=h*0.28"
# - Motion: gentle 1.05x slow-down allowed (setpts=PTS/1.05); no speed-ramp harder than 1.10x
# - Audio: slow atmospheric bed (verify exact track in-app); -c:a aac -b:a 160k, loudnorm to -14 LUFS for Reels
# - HDR guard: tone-map HDR/HLG sources per video-tool-router HLG guard before the crop chain
```

*Generated via `ig-edits-inspiration` skill from a phone survey of the Templates tab trending card.*
