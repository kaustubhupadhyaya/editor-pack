#!/usr/bin/env python3
"""push_reel.py - Send finished reel(s) back to the phone and open Instagram.

Pushes local files to --remote-dir (created via `adb shell mkdir -p`), then
optionally launches an app on the phone (default: the main Instagram app, for
hand-posting). Posting itself is ALWAYS done by the user on the phone -- this
script only gets the file there and opens the app.

Usage:
  python push_reel.py C:\\path\\reel_v1.mp4 [--remote-dir /sdcard/Movies/Reels] [--open-app]
"""
import argparse
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
DEFAULT_REMOTE_DIR = "/sdcard/Movies/Reels"
DEFAULT_PACKAGE = "com.instagram.android"  # main IG app (for posting); Edits app is com.instagram.basel


def resolve_adb(explicit):
    if explicit:
        return explicit
    if os.environ.get("ADB"):
        return os.environ["ADB"]
    found = shutil.which("adb")
    if found:
        return found
    return WINGET_ADB


def run_adb(adb, serial, *args):
    cmd = [adb] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reel", nargs="+", help="local finished reel file(s) to push")
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    ap.add_argument("--open-app", action="store_true",
                    help="launch the app on the phone after pushing (hand-off for the user to post)")
    ap.add_argument("--package", default=DEFAULT_PACKAGE,
                    help="app to open with --open-app (default: main Instagram app)")
    ap.add_argument("--serial", default=None)
    ap.add_argument("--adb", default=None, help="path to adb.exe (else ADB env, PATH, winget fallback)")
    a = ap.parse_args()

    for f in a.reel:
        if not os.path.isfile(f):
            sys.exit(f"not a file: {f}")

    adb = resolve_adb(a.adb)
    r = run_adb(adb, a.serial, "shell", "mkdir", "-p", a.remote_dir)
    if r.returncode != 0:
        sys.exit(f"could not create {a.remote_dir} on device:\n{(r.stderr or r.stdout).strip()}")

    for f in a.reel:
        name = os.path.basename(f)
        r = run_adb(adb, a.serial, "push", f, a.remote_dir + "/" + name)
        if r.returncode != 0:
            sys.exit(f"push failed for {f}:\n{(r.stderr or r.stdout).strip()}")
        print(f"pushed {f} -> {a.remote_dir}/{name}")

    if a.open_app:
        run_adb(adb, a.serial, "shell", "monkey", "-p", a.package,
                "-c", "android.intent.category.LAUNCHER", "1")
        print(f"opened {a.package} on the phone.")

    print("handed off to user to post -- posting is never automated.")


if __name__ == "__main__":
    main()
