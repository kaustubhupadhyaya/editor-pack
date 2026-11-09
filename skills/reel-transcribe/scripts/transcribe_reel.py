"""Transcribe ONE reel/short video -- the spoken words (closed-caption-style
text), plus the post caption when it's an Instagram reel -- and report how
long each stage took.

Usage:
    python transcribe_reel.py --url "https://www.instagram.com/reel/SHORTCODE/"
    python transcribe_reel.py --url SHORTCODE                     # bare IG shortcode works
    python transcribe_reel.py --latest-saved                      # newest reel in your IG saved section
    python transcribe_reel.py --latest-saved --account example_account  # ...on a named account
    python transcribe_reel.py --url "https://youtu.be/..."        # non-IG links go through yt-dlp

How each source is handled:
  - Instagram URLs / shortcodes / --latest-saved: instagrapi with the session
    from ../../ig-saved-collections (already-authenticated, no yt-dlp cookie
    dance). Gets the post caption for free.
  - Anything else: yt-dlp download + its .info.json description as the caption.
  - Note: for YouTube specifically, prefer the yt-saved-playlists skill --
    YouTube SERVES captions, so Whisper is a waste there.

Transcription: ffmpeg audio extract -> stable-ts Whisper (same engine as
reel-maker / ig-saved-collections --transcribe). Whisper mishears
singing/music-heavy audio -- transcripts are reliable for spoken dialogue,
best-effort otherwise.

Output (per the standing storage conventions):
  <out>/<shortcode>.txt -- header (owner, URL, caption, source) + transcript,
  all wrapped at 120 chars/line (readable with no horizontal scrolling).
  The transcript is also printed to stdout, followed by a stage-by-stage
  timing summary (fetch, download, audio extract, transcribe, total).
"""
import argparse
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS_DIR / "ig-saved-collections" / "scripts"))

LINE_WIDTH = 120


def wrap(text: str | None, indent: str = "") -> str:
    if not text:
        return ""
    eff = max(20, LINE_WIDTH - len(indent))
    out = "\n".join(
        textwrap.fill(p, width=eff, break_long_words=False, break_on_hyphens=False) if p.strip() else ""
        for p in text.splitlines() or [text]
    )
    return "\n".join(indent + ln if ln else "" for ln in out.splitlines()) if indent else out


def find_ffmpeg() -> str:
    import shutil
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    for p in Path(r"C:\Users").glob(r"*\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\ffmpeg.exe"):
        return str(p)
    raise SystemExit("ffmpeg not found -- required to extract the reel's audio.")


def is_instagram(url: str) -> bool:
    return "instagram.com" in url or re.fullmatch(r"[A-Za-z0-9_-]{5,}", url) is not None


def fetch_instagram(url_or_shortcode: str, account: str | None, tmp: Path, timings: dict):
    from ig_auth import get_client

    t0 = time.perf_counter()
    cl = get_client(account=account)
    if re.fullmatch(r"[A-Za-z0-9_-]{5,}", url_or_shortcode):
        url_or_shortcode = f"https://www.instagram.com/reel/{url_or_shortcode}/"
    pk = cl.media_pk_from_url(url_or_shortcode)
    m = cl.media_info(pk)
    timings["fetch metadata"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    video = Path(cl.clip_download(int(pk), folder=tmp))
    timings["download video"] = time.perf_counter() - t0
    return {
        "id": m.code, "owner": m.user.username, "caption": m.caption_text,
        "url": f"https://www.instagram.com/reel/{m.code}/", "video": video,
        "duration_s": m.video_duration,
    }


def fetch_latest_saved(account: str | None, tmp: Path, timings: dict):
    from ig_auth import get_client

    t0 = time.perf_counter()
    cl = get_client(account=account)
    items, _ = cl.collection_medias_v1_chunk("ALL_MEDIA_AUTO_COLLECTION", max_id="")
    m = next((i for i in items if i.media_type == 2), None)
    if m is None:
        sys.exit("No video/reel found near the top of the saved section.")
    timings["fetch metadata"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    video = Path(cl.clip_download(int(m.pk), folder=tmp))
    timings["download video"] = time.perf_counter() - t0
    return {
        "id": m.code, "owner": m.user.username, "caption": m.caption_text,
        "url": f"https://www.instagram.com/reel/{m.code}/", "video": video,
        "duration_s": m.video_duration,
    }


def fetch_via_ytdlp(url: str, tmp: Path, timings: dict):
    import json
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
         "--write-info-json", "-o", "%(id)s.%(ext)s", "-P", str(tmp), url],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-1] if r.stderr else "unknown"
        sys.exit(f"yt-dlp download failed: {tail}")
    timings["download video"] = time.perf_counter() - t0
    info = json.loads(next(tmp.glob("*.info.json")).read_text(encoding="utf-8"))
    video = next(p for p in tmp.iterdir() if p.suffix in (".mp4", ".mkv", ".webm"))
    return {
        "id": info.get("id", "video"), "owner": info.get("uploader") or info.get("channel") or "",
        "caption": info.get("description") or "", "url": info.get("webpage_url") or url,
        "video": video, "duration_s": info.get("duration"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="Reel URL, bare IG shortcode, or any yt-dlp-supported link.")
    g.add_argument("--latest-saved", action="store_true", help="Transcribe the newest reel in your IG saved section.")
    ap.add_argument("--account", default=None, help="Named IG account (.env.<account>); omit for the default.")
    ap.add_argument("--out", default="reel_transcripts", help="Output folder (one wrapped .txt per reel).")
    ap.add_argument("--whisper-model", default="base")
    ap.add_argument("--language", default="en", help='Language hint; pass "" to auto-detect.')
    ap.add_argument("--keep-video", action="store_true", help="Keep the downloaded mp4 next to the transcript.")
    args = ap.parse_args()

    timings: dict[str, float] = {}
    total0 = time.perf_counter()

    out_dir = Path(args.out)
    tmp = out_dir / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    import stable_whisper  # heavy import (torch) -- counted separately from transcription itself
    model = stable_whisper.load_model(args.whisper_model)
    timings["load whisper model"] = time.perf_counter() - t0

    if args.latest_saved:
        meta = fetch_latest_saved(args.account, tmp, timings)
    elif is_instagram(args.url):
        meta = fetch_instagram(args.url, args.account, tmp, timings)
    else:
        meta = fetch_via_ytdlp(args.url, tmp, timings)

    ffmpeg = find_ffmpeg()
    audio = meta["video"].with_suffix(".wav")
    t0 = time.perf_counter()
    subprocess.run([ffmpeg, "-y", "-i", str(meta["video"]), "-vn", "-ac", "1", "-ar", "16000", str(audio)],
                   check=True, capture_output=True)
    timings["extract audio"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    result = model.transcribe(str(audio), language=(args.language or None), word_timestamps=False, vad=False)
    transcript = (result.text or "").strip() or None
    timings["transcribe (whisper)"] = time.perf_counter() - t0

    audio.unlink(missing_ok=True)
    if args.keep_video:
        meta["video"].replace(out_dir / meta["video"].name)
    else:
        meta["video"].unlink(missing_ok=True)
    try:
        tmp.rmdir()
    except OSError:
        pass

    timings["TOTAL"] = time.perf_counter() - total0

    dur = f"{meta['duration_s']:.1f}s" if meta.get("duration_s") else "?"
    body = "\n".join([
        "=" * LINE_WIDTH,
        wrap(f"@{meta['owner']} — {meta['id']}  ({dur})"),
        "=" * LINE_WIDTH,
        f"URL     : {meta['url']}",
        "",
        "Post caption:",
        wrap(meta["caption"], indent="  ") or "  (none)",
        "",
        "Spoken transcript:",
        wrap(transcript, indent="  ") or "  (nothing intelligible -- likely music/singing only)",
        "",
        "Timings:",
        *(f"  {k:22} {v:6.1f}s" for k, v in timings.items()),
        "",
    ])
    out_file = out_dir / f"{meta['id']}.txt"
    out_file.write_text(body, encoding="utf-8")
    print(body)
    print(f"Saved -> {out_file}")


if __name__ == "__main__":
    main()
