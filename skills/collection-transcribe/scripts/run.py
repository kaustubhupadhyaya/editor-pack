"""ONE command to batch-transcribe a whole source -- runnable from ANY cwd.

    python run.py ig "ai hustle"                     # IG saved collection, default account
    python run.py ig "Better" --account example_account    # IG collection on a named account
    python run.py ig "All posts" --limit 10          # smoke test the whole saved section
    python run.py yt PLxxxxxxxx                      # YouTube playlist (served captions, no Whisper)
    python run.py yt WL --cookies yt-cookies.txt     # YouTube Watch Later

Routes to the sibling skills' extractors with the right working directory, so
output always lands in the extractor's canonical location (one folder per
collection/playlist) regardless of where you invoked this from:
  IG -> skills/ig-saved-collections/scripts/saved_export/[<account>/]<Collection>/
  YT -> skills/yt-saved-playlists/scripts/yt_export/<Playlist>/

Prints total wall-clock time and per-item average at the end. Both extractors
are resume-safe: re-run the same command after any interruption and it picks
up where it stopped.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
IG_SCRIPTS = SKILLS / "ig-saved-collections" / "scripts"
YT_SCRIPTS = SKILLS / "yt-saved-playlists" / "scripts"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["ig", "yt"], help="ig = Instagram saved collection (Whisper); yt = YouTube playlist (served captions).")
    ap.add_argument("target", help='IG collection name (e.g. "ai hustle", "All posts") or YouTube playlist id/URL (WL = Watch Later).')
    ap.add_argument("--account", help="ig only: named account (.env.<account>); omit for default.")
    ap.add_argument("--cookies", help="yt only: cookies.txt for Watch Later / saved-from-others.")
    ap.add_argument("--limit", type=int, default=0, help="Stop after N items (smoke test).")
    ap.add_argument("--languages", default=None, help='yt: caption language priority. ig: Whisper hint ("" = auto).')
    args = ap.parse_args()

    if args.source == "ig":
        cmd = [sys.executable, "extract_saved.py", "--collection", args.target,
               "--out", r".\saved_export", "--transcribe"]
        if args.account:
            cmd += ["--account", args.account]
        if args.languages is not None:
            cmd += ["--language", args.languages]
        cwd = IG_SCRIPTS
    else:
        cmd = [sys.executable, "extract_playlist.py",
               ("--video" if "watch?v=" in args.target or "youtu.be/" in args.target else "--playlist"),
               args.target, "--out", r".\yt_export"]
        if args.cookies:
            cmd += ["--cookies", args.cookies]
        if args.languages is not None:
            cmd += ["--languages", args.languages]
        cwd = YT_SCRIPTS
    if args.limit:
        cmd += ["--limit", str(args.limit)]

    print(f"-> {' '.join(cmd)}   (cwd: {cwd})\n")
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd)
    elapsed = time.perf_counter() - t0

    mins, secs = divmod(int(elapsed), 60)
    print(f"\n[run.py] total wall-clock: {mins}m {secs}s")
    print("[run.py] divide by the item count the extractor reported above for the per-item average.")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
