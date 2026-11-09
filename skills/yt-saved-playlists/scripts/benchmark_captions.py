"""Benchmark caption-extraction tools on the user's OWN videos and rank them.

The point is MEASURED evidence, not a rerun of web research: we draw a
stratified ~20-video sample from real playlists, run several extraction "arms"
over the identical sample, and score each by whether it produced non-empty
text -- never by process exit code (yt-dlp exits 0 while writing nothing).

Arms
  yta-serial     youtube-transcript-api, 1 thread (today's primary)
  ytdlp-1w-vtt   yt-dlp -t sleep, skip=translated_subs, --sub-format vtt/srt/best
  ytdlp-1w-json3 same but json3/vtt/best  (settles the json3 question with data)
  ytdlp-3w-vtt   the vtt arm across 3 worker processes (settles concurrency)
  pytubefix      only if already importable (never auto-installed)

Output: benchmarks/<ISO>/{results.json, summary.md}. summary.md ends with a
literal STRATEGY = {...} dict to paste into extract_playlist.py.

Usage:
    python benchmark_captions.py --seed 42 --n 20
    python benchmark_captions.py --arms yta-serial,ytdlp-1w-vtt --cooldown 60
"""
import argparse
import concurrent.futures
import datetime as dt
import difflib
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi, IpBlocked, RequestBlocked

from extract_playlist import json3_to_text, vtt_to_text, safe_name  # reuse parsers
from yt_auth import get_service

SKILL_DIR = Path(__file__).resolve().parent.parent
BENCH_DIR = Path(__file__).resolve().parent / "benchmarks"
MIN_CHARS = 40  # non-empty threshold for "did this arm actually get captions?"

# yt-dlp in 2026 needs a JS runtime for full YouTube extraction (deno default;
# node is what's installed here) and gets 429'd on the timedtext endpoint
# without an authenticated session -- both proven live during this benchmark.
# The realistic production config, and so the config under test, uses both.
JS_RUNTIME = "node"
COOKIES_PATH: str | None = None  # set in main() from --cookies (defaults to yt-cookies.txt)


class RateLimitTripped(Exception):
    """YouTube throttled this IP; stop the arm rather than dig the hole deeper."""


# --------------------------------------------------------------- sampling

def _bucket_duration(seconds: int) -> str:
    if seconds < 300:
        return "short"
    if seconds <= 1200:
        return "mid"
    return "long"


def build_sample(seed: int, n: int, per_playlist_cap: int = 2) -> list[dict]:
    """Stratified sample across playlists using three free signals from
    videos.list: contentDetails.caption (creator captions exist), duration,
    categoryId==10 (Music). Deterministic by seed; cached to sample.json."""
    cache = BENCH_DIR / "sample.json"
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("seed") == seed and cached.get("n") == n:
            print(f"Reusing cached sample ({len(cached['videos'])} videos, seed {seed}).")
            return cached["videos"]

    playlists = json.loads((SKILL_DIR / "scripts" / "playlists.json").read_text(encoding="utf-8")) \
        if (SKILL_DIR / "scripts" / "playlists.json").exists() \
        else json.loads((Path(__file__).resolve().parent / "playlists.json").read_text(encoding="utf-8"))

    yt = get_service()
    # candidate pool: first page of each owned playlist (skip the huge Liked list for sampling)
    candidates: dict[str, str] = {}  # video_id -> playlist_title
    for p in playlists:
        if p["id"] in ("LL", "WL") or p.get("privacy") == "system":
            continue
        try:
            r = yt.playlistItems().list(part="contentDetails", playlistId=p["id"], maxResults=50).execute()
        except Exception:
            continue
        for it in r.get("items", []):
            vid = it["contentDetails"]["videoId"]
            candidates.setdefault(vid, p["title"])

    ids = list(candidates)
    meta: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        r = yt.videos().list(part="contentDetails,snippet", id=",".join(chunk)).execute()
        for v in r.get("items", []):
            cd, sn = v["contentDetails"], v["snippet"]
            dur = _iso8601_seconds(cd.get("duration", "PT0S"))
            meta[v["id"]] = {
                "video_id": v["id"],
                "title": sn.get("title", ""),
                "channel": sn.get("channelTitle", ""),
                "playlist": candidates.get(v["id"], ""),
                "stratum": (
                    f"cap={cd.get('caption', 'false')}"
                    f"|dur={_bucket_duration(dur)}"
                    f"|music={'y' if sn.get('categoryId') == '10' else 'n'}"
                ),
            }

    rnd = random.Random(seed)
    pool = list(meta.values())
    rnd.shuffle(pool)
    # round-robin by stratum, capping per playlist
    by_stratum: dict[str, list] = {}
    for v in pool:
        by_stratum.setdefault(v["stratum"], []).append(v)
    picked: list[dict] = []
    per_pl: dict[str, int] = {}
    strata = sorted(by_stratum)
    while len(picked) < n and any(by_stratum.values()):
        for s in strata:
            if not by_stratum[s]:
                continue
            v = by_stratum[s].pop()
            if per_pl.get(v["playlist"], 0) >= per_playlist_cap:
                continue
            picked.append(v)
            per_pl[v["playlist"]] = per_pl.get(v["playlist"], 0) + 1
            if len(picked) >= n:
                break

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"seed": seed, "n": n, "videos": picked}, indent=2, ensure_ascii=False),
                     encoding="utf-8")
    print(f"Built sample: {len(picked)} videos across {len(by_stratum)} strata.")
    return picked


def _iso8601_seconds(iso: str) -> int:
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


# ------------------------------------------------------------------ scoring

def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def score_text(text: str | None) -> tuple[bool, int, int]:
    t = _norm(text)
    return (len(t) >= MIN_CHARS, len(t), len(t.split()) if t else 0)


_BLOCK_RE = re.compile(r"HTTP Error 429|Too Many Requests|Sign in to confirm|not a bot", re.I)


def _stderr_blocked(stderr: str) -> bool:
    return bool(_BLOCK_RE.search(stderr or ""))


# -------------------------------------------------------------------- arms

def arm_yta(video_id: str) -> dict:
    ytt = YouTubeTranscriptApi()
    try:
        fetched = ytt.fetch(video_id, languages=["en"])
    except (RequestBlocked, IpBlocked):
        raise RateLimitTripped(video_id)
    except Exception:
        # fall back to whatever track exists (manual preferred)
        try:
            listing = ytt.list(video_id)
            tr = next(iter([t for t in listing if not t.is_generated] or list(listing)), None)
            if tr is None:
                return {"text": None, "source": None, "error": "NoTranscriptFound", "ext": None}
            fetched = tr.fetch()
        except (RequestBlocked, IpBlocked):
            raise RateLimitTripped(video_id)
        except Exception as e:
            return {"text": None, "source": None, "error": type(e).__name__, "ext": None}
    text = " ".join(s.text for s in fetched if s.text.strip())
    kind = "auto" if fetched.is_generated else "manual"
    return {"text": _norm(text), "source": f"yta:{kind}:{fetched.language_code}", "error": None, "ext": None}


def _ytdlp_sub(video_id: str, sub_format: str, workers_sleep: bool) -> dict:
    with tempfile.TemporaryDirectory() as td:
        cmd = [
            sys.executable, "-m", "yt_dlp", "--skip-download", "--ignore-config",
            "--js-runtimes", JS_RUNTIME,
            "--write-subs", "--write-auto-subs",
            "--sub-langs", "en.*,en-orig",
            "--sub-format", sub_format,
            "--extractor-args", "youtube:skip=translated_subs",
            "-o", "%(id)s.%(ext)s", "-P", td,
        ]
        if COOKIES_PATH:
            cmd += ["--cookies", COOKIES_PATH]
        if workers_sleep:
            cmd += ["-t", "sleep"]
        cmd.append(f"https://www.youtube.com/watch?v={video_id}")
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if _stderr_blocked(proc.stderr):
            raise RateLimitTripped(video_id)
        tdp = Path(td)
        for ext, parser in (("json3", json3_to_text), ("vtt", vtt_to_text), ("srt", _srt_to_text)):
            for f in sorted(tdp.glob(f"{video_id}*.{ext}")):
                try:
                    text = parser(f)
                except Exception:
                    text = ""
                if _norm(text):
                    return {"text": _norm(text), "source": f"ytdlp:{ext}", "error": None, "ext": ext}
    return {"text": None, "source": None, "error": "no-subs-written", "ext": None}


def _srt_to_text(path: Path) -> str:
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln.isdigit() or "-->" in ln:
            continue
        if not out or out[-1] != ln:
            out.append(ln)
    return _norm(" ".join(out))


def arm_ytdlp_vtt(video_id: str) -> dict:
    return _ytdlp_sub(video_id, "vtt/srt/best", workers_sleep=True)


def arm_ytdlp_json3(video_id: str) -> dict:
    return _ytdlp_sub(video_id, "json3/vtt/best", workers_sleep=True)


def arm_pytubefix(video_id: str) -> dict:
    try:
        from pytubefix import YouTube
    except Exception:
        return {"text": None, "source": None, "error": "skipped-not-installed", "ext": None}
    try:
        yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
        caps = yt.captions
        track = caps.get("en") or caps.get("a.en") or (list(caps)[0] if len(caps) else None)
        if not track:
            return {"text": None, "source": None, "error": "NoCaptions", "ext": None}
        text = re.sub(r"<[^>]+>", " ", track.generate_srt_captions())
        return {"text": _norm(_srt_text_inline(text)), "source": "pytubefix", "error": None, "ext": None}
    except Exception as e:
        return {"text": None, "source": None, "error": type(e).__name__, "ext": None}


def _srt_text_inline(s: str) -> str:
    return _norm(" ".join(ln for ln in s.splitlines()
                          if ln.strip() and not ln.strip().isdigit() and "-->" not in ln))


ARMS = {
    "yta-serial": (arm_yta, 1, 1.2),
    "ytdlp-1w-vtt": (arm_ytdlp_vtt, 1, 0.0),
    "ytdlp-1w-json3": (arm_ytdlp_json3, 1, 0.0),
    "ytdlp-3w-vtt": (arm_ytdlp_vtt, 3, 0.0),
    "pytubefix": (arm_pytubefix, 1, 0.5),
}


def run_arm(name: str, sample: list[dict], continue_after_block: bool) -> list[dict]:
    fn, workers, sleep_s = ARMS[name]
    rows: list[dict] = []
    print(f"\n--- arm {name} ({workers} worker(s)) over {len(sample)} videos ---")

    def one(idx_v):
        idx, v = idx_v
        t0 = time.time()
        try:
            res = fn(v["video_id"])
            blocked = False
        except RateLimitTripped:
            return {"blocked_at": idx, **v}
        ok, chars, words = score_text(res["text"])
        return {
            "arm": name, "idx": idx, "video_id": v["video_id"], "stratum": v["stratum"],
            "ok": ok, "chars": chars, "words": words, "wall_s": round(time.time() - t0, 2),
            "source": res["source"], "error": res["error"], "ext": res["ext"],
            "text": res["text"] if ok else None,
        }

    if workers == 1:
        for idx, v in enumerate(sample):
            r = one((idx, v))
            if "blocked_at" in r:
                print(f"  !! blocked at index {idx} -- stopping arm {name}")
                rows.append({"arm": name, "idx": idx, "video_id": v["video_id"], "blocked": True})
                if not continue_after_block:
                    rows.append({"arm": name, "aborted_after": idx})
                    return rows
            else:
                rows.append(r)
                print(f"  [{idx+1}/{len(sample)}] {'OK ' if r['ok'] else '-- '} {r['chars']:>6}c  {r['source'] or r['error']}")
            if sleep_s:
                time.sleep(sleep_s)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(one, (idx, v)): idx for idx, v in enumerate(sample)}
            blocked_idx = None
            for fut in concurrent.futures.as_completed(futs):
                r = fut.result()
                if "blocked_at" in r:
                    blocked_idx = r["blocked_at"] if blocked_idx is None else min(blocked_idx, r["blocked_at"])
                    rows.append({"arm": name, "idx": r["blocked_at"], "video_id": r["video_id"], "blocked": True})
                else:
                    rows.append(r)
            if blocked_idx is not None:
                print(f"  !! blocked (earliest index {blocked_idx}) in concurrent arm {name}")
                rows.append({"arm": name, "aborted_after": blocked_idx})
    return rows


# -------------------------------------------------------------- summarize

def summarize(sample: list[dict], results: dict[str, list[dict]]) -> str:
    per_video_best: dict[str, int] = {}
    texts: dict[str, dict[str, str]] = {}
    for arm, rows in results.items():
        for r in rows:
            if r.get("ok"):
                per_video_best[r["video_id"]] = max(per_video_best.get(r["video_id"], 0), r["chars"])
                texts.setdefault(r["video_id"], {})[arm] = r.get("text") or ""

    captionable = set(per_video_best)
    all_ids = {v["video_id"] for v in sample}
    no_caption = all_ids - captionable

    lines = ["# Caption tool benchmark", ""]
    lines.append(f"Sample: {len(sample)} videos | captionable (>=1 arm got text): {len(captionable)} | "
                 f"no caption track (0 arms): {len(no_caption)}")
    lines.append("")
    lines.append("| arm | success/captionable | blocks | videos/min | median chars | fidelity |")
    lines.append("|---|---|---|---|---|---|")

    ranking = []
    for arm, rows in results.items():
        data = [r for r in rows if "ok" in r]
        blocked = any(r.get("blocked") or "aborted_after" in r for r in rows)
        succ = [r for r in data if r["video_id"] in captionable]
        n_succ = sum(1 for r in succ if r["ok"])
        wall = sum(r.get("wall_s", 0) for r in data) or 1e-9
        vpm = round(len(data) / (wall / 60.0), 1) if data else 0.0
        chars_sorted = sorted(r["chars"] for r in data if r["ok"])
        med = chars_sorted[len(chars_sorted) // 2] if chars_sorted else 0
        # fidelity: for videos this arm solved AND >=2 arms solved, ratio vs longest
        fids = []
        for r in data:
            if not r.get("ok"):
                continue
            vid = r["video_id"]
            others = texts.get(vid, {})
            if len(others) >= 2:
                longest = max(others.values(), key=len)
                fids.append(difflib.SequenceMatcher(None, r.get("text") or "", longest).ratio())
        fidelity = round(sum(fids) / len(fids), 3) if fids else 1.0
        rate = round(n_succ / max(len(captionable), 1), 3)
        lines.append(f"| {arm} | {n_succ}/{len(captionable)} ({rate:.0%}) | "
                     f"{'YES' if blocked else 'no'} | {vpm} | {med} | {fidelity} |")
        ranking.append((rate, 0 if blocked else 1, vpm, fidelity, arm))

    ranking.sort(reverse=True)
    winner = ranking[0][4] if ranking else "yta-serial"
    lines += ["", f"**Winner: `{winner}`** (rank key: success rate -> no blocks -> videos/min -> fidelity).", ""]

    # promoted STRATEGY dict
    # The yt-dlp fallback config is fixed by live evidence regardless of which
    # arm wins the primary slot: it needs a JS runtime and cookies or it 429s.
    if winner.startswith("ytdlp"):
        fmt = "json3/vtt/best" if "json3" in winner else "vtt/srt/best"
        workers = 3 if "3w" in winner else 1
        strat = {"primary": "ytdlp", "ytdlp_sub_format": fmt,
                 "ytdlp_sleep_args": ["-t", "sleep"], "workers": workers,
                 "ytdlp_js_runtime": JS_RUNTIME, "ytdlp_use_cookies": True}
    else:
        strat = {"primary": "yta", "ytdlp_sub_format": "vtt/srt/best",
                 "ytdlp_sleep_args": ["-t", "sleep"], "workers": 1,
                 "ytdlp_js_runtime": JS_RUNTIME, "ytdlp_use_cookies": True}
    strat_py = "STRATEGY = {\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in strat.items()) + "}"
    lines += ["Paste into extract_playlist.py:", "", "```python", strat_py, "```", ""]
    if no_caption:
        lines.append(f"_No-caption videos ({len(no_caption)}) are expected for music/workout/Shorts and are "
                     "excluded from the success denominator._")
    return "\n".join(lines)


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--arms", default=",".join(ARMS), help="comma list; default all")
    ap.add_argument("--cooldown", type=int, default=120, help="seconds between arms")
    ap.add_argument("--continue-after-429", action="store_true")
    ap.add_argument("--cookies", default=str(SKILL_DIR / "yt-cookies.txt"),
                    help="cookies.txt for the yt-dlp arms (default: the skill's yt-cookies.txt if present). "
                         "Pass '' to test the unauthenticated path.")
    args = ap.parse_args()

    global COOKIES_PATH
    COOKIES_PATH = args.cookies if (args.cookies and Path(args.cookies).exists()) else None
    print("yt-dlp arms:", f"cookies={'yes' if COOKIES_PATH else 'no'}", f"js-runtime={JS_RUNTIME}")

    sample = build_sample(args.seed, args.n)
    arms = [a.strip() for a in args.arms.split(",") if a.strip() in ARMS]
    rnd = random.Random(args.seed)
    rnd.shuffle(arms)
    print("arm order:", arms)

    results: dict[str, list[dict]] = {}
    for i, arm in enumerate(arms):
        results[arm] = run_arm(arm, sample, args.continue_after_429)
        if i < len(arms) - 1:
            print(f"cooldown {args.cooldown}s...")
            time.sleep(args.cooldown)

    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    outdir = BENCH_DIR / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "results.json").write_text(
        json.dumps({"seed": args.seed, "arm_order": arms, "sample": sample, "results": results},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    summary = summarize(sample, results)
    (outdir / "summary.md").write_text(summary, encoding="utf-8")
    print("\n" + summary)
    print(f"\nWrote {outdir / 'summary.md'}")


if __name__ == "__main__":
    main()
