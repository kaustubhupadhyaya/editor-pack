---
name: video-tool-router
description: >-
  Route any video task to the right tool BEFORE running it — cut/trim/join/concat/remux/extract-audio,
  transcode/scale/compress, silence/dead-air removal, programmatic assembly with text/overlays/transitions,
  color grade / HLG->SDR tone-map / denoise / stabilize / upscale, 3D titles/motion-graphics, or AI/text-based
  editing. Picks among FFmpeg, auto-editor, MoviePy, DaVinci Resolve (Python API), Blender headless, and Descript
  MCP, and only pays DaVinci Resolve's ~15x cost when corrective/creative refinement actually earns it. Use
  whenever deciding HOW to process video on this Windows laptop, especially iPhone HEVC / 10-bit HLG footage
  (which greys out on a naive SDR H.264 export). Trigger words: cut, trim, join, concat, remux, transcode,
  compress, scale, convert, re-encode, silence, dead air, jump cut, overlay, caption, transition, color grade,
  tone-map, HDR, HLG, denoise, stabilize, upscale, SuperScale, titles, motion graphics, "edit this video".
---

# Video Tool Router

Pick the cheapest tool that fully satisfies the task. On this machine, transcode QUALITY is source-limited and
non-differentiating (every tool lands VMAF 98.4-99.8), so **never** pick a tool "because it's higher quality" for
a plain transcode. Only pick DaVinci Resolve when the task is CORRECTIVE or CREATIVE (grade, tone-map, denoise,
stabilize, upscale, multi-clip finish) — that is the one regime where it is the right tool. It costs ~15x
FFmpeg's time plus heavier setup, and its edge on grade/denoise/stabilize/upscale is a domain-standard
expectation, NOT something the benchmark below measured (that benchmark only covers plain transcode).

## Decision tree (match the FIRST rule that fits)

1. **No pixel change** — cut / trim / join / concat / remux / container change / extract audio
   -> **FFmpeg stream-copy (`-c copy`)**. ~1.2s, lossless. Fastest real edit — always prefer it when pixels
   don't need to change. Two caveats: (a) the cut is **keyframe-accurate**, not frame-accurate — the start
   snaps to the nearest keyframe; re-encode if you need an exact frame boundary. (b) the output **stays in the
   source codec (HEVC)** — if you need an H.264 deliverable, this rule does not apply; go to rule 2.

2. **Transcode / scale / compress** (pixels change, no correction) -> **FFmpeg libx264**.
   Default `-preset medium -crf 20`. Use `veryfast`/`ultrafast` when speed matters — quality barely differs
   (ultrafast is still VMAF 99.66). Reach for `veryslow` **only when storage/bandwidth-bound** — 40x the
   compute over ultrafast buys +0.12 VMAF (invisible) but nearly halves the file. Even then, `medium` already
   captures almost all of that shrink (13.3 MB vs veryslow's 12.6 MB), so `veryslow` is rarely worth ~6x
   medium's time.

3. **Silence / dead-air removal on talking-head or tutorial audio** -> **auto-editor**.
   WARN: very slow on HEVC (software decode) and it ONLY helps if the audio actually has gaps. Useless on
   continuous-audio footage (ride/road/music clips) — it will grind for ~30+ min and cut 0 seconds.

4. **Programmatic assembly** — scripted cuts + text/overlays/transitions in Python -> **MoviePy 2.x**.
   Expect roughly ffmpeg speed plus Python overhead. Use when the composition logic is easier in code than in a
   long ffmpeg `filter_complex`.

5. **Color grade / HLG->SDR tone-map / denoise / stabilize / upscale (SuperScale) / multi-clip creative finish**
   -> **DaVinci Resolve via Python API**. The one regime where "do it in Resolve" is correct. ~15x FFmpeg time,
   color-managed (not a transparent pass-through). Its quality edge here is domain-standard, not benchmarked
   below. Setup in its own section.

6. **3D titles / motion-graphics** -> **Blender headless -> PNG frames -> ffmpeg mux**.
   NOTE: Blender 5.1's render `image_settings.file_format` enum has NO `FFMPEG`/video option. Do NOT try
   `image_settings.file_format='FFMPEG'` or scripted VSE video export — it's broken on this build. Render image
   frames, then mux with ffmpeg.

7. **AI / text-based editing** (edit by transcript, remove filler words, "make it shorter") -> **Descript MCP**.

## Exact commands (copy-pasteable)

### 1. Stream-copy (no re-encode)
```bash
# trim: -t is DURATION (unambiguous). Cut is keyframe-accurate, output stays HEVC.
ffmpeg -ss 10 -i in.mov -t 15 -c copy out.mov
# concat (list.txt lines: file 'clip1.mov')
ffmpeg -f concat -safe 0 -i list.txt -c copy out.mov
# remux container / extract audio (no re-encode)
ffmpeg -i in.mov -c copy out.mp4
ffmpeg -i in.mov -vn -c:a copy out.m4a
```
(Use `-t <seconds>` for duration. Avoid `-ss ... -to ...` with `-c copy`: `-to`'s reference point is ambiguous
with input-side seeking and can produce the wrong length. For an exact frame boundary, drop `-c copy` and
re-encode — that reintroduces the HEVC-decode cost.)

### 2. Transcode HEVC -> H.264
```bash
# default balance
ffmpeg -i in.mov -c:v libx264 -preset medium -crf 20 -c:a aac -b:a 192k out.mp4
# speed-first (quality barely differs)
ffmpeg -i in.mov -c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 192k out.mp4
# scale to 720p while transcoding
ffmpeg -i in.mov -vf scale=-2:720 -c:v libx264 -preset medium -crf 20 -c:a aac out.mp4
```

### 3. Silence removal
```bash
auto-editor in.mp4 --edit audio:threshold=4% -o out.mp4
# check the cut first if unsure the audio even has gaps:
auto-editor in.mp4 --edit audio:threshold=4% --preview
```

### 4. MoviePy 2.x (renamed API: subclipped / with_position / with_duration)
```python
from moviepy import VideoFileClip, TextClip, CompositeVideoClip
clip = VideoFileClip("in.mov").subclipped(10, 25)
# NOTE: MoviePy 2.x TextClip needs a font FILE PATH, not a family name like "Arial".
txt = (TextClip(text="Hello", font=r"C:\Windows\Fonts\arial.ttf", font_size=48, color="white")
       .with_duration(5).with_position("center"))
final = CompositeVideoClip([clip, txt])
final.write_videofile("out.mp4", codec="libx264", preset="medium",
                      ffmpeg_params=["-crf", "20"])
```

### 6. Blender 3D/motion-graphics -> frames -> mux
```bash
# render animation to PNG frames (do NOT set file_format='FFMPEG' on 5.1)
blender -b scene.blend -o "//frames/f_####" -F PNG -a
# mux frames to H.264 (match -framerate to the scene fps)
ffmpeg -framerate 30 -i frames/f_%04d.png -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p out.mp4
```

## HLG guard (RUN THIS BEFORE ANY SDR H.264 EXPORT)

Some iPhone footage is 10-bit HLG. A naive libx264 export to SDR **greys out / washes color** unless tone-mapped.

```bash
# 1. probe the transfer characteristic
ffprobe -v error -select_streams v:0 -show_entries stream=color_transfer -of default=nk=1:nw=1 in.mov
```
- Output `arib-std-b67` (HLG) or `smpte2084` (PQ) -> footage is HDR: **tone-map before/while encoding**.
- Anything else (`bt709`, unknown) -> already SDR: use the normal transcode command.

```bash
# 2. HLG/PQ -> SDR BT.709 tone-map + H.264 in one pass (and tag output as bt709)
ffmpeg -i in.mov -vf \
"zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p" \
-c:v libx264 -preset medium -crf 20 -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
-c:a aac -b:a 192k out_sdr.mp4
```
`npl=100` is the nominal peak luminance used for linearization; raise it if bright highlights clip.
Alternative: let DaVinci Resolve's color management do the Rec.2020-HLG -> Rec.709 conversion (route to Resolve if
you're grading anyway — it does this more precisely than the ffmpeg tonemap).

## DaVinci Resolve Python API setup (the corrective/creative path)

GOTCHAS baked in below: **must** run on Python 3.11 (`py -3.11`) — the default 3.14 fails to init
`fusionscript.dll`. `RESOLVE_SCRIPT_API` / `RESOLVE_SCRIPT_LIB` are persisted env vars. Resolve must be running
(or launch it `-nogui`). Use a throwaway temp project, set format+codec explicitly, then render and poll.

```python
# Run with:  py -3.11 resolve_render.py
import os, sys, time
sys.path.append(os.path.join(os.environ["RESOLVE_SCRIPT_API"], "Modules"))  # required header
import DaVinciResolveScript as dvr

resolve = dvr.scriptapp("Resolve")          # Resolve must already be running (or start -nogui first)
pm      = resolve.GetProjectManager()

proj_name = "temp_render_" + str(int(time.time()))   # keep the name so cleanup can match it
proj    = pm.CreateProject(proj_name)
pool    = proj.GetMediaPool()
items   = resolve.GetMediaStorage().AddItemListToMediaPool([r"C:\in\clip.mov"])
tl      = pool.CreateTimelineFromClips("temp_timeline", items)   # also sets it current

# ---- apply the refinement that justified Resolve here ----
# (color grade / project color management for HLG->SDR / SuperScale upscale / denoise / stabilize)

proj.SetCurrentRenderFormatAndCodec("mp4", "H264")   # explicit format + codec
proj.SetRenderSettings({
    "TargetDir": r"C:\out",
    "CustomName": "out",
    "SelectAllFrames": True,
})
pid = proj.AddRenderJob()
proj.StartRendering(pid)

while proj.IsRenderingInProgress():                   # render + wait loop
    time.sleep(1)
print("render:", proj.GetRenderJobStatus(pid))

# cleanup: close current project before deleting the temp one
pm.CloseProject(proj)
pm.DeleteProject(proj_name)                           # matches the name we generated above
```

Env vars (for reference / re-persisting):
```
RESOLVE_SCRIPT_API = C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting
RESOLVE_SCRIPT_LIB = C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll
```

## Performance reality on this machine

- **HEVC software decode is the true bottleneck.** Every transcode of this iPhone footage is slow regardless of
  tool; `veryslow` took ~5 min for 15s of 720p. Every tool pays the same decode tax — the differentiator is only
  what happens after decode.
- **Prefer stream-copy (~1.2s)** whenever no pixel change is needed (and HEVC output is acceptable). If you must
  re-encode and speed matters, use FFmpeg `ultrafast`/`veryfast` (~7s).
- **DaVinci Resolve is ~15x slower** than FFmpeg and color-manages output (bigger files, output intentionally
  differs from source). Worth it only for grade/tone-map/denoise/stabilize/upscale/creative finish.
- **auto-editor can take 30+ min** on a long HEVC clip and may remove nothing — only run it on talking-head /
  tutorial audio that actually has pauses.

## Empirical basis (trust the routing, not the second decimal)

Task: trim seconds 10-25 (15s) then transcode HEVC->H.264 720p, run serially for fair CPU timing, VMAF vs the
untouched source. Footage: iPhone HEVC 720p30 (one representative clip from a 9-clip set). **Numbers are single
runs (n=1), no repeats** — the large gaps (the routing) are solid; small ones (e.g. MoviePy vs ffmpeg medium)
are within single-run noise.

| Tool / setting                             | Time            | Size     | VMAF     | Verdict |
|--------------------------------------------|-----------------|----------|----------|---------|
| FFmpeg stream-copy (`-c copy`, stays HEVC) | 1.2s            | 11.0 MB  | lossless | Fastest; cut/join/remux only, no pixel change, output stays HEVC |
| FFmpeg libx264 ultrafast crf20             | 7.4s            | 22.3 MB  | 99.66    | Fastest real transcode; quality already at the source-limited ceiling |
| FFmpeg libx264 medium crf20                | 47.9s           | 13.3 MB  | 99.71    | Default balance; captures nearly all the size benefit |
| FFmpeg libx264 veryslow crf20              | 297s (~5 min)   | 12.6 MB  | 99.78    | +0.12 VMAF (invisible), ~0.7 MB over medium — only if size-bound |
| MoviePy (x264 medium crf20)                | 53.5s           | 13.4 MB  | 99.44*   | ~ ffmpeg speed + Python overhead (gap within noise); use for scripted comps |
| Blender VSE video export                   | FAILED          | —        | —        | No video enum on 5.1; render frames + ffmpeg mux |
| DaVinci Resolve (API, default H264)        | 109s            | 48.2 MB  | 98.40*   | ~15x slower, color-managed; corrective/creative only |
| auto-editor (full 81s clip)                | 2021s (~34 min) | —        | —        | Cut 0s: audio was continuous — wrong tool for it |

\* MoviePy and Resolve VMAF were re-measured on a matched **no-trim** pass, so they are not perfectly
apples-to-apples with the trimmed ffmpeg rows. The 48.2 MB and 98.40 for Resolve come from **different passes**
(the no-trim re-measure produced 15.24 MB): Resolve reads lower vs the untouched source because it is
color-managed, not a transparent pass-through, and its larger file is a quality-first default-bitrate artifact.
A CQ/size-matched Resolve render would change both comparisons — the 3.75x size is default-dependent, not
intrinsic.

**Takeaways the routing is built on:** (1) Plain-transcode quality is non-differentiating — don't chase it.
(2) The FFmpeg preset sweep is a speed-vs-**bitrate-efficiency** trade, not a quality trade. (3) "Most refined"
only means something for corrective/creative work; there DaVinci Resolve is the right tool (a domain-standard
expectation, not measured here) — nowhere else.
