# reel-maker cookbook — exact commands

Windows/PowerShell. Replace placeholders. `$ff`/`$fp` = ffmpeg/ffprobe.
`$dir` = user footage folder, `$sp` = scratch/work dir, `$ref` = downloaded reference.

## §0 — Binaries
```powershell
# ffmpeg: prefer PATH, else the WinGet Gyan build
$ff = (Get-Command ffmpeg -EA SilentlyContinue).Source
if(-not $ff){ $ff = (Get-ChildItem "C:\Users\$env:USERNAME\AppData\Local\Microsoft\WinGet\Packages" -Recurse -Filter ffmpeg.exe -EA SilentlyContinue | Select -First 1).FullName }
$fp = $ff -replace 'ffmpeg\.exe$','ffprobe.exe'
# yt-dlp
python -m pip install --user --upgrade yt-dlp   # if missing
# add ffmpeg dir to PATH for the session so yt-dlp can merge:
$env:PATH = (Split-Path $ff) + ";" + $env:PATH
```

## §1 — Download the reference (any link)
```powershell
# list formats (also proves access). Anonymous first:
python -m yt_dlp -F "URL"
# only if login-walled, add cookies (fails if that browser is open & locks the DB):
#   --cookies-from-browser edge   (or chrome/firefox)
# download best video+audio, merge to mp4:
python -m yt_dlp -f "bv*+ba/b" --merge-output-format mp4 -o "$sp\ref_%(id)s.%(ext)s" "URL"
# also grab the written caption/hashtags as posted (separate from burned-in text):
python -m yt_dlp --write-info-json --skip-download -o "$sp\ref_%(id)s.%(ext)s" "URL"
# -> read the "description" field of the resulting ref_<id>.info.json
```
Notes: IG/TikTok reels are `blob:`/MSE — yt-dlp handles the DASH merge; never fetch
the blob URL. `--cookies-from-browser` needs the browser **closed** (DB lock).

## §2 — Reverse-engineer the reference
### §2a Cut timestamps
```powershell
& $ff -i $ref -filter:v "select='gt(scene,0.25)',showinfo" -an -f null - 2>&1 |
  Select-String "pts_time" | %{ ($_ -replace '.*pts_time:([0-9.]+).*','$1') }
```
Lower the 0.25 threshold to catch soft cuts. Derive per-shot durations from the
gaps; that list drives the whole rebuild.

### §2b Layout geometry (active picture area / bars / border)
```powershell
& $ff -i $ref -vf "cropdetect=24:2:0" -frames:v 300 -an -f null - 2>&1 |
  Select-String "crop=" | Select -Last 3
# e.g. crop=700:704:10:288  => ~square band on a 720x1280 canvas, ~288px bars top/bottom
```

### §2c On-screen text (read it — this gives the correct WORDS)
```powershell
# 2 fps contact sheet (each tile = 0.5s), tiled; then Read the jpg
& $ff -y -i $ref -vf "fps=2,scale=200:-1,tile=8x9" -frames:v 1 "$sp\ref_sheet.jpg"
# To READ small burned-in captions: crop the caption band, upscale, stack N moments.
# (band y-offset from cropdetect; here lower third of a ~square band centered on 9:16)
$T=@(1.0,1.5,2.0,2.5,3.0,3.5); $k=0
foreach($t in $T){ $k++; & $ff -y -ss $t -i $ref -frames:v 1 -vf "crop=720:150:0:560,scale=1200:250" ("$sp\cap_{0}.png" -f $k) 2>&1|Out-Null }
& $ff -y -i "$sp\cap_%d.png" -vf "tile=1x$($T.Count)" -frames:v 1 "$sp\ref_caps.png"   # Read this
```
Reconstruct the caption script word-by-word with times + style (weight, color,
ALL-CAPS keywords, position). Note audio via `& $fp -v error -select_streams a -show_entries stream=codec_name,channels -of default=noprint_wrappers=1 $ref`.

### §2d Accurate caption timing with stable-ts (word-level)
```powershell
pip install --user stable-ts   # pulls torch(CPU)+openai-whisper; WhisperX won't install on newest Python
$env:PATH = (Split-Path $ff) + ";" + $env:PATH   # stable-ts shells out to ffmpeg -> MUST be on PATH
& $ff -y -i $ref -vn -ac 1 -ar 16000 -c:a pcm_s16le "$sp\ref_audio.wav"   # extract audio
```
```python
# transcribe.py  ->  python transcribe.py ref_audio.wav base
import sys, json, os, stable_whisper
audio = sys.argv[1]; model = stable_whisper.load_model(sys.argv[2] if len(sys.argv)>2 else "base")
r = model.transcribe(audio, language="en", word_timestamps=True, regroup=True, vad=False)
words = [{"word": w.word.strip(), "start": round(w.start,3), "end": round(w.end,3)}
         for s in r.segments for w in s.words]
json.dump(words, open(os.path.join(os.path.dirname(os.path.abspath(audio)),"words.json"),"w"), indent=1)
print(r.text); [print(f"{w['start']:6.2f}-{w['end']:6.2f} {w['word']}") for w in words]
```
Use `words.json` for TIMING only (each phrase's start = its first word's start, end =
its last word's end). **Take the WORDS from the §2c on-screen read, not the ASR text**
— stable-ts mishears (it turned "owned in my life" into "are only in my life"). Match
the correct on-screen words onto the aligned word times. Model downloads once (~140 MB).
`base` is fine for timing. (If you have the exact transcript already, `model.align(
audio, KNOWN_TEXT, language='en')` force-aligns it and sidesteps ASR mishears entirely.)

## §3 — Probe user footage
```powershell
Get-ChildItem "$dir\*.mp4" | %{
  $j = & $fp -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -show_entries format=duration -of json $_.FullName | ConvertFrom-Json
  "{0}: {1}x{2} dur={3}" -f $_.Name,$j.streams[0].width,$j.streams[0].height,[math]::Round([double]$j.format.duration,1)
}
Get-Content "$dir\playlist.m3u" -EA SilentlyContinue   # intended order/descriptions
```

## §4 — Motion + jitter per clip (clip-picker)
```powershell
# per-frame MAFD via scdet -> metadata file (escape the path for the filter)
$esc = ("$sp\mafd_NAME.txt") -replace '\\','/' -replace ':','\:'
& $ff -y -i "$dir\NAME.mp4" -vf "scdet=threshold=0,metadata=mode=print:file='$esc'" -an -f null - | Out-Null
# parse -> (t, mafd); per-1s mean = motion/vividness, per-1s stdev = shake/jitter
function Get-Series($f){ $t=$null; $l=New-Object System.Collections.Generic.List[object]
  foreach($ln in Get-Content $f){ if($ln -match 'pts_time:([0-9.]+)'){$t=[double]$Matches[1]}
    elseif($ln -match 'lavfi\.scd\.mafd=([0-9.]+)' -and $t -ne $null){$l.Add([pscustomobject]@{t=$t;m=[double]$Matches[1]})} } ; $l }
# Best window of length D that fits and isn't shaky: maximize mean over [s,s+D],
# skipping windows whose stdev > ~0.6; exclude ranges already used by another segment.
```
Pick fast (high mean) + steady (low stdev). Shift ±2–3s off any shaky window.

## §5 — Decisions: ask via AskUserQuestion (layout, border, captions, audio).

## §6 — Assemble
### §6a Per-segment layout filters (720x1280 canvas)
```text
# Match-reference SQUARE band, no border:
crop=720:720:280:0,scale=700:704,pad=720:1280:10:288:color=black,setsar=1,fps=30,format=yuv420p
# SQUARE band + white border ~14px:
crop=720:720:280:0,scale=664:668,pad=692:696:14:14:color=white,pad=720:1280:14:292:color=black,setsar=1,fps=30,format=yuv420p
# Full-bleed vertical 9:16 (aggressive side crop) + ~14px border + ~5% margin:
crop=406:720:437:0,scale=664:1180,setsar=1,pad=692:1208:14:14:color=white,pad=720:1280:14:36:color=black,fps=30,format=yuv420p
# 4:5 box (gentler crop): crop=576:720:352:0,scale=... then pad to 720x1280
```
Build each segment: `& $ff -y -ss <in> -i "$dir\CLIP.mp4" -t <dur> -an -vf "<filter>" -c:v libx264 -crf 18 -preset veryfast "$sp\seg_NN.mp4"`

### §6b Concat (segments share codec/params)
```powershell
(1..N | %{ "file 'seg_{0:D2}.mp4'" -f $_ }) | Set-Content "$sp\list.txt" -Encoding ASCII
& $ff -y -f concat -safe 0 -i "$sp\list.txt" -c copy "$sp\montage.mp4"
```

### §6c Captions (ASS) + audio + fade → final
`captions.ass` — PlayResX 720, PlayResY 1280; two styles (Base white; Key
big+cyan). Cue start/end come from §2d `words.json` (never hand-timed); CAPS
keywords; inline color for emphasized words `{\c&HF7C34F&}WORD`
(ASS colour = &HBBGGRR). Alignment 2 + MarginV ~585 puts text lower-third of band.
```powershell
$ass = ("$sp\captions.ass") -replace '\\','/' -replace ':','\:'
$fc = "[0:v]subtitles='$ass',fade=t=out:st=<end-3>:d=3[v];[1:a]afade=t=out:st=<end-3>:d=3[a]"
& $ff -y -i "$sp\montage.mp4" -i $ref -filter_complex $fc -map "[v]" -map "[a]" -t <TOTAL> `
  -c:v libx264 -crf 19 -preset medium -pix_fmt yuv420p -c:a aac -b:a 192k -movflags +faststart "OUT_v1.mp4"
```

## §7 — Verify + flag shaky frames
```powershell
# verify: contact sheet + Read; frame-check caption/fade times
& $ff -y -i "OUT_v1.mp4" -vf "fps=1,scale=180:-1,tile=6x6" -frames:v 1 "$sp\out_sheet.jpg"
# flag a shaky source frame (two passes: boxes, then text). Quote the fontfile!
& $ff -y -ss T -i "$dir\CLIP.mp4" -frames:v 1 -vf "drawbox=x=0:y=0:w=iw:h=70:color=black:t=fill,drawbox=x=0:y=672:w=iw:h=48:color=black:t=fill,drawbox=x=3:y=3:w=iw-6:h=ih-6:color=red:t=6" "$sp\boxed.png"
& $ff -y -i "$sp\boxed.png" -vf "drawtext=fontfile='C\:/Windows/Fonts/arialbd.ttf':text='FLAGGED SHAKY DO NOT USE':x=20:y=16:fontsize=40:fontcolor=red,drawtext=fontfile='C\:/Windows/Fonts/arialbd.ttf':text='CLIP range reason':x=20:y=682:fontsize=24:fontcolor=white" "OUT\_flagged_shaky_frames\FLAGGED_CLIP.png"
```

## Gotchas recap
- drawtext fontfile MUST be quoted: `fontfile='C\:/Windows/Fonts/arialbd.ttf'`.
- filled `drawbox` with `@alpha` → `-22`; use opaque; boxes and text in separate passes.
- use absolute y (not `ih-N`) inside drawtext if `-22` persists.
- `--cookies-from-browser` fails while that browser is open (cookie DB lock).
- prefer ASS `subtitles=` over stacked drawtext for real captions.
