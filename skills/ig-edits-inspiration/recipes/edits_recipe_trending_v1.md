# Edits Inspiration Recipe — Trending Pacing & Audio (v1)

**Extracted from Instagram Edits (`com.instagram.basel`) on Vivo 1951**  
**Date:** 2026-09-12  
**Target Engine:** `reel-maker` (PC-side via `editor-pack`)

---

## 1. Observed Trending Templates

### Template A: "Ultra-Kinetic Beat Sync" (Top Trending)
- **Creator Reference:** `boss_jxtt`
- **Total Duration:** 0:06 (6.0 seconds)
- **Cut Count:** 38 micro-cuts (~0.15s per cut average)
- **Pacing Pattern:** Rapid montage / staccato burst cutting synchronized to audio transients / beat drop
- **Usage Metrics:** 4,934 creators
- **Aspect Ratio:** 9:16 vertical (1080x1920)
- **Best Suited For:** High-energy POV cycling / moto pass-bys / scenery highlights

### Template B: "High Save Rate Narrative Cut"
- **Creator Reference:** `shoahmedk`
- **Audio Track:** `Original audio — skylarandthestory`
- **Pacing Pattern:** Moderate rhythm (1.0s – 1.8s per clip)
- **Usage Metrics:** 1,077 creators (High save rate badge)
- **Aspect Ratio:** 9:16 vertical (1080x1920)
- **Best Suited For:** Route exploration, smooth cruising clips, scenic vistas

---

## 2. Trending Audio Tracks (from Edits Audio Hub)

| Rank | Audio Title | Creator | Popularity / Reach | Best Template Pairing |
|---|---|---|---|---|
| **#1** | *Original audio* | `punam__rajput_2310` | 100K reels (3.6M views) | Template A (kinetic drops) |
| **#2** | *Original audio* | `at_tyagii` | 34K reels (216K views) | Template B (rhythmic cruiser) |

---

## 3. PC-Side Execution Recipe for `reel-maker`

```bash
# Recommended reel-maker / ffmpeg parameters derived from this recipe:
# - Aspect: 1080x1920 (9:16)
# - Pacing: 6 to 10 seconds total duration
# - Sub-clips: 6 to 10 hero cuts selected via clip-picker (0.8s to 1.2s each)
# - Audio: Trending beat-sync or instrumental background
```

*Generated autonomously via `ig-edits-inspiration` skill (`capture.py`)*
