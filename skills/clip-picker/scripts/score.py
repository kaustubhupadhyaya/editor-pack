#!/usr/bin/env python3
"""score.py - Stage 3 of clip-picker.

Read WORKDIR/candidates.json, pull one full-resolution hero frame per candidate
(the montage thumbs are too small to judge sharpness), score each with the
variance-of-Laplacian (a cheap focus metric), and tile them into review.jpg with
labels. Claude views review.jpg to confirm picks and reject any that are
globally soft before cutting.

Low sharpness == whole-frame motion streak. For "flow" clips that's the desired
look, not a defect - so treat the score as a flag to LOOK, not an auto-reject.

candidates.json = list of:
  {"pick":"P01","clip":"clip_6318","file":"C:/.../clip_6318.mov",
   "start":96,"end":120,"hero":104,"title":"...","tags":["speed-blur"]}
(start/end/hero in seconds; hero is a representative instant inside the window.)

Deps: ffmpeg on PATH, numpy, pillow.
"""
import argparse, os, json, math, subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def mmss(s): return f"{int(s)//60:02d}:{int(s)%60:02d}"


def sharpness(path):
    a = np.asarray(Image.open(path).convert("L"), dtype=np.float64)
    lap = (-4 * a + np.roll(a, 1, 0) + np.roll(a, -1, 0)
           + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return float(lap.var())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    cj = os.path.join(a.work, "candidates.json")
    cands = json.load(open(cj))
    hero = os.path.join(a.work, "heroes"); os.makedirs(hero, exist_ok=True)

    for c in cands:
        out = os.path.join(hero, c["pick"] + ".jpg")
        subprocess.run(["ffmpeg", "-y", "-ss", str(c["hero"]), "-i", c["file"],
                        "-frames:v", "1", "-q:v", "2", out],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        c["sharp"] = round(sharpness(out), 1) if os.path.exists(out) else -1

    sv = [c["sharp"] for c in cands if c["sharp"] > 0]
    lo, hi = min(sv), max(sv)
    for c in cands:
        print(f'{c["pick"]:4} {c["clip"]:11} {mmss(c["start"])}-{mmss(c["end"])} '
              f'{c["end"]-c["start"]:>3}s  sharp {c["sharp"]:>7.1f}  {c["title"][:48]}')
    print(f"sharpness {lo:.0f}..{hi:.0f}  (low = heavy global blur; fine for flow shots, look before cutting)")

    COLS, TW, TH, LAB, PAD = 6, 480, 270, 52, 6
    rows = math.ceil(len(cands) / COLS)
    CW, CH = TW + PAD * 2, TH + LAB
    canvas = Image.new("RGB", (COLS * CW, rows * CH), (18, 18, 20))
    d = ImageDraw.Draw(canvas)
    fb = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 20)
    fs = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    for i, c in enumerate(cands):
        r, cl = divmod(i, COLS); x, y = cl * CW, r * CH
        try:
            canvas.paste(Image.open(os.path.join(hero, c["pick"] + ".jpg")).resize((TW, TH)), (x + PAD, y))
        except Exception:
            pass
        soft = c["sharp"] > 0 and c["sharp"] < lo + (hi - lo) * 0.18
        d.text((x + PAD + 2, y + TH + 2), f'{c["pick"]}  {c["clip"]}', font=fb, fill=(255, 220, 90))
        d.text((x + PAD + 2, y + TH + 26), f'{mmss(c["start"])}-{mmss(c["end"])}  sh{int(c["sharp"])}',
               font=fs, fill=(255, 120, 120) if soft else (180, 230, 180))
    canvas.save(os.path.join(a.work, "review.jpg"), quality=88)
    json.dump(cands, open(cj, "w"), indent=1)
    print("review sheet ->", os.path.join(a.work, "review.jpg"), " (softest flagged red)")


if __name__ == "__main__":
    main()
