#!/usr/bin/env python3
"""capture.py - read-only Edits-app capture over ADB.

Ask the user to navigate the Edits app to the screen of interest, then run:
  python capture.py --name trending --dir PROJECT_DIR [--serial SERIAL] [--adb ADB_PATH]

Writes edits_<name>.png (screencap bytes) plus edits_<name>.xml (uiautomator text dump, best-effort).
Downscales oversized captures to max width 1080 as .jpg (quality 85) for token-cheap reading.
Prints one JSON line: {"screen": ..., "image": ..., "xml": ...|null, "bytes": ...}.
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

ADB_FALLBACK = ("%USERPROFILE%\\AppData\\Local\\Microsoft\\WinGet\\Packages\\"
                "Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\\platform-tools\\adb.exe")
USB_CHECKLIST = ("no Android device found. USB checklist: use a data cable (not charge-only), "
                 "set phone USB mode to File Transfer, enable USB debugging, accept the RSA prompt.")


def resolve_adb(flag):
    if flag:
        return flag
    if os.environ.get("ADB"):
        return os.environ["ADB"]
    found = shutil.which("adb")
    if found:
        return found
    return ADB_FALLBACK


def adb_base(adb, serial):
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    return cmd


def has_device(adb, serial):
    r = subprocess.run(adb_base(adb, serial) + ["devices", "-l"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False
    lines = [ln for ln in r.stdout.splitlines()[1:] if ln.strip()]
    return any("device" in ln.split()[-1:] or ln.strip().endswith("device")
               or " device " in (" " + ln + " ") for ln in lines)


def main():
    ap = argparse.ArgumentParser(description="Read-only Edits-app capture: screencap + uiautomator dump.")
    ap.add_argument("--name", required=True, help="screen label, e.g. trending, template_beatdrop")
    ap.add_argument("--serial", default=None, help="adb device serial (for multiple devices)")
    ap.add_argument("--adb", default=None, help="adb path (overrides ADB env var / PATH / fallback)")
    ap.add_argument("--dir", default=".", help="output dir (default: cwd)")
    a = ap.parse_args()

    adb = resolve_adb(a.adb)
    outdir = os.path.abspath(a.dir)
    os.makedirs(outdir, exist_ok=True)

    if not has_device(adb, a.serial):
        print("capture.py: " + USB_CHECKLIST, file=sys.stderr)
        sys.exit(1)

    base = adb_base(adb, a.serial)
    png = os.path.join(outdir, "edits_%s.png" % a.name)
    xml = os.path.join(outdir, "edits_%s.xml" % a.name)

    r = subprocess.run(base + ["exec-out", "screencap", "-p"], capture_output=True)
    if r.returncode != 0 or not r.stdout:
        print("capture.py: screencap failed: %s" % r.stderr.decode("utf-8", "replace")[:300],
              file=sys.stderr)
        sys.exit(1)
    with open(png, "wb") as f:
        f.write(r.stdout)

    image = png
    try:
        from PIL import Image
        with Image.open(png) as im:
            if im.width > 1080:
                ratio = 1080.0 / im.width
                small = im.convert("RGB").resize((1080, round(im.height * ratio)))
                image = os.path.join(outdir, "edits_%s.jpg" % a.name)
                small.save(image, quality=85)
                os.remove(png)
    except ImportError:
        print("capture.py: Pillow missing, keeping full-size PNG", file=sys.stderr)
    except Exception as e:
        print("capture.py: downscale skipped (%s), keeping PNG" % e, file=sys.stderr)

    xml_path = None
    dump = subprocess.run(base + ["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"],
                          capture_output=True, text=True)
    if dump.returncode == 0:
        pull = subprocess.run(base + ["pull", "/sdcard/window_dump.xml", xml],
                              capture_output=True, text=True)
        if pull.returncode == 0 and os.path.exists(xml):
            xml_path = xml
        else:
            print("capture.py: uiautomator pull failed (non-fatal)", file=sys.stderr)
    else:
        print("capture.py: uiautomator dump failed (non-fatal, e.g. secure screen)", file=sys.stderr)

    print(json.dumps({"screen": a.name, "image": image, "xml": xml_path,
                      "bytes": os.path.getsize(image)}))


if __name__ == "__main__":
    main()
