#!/usr/bin/env python3
"""board.py - Stage 5 of clip-picker.

Turn the confirmed candidates.json into something the user can review and pick from.

  --mode thumbs : static pick-sheet (uses hero frames from score.py as thumbnails).
                  Fast; publishable as a claude.ai Artifact.
  --mode play   : cut each candidate to a browser-friendly H.264 MP4 (auto
                  HDR->SDR tone-map for HLG/PQ sources) and build a local page of
                  native <video> players. Videos are referenced by relative path,
                  so keep review_player.html in --out next to the clips.

  python board.py --work WORKDIR --mode thumbs
  python board.py --work WORKDIR --mode play --out C:\\Users\\me\\Downloads\\clips [--crf 21]

candidates.json schema: see score.py. Output keeps 16:9, trim-only (no crop).
Deps: ffmpeg + ffprobe on PATH, numpy/pillow only for --mode thumbs re-scoring is
not needed here (hero jpgs already exist). play mode needs only ffmpeg.
"""
import argparse, os, io, json, base64, html, subprocess, time

TM = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
      "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
CAT = {"speed-blur": "Speed-blur", "corner": "Corner", "vista": "Vista",
       "interaction": "Group", "atmosphere": "Atmosphere", "overtake": "Overtake",
       "scenery": "Scenery", "action": "Action", "light": "Light", "wildlife": "Wildlife"}


def mmss(s): return f"{int(s)//60:02d}:{int(s)%60:02d}"
def run(a): subprocess.run(a, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_hdr(f):
    trc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=color_transfer",
                          "-of", "default=nk=1:nw=1", f], capture_output=True, text=True).stdout.strip()
    return trc in ("arib-std-b67", "smpte2084")


def uri(p):
    return "data:image/jpeg;base64," + base64.b64encode(open(p, "rb").read()).decode()


CSS = r"""<style>
*{box-sizing:border-box}
:root{--bg:#15140f;--surface:#1c1b14;--surface2:#232118;--line:#35311f;--text:#ece6d6;
 --muted:#a89e86;--faint:#6f6752;--accent:#f2b02e;--accent-ink:#191509;--good:#6fce97;--warn:#e6944e;
 --mono:ui-monospace,"Cascadia Code",Consolas,Menlo,monospace;--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif}
@media (prefers-color-scheme:light){:root{--bg:#f1ede1;--surface:#fbf8f0;--surface2:#f3eee1;--line:#e3dbc8;
 --text:#272317;--muted:#6d6650;--faint:#9a917c;--accent:#b9761a;--accent-ink:#fff;--good:#2f8f5b;--warn:#b5561f}}
:root[data-theme="dark"]{--bg:#15140f;--surface:#1c1b14;--surface2:#232118;--line:#35311f;--text:#ece6d6;
 --muted:#a89e86;--faint:#6f6752;--accent:#f2b02e;--accent-ink:#191509;--good:#6fce97;--warn:#e6944e}
:root[data-theme="light"]{--bg:#f1ede1;--surface:#fbf8f0;--surface2:#f3eee1;--line:#e3dbc8;
 --text:#272317;--muted:#6d6650;--faint:#9a917c;--accent:#b9761a;--accent-ink:#fff;--good:#2f8f5b;--warn:#b5561f}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);line-height:1.5;-webkit-font-smoothing:antialiased}
.wrap{max-width:1240px;margin:0 auto;padding:34px 22px 80px}
header{border-top:2px solid var(--accent);padding-top:20px}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);margin:0 0 8px}
h1{font-size:clamp(26px,4vw,40px);margin:0 0 6px;letter-spacing:-.01em;font-weight:800;text-wrap:balance}
.sub{color:var(--muted);margin:0 0 18px;max-width:64ch}
.stats{display:flex;flex-wrap:wrap;border:1px solid var(--line);border-radius:4px;overflow:hidden;margin-bottom:18px;background:var(--surface)}
.stat{flex:1 1 120px;padding:12px 16px;border-right:1px solid var(--line)}.stat:last-child{border-right:0}
.stat b{display:block;font-family:var(--mono);font-size:20px;font-variant-numeric:tabular-nums}
.stat span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.how{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--accent);border-radius:4px;
 padding:14px 18px;margin-bottom:26px;font-size:14.5px;color:var(--muted)}
.how b{color:var(--text)}.how code{font-family:var(--mono);background:var(--surface2);color:var(--accent);padding:1px 6px;border-radius:3px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:4px;overflow:hidden;display:flex;flex-direction:column}
.card:hover{border-color:var(--accent)}
.thumb{position:relative;aspect-ratio:16/9;background:#000}
.thumb img,.thumb video{width:100%;height:100%;object-fit:cover;display:block}
.pick{position:absolute;top:8px;left:8px;background:var(--accent);color:var(--accent-ink);font-family:var(--mono);
 font-weight:700;font-size:13px;padding:3px 9px;border-radius:3px;pointer-events:none}
.dur{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.72);color:#fff;font-family:var(--mono);font-size:12px;
 padding:2px 7px;border-radius:3px;pointer-events:none;font-variant-numeric:tabular-nums}
.body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:8px;flex:1}
.meta{display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px}
.clip{color:var(--faint)}.win{color:var(--accent);font-variant-numeric:tabular-nums}
.title{margin:0;font-size:14.5px;line-height:1.4;font-weight:600;text-wrap:pretty}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:auto}
.chip{font-size:11px;color:var(--muted);border:1px solid var(--line);border-radius:20px;padding:2px 9px}
.fn{font-family:var(--mono);font-size:10.5px;color:var(--faint);word-break:break-all}
footer{margin-top:30px;padding-top:16px;border-top:1px solid var(--line);color:var(--faint);font-size:12.5px;font-family:var(--mono)}
</style>"""


def card(c, media):
    chips = "".join(f'<span class="chip">{CAT.get(t, t)}</span>' for t in c.get("tags", []))
    extra = f'<div class="fn">{html.escape(c["file_name"])} · {c.get("mb",0)} MB</div>' if "file_name" in c else ""
    return f'''<article class="card">
  <div class="thumb">{media}
    <span class="pick">{c['pick']}</span><span class="dur">{c['end']-c['start']}s</span></div>
  <div class="body">
    <div class="meta"><span class="clip">{c['clip']}</span><span class="win">{mmss(c['start'])}–{mmss(c['end'])}</span></div>
    <p class="title">{html.escape(c['title'])}</p>
    <div class="chips">{chips}</div>{extra}
  </div></article>'''


def page(title, eyebrow, sub, stats, how, cards):
    st = "".join(f'<div class="stat"><b>{v}</b><span>{k}</span></div>' for k, v in stats)
    return (f"<title>{title}</title>\n{CSS}\n<div class=\"wrap\">\n<header>"
            f'<p class="eyebrow">{eyebrow}</p><h1>{title}</h1><p class="sub">{sub}</p></header>'
            f'<div class="stats">{st}</div><div class="how">{how}</div>'
            f'<div class="grid">\n{"".join(cards)}\n</div>'
            f'<footer>Generated by Claude · clip-picker skill</footer></div>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--mode", choices=["thumbs", "play"], required=True)
    ap.add_argument("--out", help="output dir for --mode play (defaults to WORKDIR/clips)")
    ap.add_argument("--crf", default="21")
    a = ap.parse_args()
    cands = json.load(open(os.path.join(a.work, "candidates.json")))

    if a.mode == "thumbs":
        hero = os.path.join(a.work, "heroes")
        cards = [card(c, f'<img loading="lazy" src="{uri(os.path.join(hero, c["pick"]+".jpg"))}" alt="">')
                 for c in cands]
        how = ('<b>How to pick:</b> reply with the pick codes you want, e.g. '
               '<code>P01, P03, P07</code>. Ranked best-first.')
        out = os.path.join(a.work, "pickboard.html")
        open(out, "w", encoding="utf-8").write(
            page("Clip Picks", "footage · selection board",
                 f"{len(cands)} candidate clips, ranked best-first. Pick which to cut.",
                 [("candidates", len(cands)), ("format", "16:9"), ("length", "15–30s")], how, cards))
        print("wrote", out)
        return

    out = a.out or os.path.join(a.work, "clips")
    os.makedirs(os.path.join(out, "_posters"), exist_ok=True)
    t0 = time.time()
    for i, c in enumerate(cands):
        dur = c["end"] - c["start"]
        fn = f'{c["pick"]}_{c["clip"].replace(" ", "_")}_{mmss(c["start"]).replace(":","")}-{mmss(c["end"]).replace(":","")}.mp4'
        dst = os.path.join(out, fn); c["file_name"] = fn
        vf = ["-vf", TM] if is_hdr(c["file"]) else []
        run(["ffmpeg", "-y", "-ss", str(c["start"]), "-i", c["file"], "-t", str(dur), *vf,
             "-c:v", "libx264", "-crf", a.crf, "-preset", "faster", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst])
        pj = os.path.join(out, "_posters", c["pick"] + ".jpg")
        run(["ffmpeg", "-y", "-ss", str(c["hero"] - c["start"]), "-i", dst,
             "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", pj])
        c["mb"] = round(os.path.getsize(dst) / 1048576, 1) if os.path.exists(dst) else 0
        print(f'[{i+1}/{len(cands)}] {fn}  {c["mb"]}MB  ({time.time()-t0:.0f}s)', flush=True)

    cards = [card(c, f'<video controls preload="none" playsinline poster="{uri(os.path.join(out,"_posters",c["pick"]+".jpg"))}">'
                     f'<source src="./{html.escape(c["file_name"])}" type="video/mp4"></video>')
             for c in cands]
    how = ('<b>How to pick:</b> play the clips, then reply with the pick codes to keep — '
           '<code>P01, P03</code>. Open in Chrome/Edge for reliable local playback.')
    outhtml = os.path.join(out, "review_player.html")
    open(outhtml, "w", encoding="utf-8").write(
        page("Clip Board — click to play", "footage · playable board",
             f"{len(cands)} clips, 16:9 H.264 (HDR tone-mapped to SDR). Files live in this folder.",
             [("clips", len(cands)), ("format", "16:9 H.264"), ("length", "15–30s")], how, cards))
    print("DONE", round((time.time()-t0)/60, 1), "min ·", round(sum(c.get("mb", 0) for c in cands)), "MB ·", outhtml)


if __name__ == "__main__":
    main()
