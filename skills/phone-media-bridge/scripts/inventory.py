#!/usr/bin/env python3
"""inventory.py - List media files on a USB-connected Android phone over ADB.

Walks each root with `adb shell ls -la` (max 2 levels deep), skips
Android/data, Android/obb, .thumbnails and hidden dirs, and keeps files whose
extension looks like video or photo and whose size clears --min-mb.

Writes --out JSON ({device, roots, files, folders}) and appends one JSON
object per NEW file to a .jsonl next to it (resume-safe, deduped on path).

Usage:
  python inventory.py --out C:\\path\\inv.json [--serial ABC123]
      [roots ...] [--video-ext .mp4 ...] [--image-ext .jpg ...] [--min-mb 5]
      [--adb C:\\path\\adb.exe]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WINGET_ADB = ("%USERPROFILE%\\AppData\\Local\\Microsoft\\WinGet\\Packages\\"
              "Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\\"
              "platform-tools\\adb.exe")
DEFAULT_ROOTS = ["/sdcard/DCIM", "/sdcard/Pictures", "/sdcard/Movies", "/sdcard/Download"]
DEFAULT_VIDEO_EXT = [".mp4", ".mov", ".mkv", ".3gp", ".webm", ".m4v", ".mts", ".hevc"]
DEFAULT_IMAGE_EXT = [".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".dng"]
MAX_DEPTH = 2  # roots are depth 0; grandchildren are the deepest level listed


def resolve_adb(explicit):
    if explicit:
        return explicit
    if os.environ.get("ADB"):
        return os.environ["ADB"]
    found = shutil.which("adb")
    if found:
        return found
    return WINGET_ADB


def adb_shell(adb, serial, *args):
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["shell"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def list_dir(adb, serial, path):
    """Return (files, subdirs) for a device dir via `ls -la`.

    files = [(name, size_bytes, mtime), ...]; subdirs = [name, ...].
    Missing/unreadable dirs return ([], []).
    """
    r = adb_shell(adb, serial, "ls", "-la", path)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or "No such file" in out or "not found" in out.lower():
        return [], []
    files, subdirs = [], []
    for line in out.splitlines():
        line = line.rstrip()
        if not line or line.startswith("total"):
            continue
        kind = line[0]
        if kind not in ("-", "d", "l"):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        name = " ".join(parts[7:])
        if name in (".", ".."):
            continue
        if kind == "d":
            subdirs.append(name)
        elif kind == "-":
            try:
                size = int(parts[4])
            except ValueError:
                continue
            files.append((name, size, parts[5] + " " + parts[6]))
    return files, subdirs


def skipped_dir(full):
    """True for Android/data, Android/obb, .thumbnails, and hidden dirs."""
    comps = [c for c in full.replace("\\", "/").split("/") if c]
    if "Android/data" in "/".join(comps) or "Android/obb" in "/".join(comps):
        return True
    return any(c.startswith(".") or c == ".thumbnails" for c in comps)


def normalize_exts(exts):
    return {e.lower() if e.startswith(".") else "." + e.lower() for e in exts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default=None, help="adb device serial (default: only connected device)")
    ap.add_argument("roots", nargs="*", default=DEFAULT_ROOTS)
    ap.add_argument("--video-ext", nargs="+", default=DEFAULT_VIDEO_EXT)
    ap.add_argument("--image-ext", nargs="+", default=DEFAULT_IMAGE_EXT)
    ap.add_argument("--min-mb", type=float, default=5.0, help="skip files smaller than this (default 5)")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--adb", default=None, help="path to adb.exe (else ADB env, PATH, winget fallback)")
    a = ap.parse_args()

    adb = resolve_adb(a.adb)
    video_ext = normalize_exts(a.video_ext)
    image_ext = normalize_exts(a.image_ext)
    min_bytes = int(a.min_mb * 1024 * 1024)

    entries, folders = [], {}
    stack = [(r.rstrip("/"), 0) for r in a.roots]
    seen_dirs = set()
    while stack:
        path, depth = stack.pop()
        if path in seen_dirs or skipped_dir(path):
            continue
        seen_dirs.add(path)
        files, subdirs = list_dir(adb, a.serial, path)
        summary = folders.setdefault(path, {"files": 0, "videos": 0, "images": 0, "bytes": 0})
        for name, size, mtime in files:
            ext = os.path.splitext(name)[1].lower()
            is_video = ext in video_ext
            if not is_video and ext not in image_ext:
                continue
            if size < min_bytes:
                continue
            full = path + "/" + name
            entries.append({"path": full, "size_bytes": size, "mtime": mtime,
                            "ext": ext, "is_video": is_video})
            summary["files"] += 1
            summary["bytes"] += size
            summary["videos" if is_video else "images"] += 1
        if depth < MAX_DEPTH:
            for sub in subdirs:
                stack.append((path + "/" + sub, depth + 1))

    entries.sort(key=lambda e: e["path"])

    out = a.out
    jsonl = out[:-len(".json")] + ".jsonl" if out.lower().endswith(".json") else out + ".jsonl"
    known = set()
    if os.path.exists(jsonl):
        with open(jsonl, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    known.add(json.loads(line).get("path"))
                except ValueError:
                    continue
    fresh = [e for e in entries if e["path"] not in known]
    with open(jsonl, "a", encoding="utf-8") as f:
        for e in fresh:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"device": a.serial or "", "roots": a.roots,
                   "files": entries, "folders": folders}, f, indent=1, ensure_ascii=False)

    print(f"scanned {len(seen_dirs)} folders, {len(entries)} media files "
          f"({len(fresh)} new) -> {out} (+ {jsonl})")
    for path in sorted(folders):
        s = folders[path]
        print(f'  {path}: {s["files"]} files ({s["videos"]} video, {s["images"]} image) '
              f'{s["bytes"] / 1048576:.1f} MB')


if __name__ == "__main__":
    main()
