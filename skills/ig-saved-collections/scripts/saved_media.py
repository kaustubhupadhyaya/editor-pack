"""Download the media of every saved item listed by saved_collector.py, straight from Instagram's CDN.

No Instagram API request is made here: the collector's pages already hold signed CDN links (video_versions,
image_versions2, carousel_media). Those links expire within days, so this runs beside the collector (--follow) and
fetches each page soon after it is listed. Each file is stored once in <root>/_media/, named by the post's shortcode:
<code>.mp4 / <code>.jpg, or <code>_01.jpg, <code>_02.mp4 ... for carousel slides.

Writes <root>/_state/media.jsonl (one line per file: ok / expired / failed / no-url; reruns skip finished files) and
<root>/_state/content_manifest.jsonl, the input of reel_content.py (one line per downloaded file, out_dir under
<root>/_items/).

Usage:
  python saved_media.py --follow            # beside a running collector; exits once it has stopped and all is fetched
  python saved_media.py                     # one pass over the pages already listed
  python saved_media.py --retry-failed      # try the 'failed' files again (not the expired ones)

Supervised like the collector: a watchdog exits with code 75 after 5 minutes without progress and the parent
restarts it (at most 5 times).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ig_dm_export import Watchdog  # noqa: E402

ROOT = Path(r"D:\EditorPackArchive\ig-saved-collections\saved_export")
WATCHDOG_EXIT = 75
MAX_RESTARTS = 5
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")
EXT = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/heic": ".heic", "image/png": ".png", "video/mp4": ".mp4"}

watchdog = None


def beat():
    if watchdog:
        watchdog.beat()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def best_video(node, max_width):
    vv = node.get("video_versions") or []
    if not vv:
        return None
    vv = sorted(vv, key=lambda v: (v.get("width") or 0, v.get("height") or 0))
    if max_width:
        fitting = [v for v in vv if (v.get("width") or 0) <= max_width]
        return (fitting or vv[:1])[-1]
    return vv[-1]


def best_image(node):
    cands = (node.get("image_versions2") or {}).get("candidates") or []
    return max(cands, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0)) if cands else None


def file_specs(m, max_width):
    """What to download for one saved item: one entry per photo/video (carousels: one per slide)."""
    code = m["code"]
    slides = m.get("carousel_media") if m.get("media_type") == 8 else None
    nodes = [(f"{code}_{i:02d}", f"slide_{i:02d}", s) for i, s in enumerate(slides, 1)] if slides \
        else [(code, None, m)]
    specs = []
    for stem, slide, node in nodes:
        v = best_video(node, max_width)
        if v:
            specs.append({"stem": stem, "code": code, "slide": slide, "kind": "video", "url": v["url"],
                          "width": v.get("width"), "height": v.get("height")})
            continue
        img = best_image(node)
        specs.append({"stem": stem, "code": code, "slide": slide, "kind": "image",
                      "url": img["url"] if img else None,
                      "width": (img or {}).get("width"), "height": (img or {}).get("height")})
    return specs


def listed_specs(state_dir, max_width):
    """Every file the listed pages ask for, newest page data winning (fresher signed links)."""
    specs = {}
    for pages in sorted((state_dir / "pages").glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        for line in pages.open(encoding="utf-8"):
            if not line.endswith("\n"):
                break  # the collector is still writing this page
            for m in json.loads(line)["items"]:
                if m.get("code"):
                    for s in file_specs(m, max_width):
                        specs[s["stem"]] = s
    return specs


def done_stems(media_log, retry_failed):
    if not media_log.exists():
        return set()
    final = {}
    for line in media_log.open(encoding="utf-8"):
        r = json.loads(line)
        final[r["stem"]] = r["status"]
    keep = {"ok", "expired", "no-url"} | (set() if retry_failed else {"failed"})
    return {s for s, st in final.items() if st in keep}


def download(spec, media_dir):
    """Returns (status, path or None, bytes, note). Links are signed: 403/410 means expired, not retried."""
    for attempt in range(3):
        beat()
        try:
            with requests.get(spec["url"], stream=True, timeout=(10, 120), headers={"User-Agent": UA}) as r:
                if r.status_code in (403, 410):
                    return "expired", None, 0, f"HTTP {r.status_code}"
                r.raise_for_status()
                ext = EXT.get(r.headers.get("Content-Type", "").split(";")[0].strip(),
                              ".mp4" if spec["kind"] == "video" else ".jpg")
                path = media_dir / f"{spec['stem']}{ext}"
                tmp = path.with_suffix(path.suffix + ".part")
                n = 0
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
                        n += len(chunk)
                        beat()
                os.replace(tmp, path)
                return "ok", path, n, None
        except requests.RequestException as e:
            note = f"{type(e).__name__}: {e}"[:200]
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    return "failed", None, 0, note


def collector_running(state_dir):
    pid_file = state_dir / "collector.pid"
    if not pid_file.exists():
        return False
    try:
        return psutil.pid_exists(int(pid_file.read_text().strip()))
    except ValueError:
        return False


def main():
    global watchdog
    watchdog = Watchdog()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--follow", action="store_true", help="keep fetching while saved_collector.py is running")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--max-video-width", type=int, default=0, help="largest rendition at most this wide (0 = largest)")
    args = ap.parse_args()

    root = Path(args.root) if args.root else (ROOT / args.account if args.account else ROOT)
    state_dir, media_dir, items_dir = root / "_state", root / "_media", root / "_items"
    media_dir.mkdir(parents=True, exist_ok=True)
    media_log, manifest = state_dir / "media.jsonl", state_dir / "content_manifest.jsonl"
    ok = failed = expired = 0
    total_bytes, t_start = 0, time.monotonic()
    while True:
        specs = listed_specs(state_dir, args.max_video_width) if (state_dir / "pages").exists() else {}
        finished = done_stems(media_log, args.retry_failed)
        pending = [s for stem, s in specs.items() if stem not in finished]
        if not pending:
            if args.follow and collector_running(state_dir):
                beat()
                log(f"idle: all {len(specs)} listed files handled; waiting for the collector's next page")
                for _ in range(12):
                    time.sleep(5)
                    beat()
                continue
            break
        log(f"{len(pending)} files to fetch ({len(finished)} already handled)")
        for i, s in enumerate(pending, 1):
            if not s["url"]:
                status, path, n, note = "no-url", None, 0, "no video or image link in the listing"
            else:
                status, path, n, note = download(s, media_dir)
            row = {"t": datetime.now().isoformat(timespec="seconds"), "stem": s["stem"], "code": s["code"],
                   "slide": s["slide"], "kind": s["kind"], "status": status, "bytes": n,
                   "file": path.name if path else None, "width": s["width"], "height": s["height"]}
            if note:
                row["note"] = note
            with media_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            if status == "ok":
                ok += 1
                total_bytes += n
                out_dir = items_dir / s["code"] / s["slide"] if s["slide"] else items_dir / s["code"]
                with manifest.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": s["stem"], "media": str(path), "kind": s["kind"],
                                        "out_dir": str(out_dir)}) + "\n")
            elif status == "expired":
                expired += 1
            elif status == "failed":
                failed += 1
            beat()
            if i % 10 == 0 or i == len(pending):
                rate = total_bytes / 1024 / max(1.0, time.monotonic() - t_start)
                log(f"media: {ok} ok, {expired} expired, {failed} failed | {total_bytes / 1048576:.0f} MB "
                    f"at {rate:.0f} KB/s | batch {i}/{len(pending)}")
            time.sleep(1.0)
    log(f"MEDIA DONE: {ok} downloaded, {expired} expired links, {failed} failed this run; "
        f"{total_bytes / 1048576:.0f} MB")


def supervise():
    env = dict(os.environ, SAVED_MEDIA_CHILD="1")
    for restart in range(MAX_RESTARTS + 1):
        rc = subprocess.run([sys.executable, "-u", __file__, *sys.argv[1:]], env=env).returncode
        if rc != WATCHDOG_EXIT:
            return rc
        if restart < MAX_RESTARTS:
            print(f"restart {restart + 1}/{MAX_RESTARTS}: resuming", flush=True)
    print(f"watchdog stopped the downloader {MAX_RESTARTS + 1} times; giving up", flush=True)
    return WATCHDOG_EXIT


if __name__ == "__main__":
    if os.environ.get("SAVED_MEDIA_CHILD"):
        main()
    else:
        sys.exit(supervise())
