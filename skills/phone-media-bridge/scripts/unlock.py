#!/usr/bin/env python3
"""unlock.py - Wake the phone, unlock the keyguard with the vault PIN, and
remove the lock barrier for the run (stay-awake + long screen timeout).

The lock-screen PIN lives in the DPAPI secure vault (label --label, field
--field; defaults phone-1/password) and is read IN-PROCESS ONLY via vault.get_secret.
It is NEVER printed, logged, or passed via argv/env. If the vault entry is
missing, this script exits 3 with the exact collect command -- the agent then
runs that popup, the user types the PIN into the popup (never chat), and the
agent re-runs this script.

Standard-shell PINs are digits; non-digit secrets are refused (keeps the
`adb shell input text` invocation shell-safe). Brief local process-table
visibility of the PIN to the phone is the accepted tradeoff (own device,
local link only).

Usage:
  python unlock.py [--adb ADB] [--serial SER] [--label phone-1] [--field password]
  python unlock.py --check-only   (exit 0 unlocked, 1 locked, 2 no device)

Exit codes: 0 unlocked/already-unlocked, 1 locked(check-only), 2 no device,
3 vault entry missing, 4 still locked after attempt.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WINGET_ADB = ("%USERPROFILE%\\AppData\\Local\\Microsoft\\WinGet\\Packages\\"
              "Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\\"
              "platform-tools\\adb.exe")
VAULT_DIR = Path("%USERPROFILE%\\.agents\\skills\\secure-vault")


def resolve_adb(explicit):
    if explicit:
        return explicit
    if os.environ.get("ADB"):
        return os.environ["ADB"]
    found = shutil.which("adb")
    if found:
        return found
    return WINGET_ADB


def run_adb(adb, serial, *args, timeout=60):
    cmd = [adb] + (["-s", serial] if serial else []) + list(args)
    return subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def device_present(adb, serial):
    r = run_adb(adb, serial, "devices", "-l")
    if r.returncode != 0:
        return False
    lines = [l for l in r.stdout.splitlines()
             if l.strip() and "List of devices" not in l
             and "daemon" not in l and "started" not in l]
    if serial:
        return any(serial in l and "\tdevice" in l or
                   (serial in l and " device " in l) for l in lines)
    return any(" device" in l or l.rstrip().endswith("device")
               for l in lines)


def is_locked(adb, serial):
    """True = keyguard showing. Checks several dumpsys signals (OEM-proof)."""
    r = run_adb(adb, serial, "shell", "dumpsys", "window", "policy",
                timeout=30)
    out = r.stdout or ""
    for pat in ("mShowingLockscreen=true", "mDreamingLockscreen=true",
                "mShowingDream=true", "mDreaming=true"):
        if pat in out:
            return True
    r2 = run_adb(adb, serial, "shell", "dumpsys", "trust", timeout=30)
    m = re.search(r"isTrusted=(\w+)", r2.stdout or "")
    if m and m.group(1) == "false":
        return True
    return False


def get_pin(label, field):
    sys.path.insert(0, str(VAULT_DIR))
    import vault
    return vault.get_secret(label, field)


def main():
    ap = argparse.ArgumentParser(description="Unlock phone via vault PIN")
    ap.add_argument("--adb", default=None)
    ap.add_argument("--serial", default=None)
    ap.add_argument("--label", default="phone-1")
    ap.add_argument("--field", default="password")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--no-stay-on", action="store_true",
                    help="skip stay-awake/screen-timeout changes")
    a = ap.parse_args()
    adb = resolve_adb(a.adb)
    result = {"label": a.label, "field": a.field, "changes": []}

    if not device_present(adb, a.serial):
        result["status"] = "NO_DEVICE"
        print(json.dumps(result), flush=True)
        print("No device in `adb devices -l`. Reconnect first.",
              file=sys.stderr)
        return 2

    locked = is_locked(adb, a.serial)
    result["locked_before"] = locked
    if a.check_only:
        result["status"] = "LOCKED" if locked else "UNLOCKED"
        print(json.dumps(result), flush=True)
        return 1 if locked else 0

    # Barrier removal for the run (reversible settings, logged here).
    if not a.no_stay_on:
        run_adb(adb, a.serial, "shell", "settings", "put", "global",
                "stay_on_while_plugged_in", "7")
        run_adb(adb, a.serial, "shell", "settings", "put", "system",
                "screen_off_timeout", "1800000")
        result["changes"] = ["stay_on_while_plugged_in=7",
                             "screen_off_timeout=1800000 (30 min)"]

    if not locked:
        result["status"] = "ALREADY_UNLOCKED"
        print(json.dumps(result), flush=True)
        return 0

    run_adb(adb, a.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    time.sleep(0.5)
    run_adb(adb, a.serial, "shell", "input", "keyevent", "82")
    time.sleep(0.5)
    run_adb(adb, a.serial, "shell", "input", "swipe",
            "500", "1500", "500", "500", "300")
    time.sleep(1.0)

    try:
        pin = get_pin(a.label, a.field)
    except Exception:
        pin = None
    if not pin:
        result["status"] = "VAULT_ENTRY_MISSING"
        print(json.dumps(result), flush=True)
        print("Vault entry %s:%s is missing. Collect it with:\n"
              '  python "%USERPROFILE%\\.agents\\skills\\secure-vault'
              '\\vault.py" gui --request "%s:%s"\n'
              "The user types the PIN into the popup (never chat), "
              "then re-run this script."
              % (a.label, a.field, a.label, a.field), file=sys.stderr)
        return 3
    if not re.fullmatch(r"\d{4,16}", pin):
        result["status"] = "PIN_FORMAT_REFUSED"
        print(json.dumps(result), flush=True)
        print("Vault secret is not a 4-16 digit PIN; refusing to type it "
              "via `input text`. Fix the vault entry and re-run.",
              file=sys.stderr)
        return 4

    run_adb(adb, a.serial, "shell", "input", "text", pin)
    del pin
    time.sleep(0.5)
    run_adb(adb, a.serial, "shell", "input", "keyevent", "66")
    time.sleep(1.5)

    if is_locked(adb, a.serial):
        result["status"] = "STILL_LOCKED"
        print(json.dumps(result), flush=True)
        print("Still locked after PIN entry (wrong PIN in vault, or an "
              "extra keyguard layer). Verify the vault entry and re-run.",
              file=sys.stderr)
        return 4
    result["status"] = "UNLOCKED"
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
