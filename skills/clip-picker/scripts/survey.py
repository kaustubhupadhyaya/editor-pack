#!/usr/bin/env python3
"""survey.py - Stage 1 of clip-picker.

Turn footage Claude cannot stream into something it CAN read: timestamped
contact-sheet montages. One thumbnail every N seconds, tiled into a grid, with
the source timecode burned onto every tile. Claude then visually scans a whole
clip in a few images instead of watching it in real time.

Usage:
  python survey.py INPUT [INPUT ...] --out WORKDIR
    [--interval 4] [--cols 6] [--rows 6] [--width 384]

INPUT = video files and/or directories (dirs are globbed for common extensions).
Prints per-clip specs and flags HDR (HLG/PQ) sources that need tone-mapping on
export (see the HLG guard in the video-tool-router skill).

Deps: ffmpeg + ffprobe on PATH. Windows font path is hard-coded in FONT.
"""
import argparse, os, glob, subprocess, json, sys

VID_EXT = (".mov", ".mp4", ".mkv", ".avi", ".m4v", ".webm", ".hevc", ".3gp", ".mts")
FONT = "C\\:/Windows/Fonts/arial.ttf"  # ffmpeg drawtext colon-escaping on Windows


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,codec_name,color_transfer",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True).stdout
    j = json.loads(out); s = j["streams"][0]
    num, den = (s.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
    fps = round(float(num) / float(den or 1), 1)
    return dict(w=s.get("width"), h=s.get("height"), fps=fps,
                codec=s.get("codec_name"), trc=s.get("color_transfer"),
                dur=float(j["format"]["duration"]))


def collect(inputs):
    files = []
    for inp in inputs:
        if os.path.isdir(inp):
            for e in VID_EXT:
                files += glob.glob(os.path.join(inp, "**", "*" + e), recursive=True)
        else:
            files.append(inp)
    return sorted(set(files))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=4.0, help="seconds between thumbnails")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--rows", type=int, default=6)
    ap.add_argument("--width", type=int, default=384)
    a = ap.parse_args()

    mont = os.path.join(a.out, "montages"); os.makedirs(mont, exist_ok=True)
    files = collect(a.input)
    if not files:
        sys.exit("no video files found in: " + " ".join(a.input))
    h = int(a.width * 9 / 16)
    for f in files:
        p = ffprobe(f); stem = os.path.splitext(os.path.basename(f))[0]
        hdr = p["trc"] in ("arib-std-b67", "smpte2084")
        print(f'{stem}: {p["w"]}x{p["h"]} {p["fps"]}fps {p["codec"]} {p["dur"]:.0f}s'
              + (f'  [HDR {p["trc"]} -> tonemap on export]' if hdr else ''))
        vf = (f"fps=1/{a.interval},scale={a.width}:{h},"
              f"drawtext=fontfile='{FONT}':text='%{{pts\\:hms}}':x=6:y=6:fontsize=16:"
              f"fontcolor=yellow:box=1:boxcolor=black@0.6,tile={a.cols}x{a.rows}")
        subprocess.run(["ffmpeg", "-y", "-i", f, "-vf", vf, "-q:v", "4",
                        os.path.join(mont, f"{stem}_sheet_%02d.jpg")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"\nmontages -> {mont}")
    print(f"NEXT: Read each *_sheet_*.jpg, then author {os.path.join(a.out, 'candidates.json')}")


if __name__ == "__main__":
    main()
