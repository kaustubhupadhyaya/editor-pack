#!/usr/bin/env python3
"""pull.py - Copy chosen phone files to the PC archive over ADB.

Two ways to choose files (unioned): --from-inventory INV.jsonl --select SUB [SUB ...]
(substring match on the device path) and/or positional REMOTE paths.

Local layout mirrors the phone path under /sdcard/ (e.g. /sdcard/DCIM/x.mp4
-> phone_pull/DCIM/x.mp4). Files already present locally with the same size
are skipped, so interrupted runs resume by re-running the same command.

Usage:
  python pull.py --from-inventory INV.jsonl --select DCIM beach --dest C:\\path\\phone_pull
  python pull.py /sdcard/DCIM/Camera/VID_001.mp4 [--dest ...]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WINGET_ADB = ("%USERPROFILE%\\AppData\\Local\\Microsoft\\WinGet\\Packages\\"
              "Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\\"
              "platform-tools\\adb.exe")


def resolve_adb(explicit):
    if explicit:
        return explicit
    if os.environ.get("ADB"):
        return os.environ["ADB"]
    found = shutil.which("adb")
    if found:
        return found
    return WINGET_ADB


def resolve_dest(explicit):
    if explicit:
        return Path(explicit)
    junction = Path(__file__).parent / "phone_pull"
    if junction.is_dir():
        return junction
    fallback = Path(__file__).parent / "phone_pull"
    print("WARNING: phone_pull junction is missing (fresh clone or drive detached?) -- "
          "writing to a real local folder instead. Re-create it per SKILL.md "
          '("Where the data actually lives").', file=sys.stderr, flush=True)
    return fallback


def run_adb(adb, serial, *args):
    cmd = [adb] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def remote_size(adb, serial, remote):
    r = run_adb(adb, serial, "shell", "stat", "-c", "%s", remote)
    try:
        return int(r.stdout.strip().split()[0])
    except (ValueError, IndexError):
        return None


def local_for(dest, remote):
    rel = PurePosixPath(remote)
    try:
        rel = rel.relative_to("/sdcard")
    except ValueError:
        rel = PurePosixPath(str(rel).lstrip("/"))
    return dest.joinpath(*rel.parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("remote", nargs="*", help="device path(s), e.g. /sdcard/DCIM/Camera/VID_001.mp4")
    ap.add_argument("--from-inventory", default=None, help="inventory .jsonl to select from")
    ap.add_argument("--select", nargs="+", default=[],
                    help="path substring(s); keep inventory entries containing any of them")
    ap.add_argument("--dest", default=None, help="default: scripts/phone_pull junction")
    ap.add_argument("--serial", default=None)
    ap.add_argument("--adb", default=None, help="path to adb.exe (else ADB env, PATH, winget fallback)")
    a = ap.parse_args()

    if not a.remote and not (a.from_inventory and a.select):
        ap.error("give positional REMOTE path(s) and/or --from-inventory INV.jsonl --select SUB ...")

    adb = resolve_adb(a.adb)
    dest = resolve_dest(a.dest)
    dest.mkdir(parents=True, exist_ok=True)

    wanted = {}  # remote path -> size_bytes or None
    for r in a.remote:
        wanted[r] = None
    if a.from_inventory:
        subs = [s.lower() for s in a.select]
        with open(a.from_inventory, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                p = e.get("path", "")
                if subs and not any(s in p.lower() for s in subs):
                    continue
                wanted.setdefault(p, e.get("size_bytes"))

    pulled, skipped, total = [], [], 0
    for remote, known_size in wanted.items():
        local = local_for(dest, remote)
        size = known_size if isinstance(known_size, int) else remote_size(adb, a.serial, remote)
        if local.exists() and size is not None and local.stat().st_size == size:
            skipped.append(str(local))
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        r = run_adb(adb, a.serial, "pull", remote, str(local))
        if r.returncode != 0 or not local.exists():
            print(f"FAILED {remote}\n{(r.stderr or r.stdout).strip()}")
            continue
        n = local.stat().st_size
        pulled.append(str(local))
        total += n
        print(f"pulled {remote} -> {local} ({n / 1048576:.1f} MB)")

    print(f"\ndone: {len(pulled)} pulled ({total / 1048576:.1f} MB), {len(skipped)} already present, "
          f"into {dest}")
    for s in skipped:
        print(f"  skipped (same size): {s}")


if __name__ == "__main__":
    main()
