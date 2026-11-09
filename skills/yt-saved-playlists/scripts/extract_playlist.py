"""Extract the CLOSED CAPTIONS (transcript of what's said) for every video in
a YouTube playlist -- or for a single video -- one video at a time, plus a
summary index.

Usage:
    python extract_playlist.py --playlist PLxxxx                       # playlist you own (API auth)
    python extract_playlist.py --playlist "https://...list=PLxxxx"     # URL form works too
    python extract_playlist.py --playlist WL --cookies yt-cookies.txt  # Watch Later (cookie auth)
    python extract_playlist.py --video "https://youtu.be/VIDEOID"      # one video
    python extract_playlist.py --playlist PLxxxx --limit 10            # smoke test first

Enumeration (which videos are in the playlist):
  - Default: the official YouTube Data API (OAuth token from list_playlists.py) --
    covers playlists you created incl. private ones, and Liked Videos. Zero risk.
  - --cookies <cookies.txt>: yt-dlp --flat-playlist instead -- the only way to
    reach Watch Later and playlists saved from other channels. See cookbook §2
    for the mandatory private-window cookie-export ritual.
  - Public playlists work with either (no cookies needed for yt-dlp on public).

Captions (unlike Instagram, YouTube SERVES its captions -- no ASR needed):
  1. PRIMARY: youtube-transcript-api -- cleanest plain text (manual captions
     preferred over auto-generated when both exist). ~1 req/s pacing.
  2. FALLBACK (per video, only when primary fails with PoTokenRequired /
     NoTranscriptFound / parse errors): yt-dlp --skip-download subtitle pull,
     asking for original-language auto-subs ("en-orig,en-en,...") which avoids
     the auto-TRANSLATED-subs 429 rate-limit trap entirely.
  3. Videos with genuinely no caption track (music, shorts, disabled) are
     recorded as such -- no local Whisper by default (that's 100-1000x slower;
     ask before re-transcribing those with ASR).
  If youtube-transcript-api reports RequestBlocked/IpBlocked, the run STOPS --
  that means YouTube is throttling this IP and continuing makes it worse.

Output (in --out/<playlist title>/index.jsonl) -- AGENT-READABLE, NOT
human-wrapped: this is bulk archival raw data on external storage, opened by
an agent on demand rather than read directly in an editor. One JSON object
per video (position, video_id, title, channel, url, transcript_source,
transcript) on its own line -- the full transcript is inlined as a normal
JSON string, not split into a separate per-video .txt file, so the whole
playlist's captions are greppable/jq-able from one file.
Resumable: re-running skips any video_id already present in index.jsonl.
"""
import argparse
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from youtube_transcript_api import (
    YouTubeTranscriptApi,
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)
from googleapiclient.errors import HttpError

from yt_auth import get_service, export_cookies_txt

SKILL_DIR = Path(__file__).resolve().parent.parent

JSONL_FIELDS = ["position", "video_id", "title", "channel", "url", "transcript_source", "transcript"]

# Caption strategy, promoted from benchmark_captions.py's measured ranking.
# Primary = youtube-transcript-api (fastest, cleanest, no auth). The yt-dlp
# fallback config is NOT optional: live testing showed it 429s on the timedtext
# endpoint without a JS runtime + an authenticated cookie jar, and recovers with
# both. json3/srv* had no advantage over vtt in the benchmark, and vtt avoids the
# sub-format edge cases -- so vtt/srt/best.
STRATEGY = {
    "primary": "yta",
    "ytdlp_sub_format": "vtt/srt/best",
    "ytdlp_sleep_args": ["-t", "sleep"],
    "ytdlp_js_runtime": "node",
    "ytdlp_use_cookies": True,
    "workers": 1,
}


class RateLimitTripped(Exception):
    """yt-dlp fallback saw a 429 / bot-check -- stop before the IP block deepens."""


_YTDLP_BLOCK_RE = re.compile(r"HTTP Error 429|Too Many Requests|Sign in to confirm|not a bot", re.I)


def load_seen_video_ids(jsonl_path: Path) -> set:
    """video_ids already recorded, so a re-run resumes instead of re-fetching
    -- including videos that came back with no caption track at all, exactly
    like the old per-video-file existence check did."""
    if not jsonl_path.exists():
        return set()
    seen = set()
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                row = json.loads(line)
                if row.get("video_id"):
                    seen.add(row["video_id"])
    return seen


def parse_playlist_id(s: str) -> str:
    m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", s)
    return m.group(1) if m else s


def parse_video_id(s: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else s


def safe_name(s: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in s).strip()[:80] or "playlist"


# ---------------------------------------------------------------- enumeration

def enumerate_via_api(playlist_id: str):
    """(title, [{video_id,title,channel,position}]) via YouTube Data API OAuth.
    get_service() self-heals a dead token (auto-regrant through the profile)."""
    yt = get_service()
    if playlist_id == "LL":
        title = "Liked Videos"  # the LL pseudo-playlist has no playlists.list entry
    else:
        pl = yt.playlists().list(part="snippet", id=playlist_id, maxResults=1).execute()
        title = pl["items"][0]["snippet"]["title"] if pl.get("items") else playlist_id

    videos, tok = [], None
    while True:
        r = yt.playlistItems().list(
            part="snippet,contentDetails,status", playlistId=playlist_id, maxResults=50, pageToken=tok
        ).execute()
        for it in r.get("items", []):
            vtitle = it["snippet"]["title"]
            if vtitle in ("Deleted video", "Private video"):
                continue
            videos.append({
                "video_id": it["contentDetails"]["videoId"],
                "title": vtitle,
                "channel": it["snippet"].get("videoOwnerChannelTitle", ""),
                "position": it["snippet"].get("position", len(videos)),
            })
        tok = r.get("nextPageToken")
        if not tok:
            return title, videos


def enumerate_via_ytdlp(playlist_id: str, cookies: str | None):
    """(title, videos) via yt-dlp --flat-playlist. Needed for WL / saved-from-others
    (with cookies); also works for any public playlist without cookies."""
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J", url]
    if cookies:
        cmd += ["--cookies", cookies]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        tail = (r.stderr or "").strip().splitlines()[-1] if r.stderr else "unknown error"
        sys.exit(f"yt-dlp playlist enumeration failed: {tail}\n"
                 "(If this is Watch Later or a private list: stale/missing cookies -- redo cookbook §2's export ritual.)")
    data = json.loads(r.stdout)
    videos = [
        {"video_id": e["id"], "title": e.get("title") or e["id"],
         "channel": e.get("channel") or e.get("uploader") or "", "position": i}
        for i, e in enumerate(data.get("entries") or []) if e
    ]
    return data.get("title") or playlist_id, videos


# ------------------------------------------------------------------ captions

def fetch_transcript_primary(ytt: YouTubeTranscriptApi, video_id: str, languages: list[str]):
    """Returns (text, source_label). Prefers the requested languages; if none
    match, takes whatever caption track exists (manual preferred). Raises
    youtube-transcript-api exceptions upward."""
    try:
        fetched = ytt.fetch(video_id, languages=languages)
    except NoTranscriptFound:
        listing = ytt.list(video_id)
        transcript = next(iter([t for t in listing if not t.is_generated] or list(listing)), None)
        if transcript is None:
            raise
        fetched = transcript.fetch()
    text = " ".join(s.text.strip() for s in fetched if s.text.strip())
    kind = "auto-generated" if fetched.is_generated else "manual"
    return re.sub(r"\s+", " ", text).strip(), f"{kind} captions ({fetched.language_code})"


def json3_to_text(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    words = [seg["utf8"] for ev in data.get("events", []) for seg in ev.get("segs", []) if seg.get("utf8", "").strip()]
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def vtt_to_text(path: Path) -> str:
    lines, out = path.read_text(encoding="utf-8").splitlines(), []
    for ln in lines:
        ln = re.sub(r"<[^>]+>", "", ln).strip()
        if not ln or "-->" in ln or ln == "WEBVTT" or ln.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if not out or out[-1] != ln:  # YouTube VTT roll-up duplicates every line; dedupe consecutive repeats
            out.append(ln)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def fetch_transcript_ytdlp(video_id: str, cookies: str | None):
    """Fallback: yt-dlp subtitle-only pull, configured per STRATEGY. 'en.*,en-orig'
    requests ORIGINAL-language tracks and skip=translated_subs blocks the
    auto-TRANSLATED subs that YouTube 429-rate-limits (#13831). Needs a JS runtime
    + cookies or the timedtext endpoint 429s (proven live). Raises RateLimitTripped
    on a 429/bot-check so the caller can stop before the IP block deepens."""
    with tempfile.TemporaryDirectory() as td:
        cmd = [
            sys.executable, "-m", "yt_dlp", "--skip-download", "--ignore-config",
            "--js-runtimes", STRATEGY["ytdlp_js_runtime"],
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "en.*,en-orig",
            "--sub-format", STRATEGY["ytdlp_sub_format"],
            "--extractor-args", "youtube:skip=translated_subs",
            *STRATEGY["ytdlp_sleep_args"],
            "-o", "%(id)s.%(ext)s", "-P", td,
        ]
        if cookies:
            cmd += ["--cookies", cookies]
        cmd.append(f"https://www.youtube.com/watch?v={video_id}")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if _YTDLP_BLOCK_RE.search(proc.stderr or ""):
            raise RateLimitTripped(video_id)
        td_path = Path(td)
        for f in sorted(td_path.glob(f"{video_id}*.json3")):
            text = json3_to_text(f)
            if text:
                return text, "auto-subs via yt-dlp (json3)"
        for ext in ("vtt", "srt"):
            for f in sorted(td_path.glob(f"{video_id}*.{ext}")):
                text = vtt_to_text(f)
                if text:
                    return text, f"auto-subs via yt-dlp ({ext})"
    return None, None


def _known_playlist_ids(scripts_dir: Path) -> set:
    pj = scripts_dir / "playlists.json"
    if pj.exists():
        return {p["id"] for p in json.loads(pj.read_text(encoding="utf-8"))}
    return set()


def _ensure_cookies(args) -> str:
    """cookies.txt for the yt-dlp path: use --cookies if given, else auto-export
    from the authenticated google-5 profile (Playwright returns decrypted values,
    so Chrome's v20 App-Bound Encryption never blocks us)."""
    if args.cookies:
        return args.cookies
    print("Exporting cookies from the authenticated profile (Watch Later / saved-from-others path)...")
    return str(export_cookies_txt())


def choose_access(playlist_id: str, args):
    """Route enumeration by target and return (title, videos):
      WL / :ytwatchlater          -> yt-dlp + profile cookies (API can't reach WL)
      id in playlists.json, or LL -> Data API (owned/private/Liked; zero risk)
      unknown PL*                 -> Data API, fall back to yt-dlp on a 404
    Real auth/quota errors (401/403/quotaExceeded) are NOT swallowed."""
    if playlist_id in ("WL", ":ytwatchlater"):
        return enumerate_via_ytdlp(playlist_id, _ensure_cookies(args))

    known = _known_playlist_ids(Path(__file__).resolve().parent)
    if playlist_id in known or playlist_id == "LL":
        return enumerate_via_api(playlist_id)

    try:
        return enumerate_via_api(playlist_id)
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status == 404:
            print(f"Playlist {playlist_id} isn't in your account (404) -> yt-dlp "
                  "(public playlist, or saved-from-others -- pass --cookies if it's private)...")
            return enumerate_via_ytdlp(playlist_id, args.cookies)
        raise  # 401/403/quotaExceeded: surface the real message, don't mask as "no captions"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--playlist", help="Playlist id or URL. WL = Watch Later (needs cookies, auto-exported from the "
                                       "profile); LL = Liked Videos (Data API, no cookies).")
    g.add_argument("--video", help="Single video id or URL instead of a playlist.")
    ap.add_argument("--cookies", help="cookies.txt override for the yt-dlp path. Omit and the WL/saved-from-others path "
                                      "auto-exports cookies from the authenticated profile.")
    ap.add_argument("--out", default="yt_export", help="Output root folder.")
    ap.add_argument("--languages", default="en", help="Comma-separated caption language priority (default: en). Falls back to any existing track if none match.")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N videos (0 = whole playlist).")
    ap.add_argument("--item-sleep", type=float, default=1.2, help="Base seconds between videos (jitter added).")
    # Publish straight to the Second Brain after extracting (curation selectors mirror to_second_brain.py).
    ap.add_argument("--publish-to-second-brain", action="store_true", help="After extracting, publish a note to the vault.")
    ap.add_argument("--positions", help="Publish selector: playlist positions e.g. '1-10,14'.")
    ap.add_argument("--videos", help="Publish selector: comma-separated video ids/URLs.")
    ap.add_argument("--match", help="Publish selector: title/channel regex.")
    ap.add_argument("--with-transcripts-only", action="store_true", help="Publish selector: drop no-caption videos.")
    ap.add_argument("--curation", help="Publish selector: hand-picked id list file.")
    ap.add_argument("--publish-limit", type=int, default=0, help="Publish selector: cap the note's video count.")
    ap.add_argument("--slug-suffix", help="Publish: required to cut a 2nd note from the same playlist.")
    args = ap.parse_args()

    languages = [x.strip() for x in args.languages.split(",") if x.strip()]

    if args.video:
        vid = parse_video_id(args.video)
        pl_title, videos = "single_videos", [{"video_id": vid, "title": vid, "channel": "", "position": 0}]
    else:
        pl_id = parse_playlist_id(args.playlist)
        pl_title, videos = choose_access(pl_id, args)

    out_dir = Path(args.out) / safe_name(pl_title)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "index.jsonl"

    print(f"Playlist {pl_title!r}: {len(videos)} videos. Extracting captions...\n")
    ytt = YouTubeTranscriptApi()
    seen = load_seen_video_ids(jsonl_path)
    done = skipped = 0

    with open(jsonl_path, "a", encoding="utf-8") as jf:
        for n, v in enumerate(videos, 1):
            if v["video_id"] in seen:
                skipped += 1
                continue

            text, source = None, "none"
            try:
                text, source = fetch_transcript_primary(ytt, v["video_id"], languages)
            except (RequestBlocked, IpBlocked):
                print("\n!! YouTube is rate-limiting/blocking this IP (RequestBlocked). STOPPING now -- "
                      "continuing would extend the block. Re-run in ~24h; already-fetched records are kept.")
                break
            except (TranscriptsDisabled, NoTranscriptFound):
                source = "none"
            except (PoTokenRequired, VideoUnavailable, CouldNotRetrieveTranscript) as e:
                print(f"    primary failed ({type(e).__name__}) -> yt-dlp fallback")
                try:
                    text, source = fetch_transcript_ytdlp(v["video_id"], args.cookies)
                except RateLimitTripped:
                    print("\n!! yt-dlp fallback hit a 429 / bot-check. STOPPING now -- continuing would "
                          "extend the IP block. Re-run in ~24h; already-fetched records are kept.")
                    break
                source = source or "none"

            record = {
                "position": v.get("position", n), "video_id": v["video_id"], "title": v["title"], "channel": v["channel"],
                "url": f"https://www.youtube.com/watch?v={v['video_id']}",
                "transcript_source": source if text else "none",
                "transcript": text,
            }
            jf.write(json.dumps({k: record.get(k) for k in JSONL_FIELDS}, ensure_ascii=False) + "\n")
            jf.flush()
            seen.add(v["video_id"])
            done += 1
            status = source if text else "no captions"
            print(f"  [{n}/{len(videos)}] {v['title'][:70]!r} — {status}")

            if args.limit and done >= args.limit:
                print(f"\nHit --limit {args.limit}, stopping.")
                break
            time.sleep(args.item_sleep + random.uniform(0.1, 0.8))

    print(f"\nDone. {done} new, {skipped} already present -> {jsonl_path}")

    if args.publish_to_second_brain:
        from to_second_brain import publish, parse_positions
        note_path = publish(
            pl_title, Path(args.out),
            videos=[x.strip() for x in args.videos.split(",")] if args.videos else None,
            positions=parse_positions(args.positions) if args.positions else None,
            match=args.match, with_transcripts_only=args.with_transcripts_only,
            curation=args.curation, limit=args.publish_limit, slug_suffix=args.slug_suffix,
        )
        print(f"Published to Second Brain -> {note_path}")


if __name__ == "__main__":
    main()
