---
name: phone-media-bridge
description: >-
  Move video/photo media between an Android phone (USB or wireless debugging) and the PC editing archive over ADB.
  Use when the user wants to pull phone footage onto the PC for editing ("get the videos off my phone", "copy
  yesterday's clips over"), push a finished reel back to the phone, open Instagram on the phone for hand-posting,
  or unlock the phone for an autonomous run. Assumes one phone on USB or wireless debugging with the archive on
  D:\ via junction. NOT for cutting, grading, captioning, or reel assembly (that's the sibling reel-maker skill)
  and NOT for finding edit ideas or inspiration (that's the sibling ig-edits-inspiration skill) — this skill only
  ferries bytes back and forth, unlocks the keyguard, and hands off posting to the human.
---

# Phone Media Bridge

Ferries bytes between an Android phone and the PC: pull footage for editing, push finished reels back, open
Instagram so the user can post by hand, and unlock the keyguard for autonomous runs. Four scripts in `scripts/`
(`inventory.py`, `pull.py`, `push_reel.py`, plus `unlock.py` for vault-PIN keyguard unlock);
all run by ABSOLUTE path from any cwd with `C:\Python314\python.exe`, take `--adb` (else `ADB` env, else PATH,
else the winget platform-tools fallback), and `--serial` when more than one device is attached.

## When to use

The user shot footage on their phone and wants it on the PC for editing; a finished reel needs to go back onto the
phone; the phone's Instagram app should be opened so the user can post a pushed reel themselves; or an autonomous
run needs the phone woken and unlocked without the user touching it. If the user
instead wants the reel cut, captioned, or graded, route that work through `..\..\reel-maker\SKILL.md`; if they want
what to make, route through `..\..\ig-edits-inspiration\SKILL.md`. This skill never edits and never posts.

## Inputs

- An Android phone on USB debugging or wireless debugging (Developer options → Wireless debugging; one device is
  the normal case; `--serial` picks one of several). USB needs a data-capable cable and the phone on File Transfer
  / MTP USB mode. Wireless needs phone and PC on the same Wi-Fi.
- The lock-screen PIN in the secure vault (`phone-1:password`) for autonomous unlock — never in chat, never on the
  command line; see Phase 0b.
- Optionally: which footage (a folder, a date, a filename fragment — anything matchable as a `--select`
  substring against the inventory), and which finished local reel file(s) to push back.
- Defaults if the user says nothing: inventory the standard camera roots (`/sdcard/DCIM /sdcard/Pictures
  /sdcard/Movies /sdcard/Download`), keep video+photo files >= 5 MB, pull into the `phone_pull/` archive junction,
  push reels to `/sdcard/Movies/Reels`.

## Workflow

### Phase 0 — Device check (always first)

```
& "C:\...\platform-tools\adb.exe" devices -l
```

(Any `adb` from the resolution order works; the winget fallback path is
`..._Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe` under
`%USERPROFILE%\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools` — see the scripts' `--adb` help.)
If the list is empty, STOP — do not run the other scripts — and give the user this checklist: for USB, use a
data-capable cable (many cables are charge-only), set the phone's USB mode to File Transfer / MTP, enable USB
debugging in Developer options, and accept the RSA fingerprint prompt on the phone; for wireless, confirm the
phone is on the same Wi-Fi as the PC, Wireless debugging is ON, and re-pair (`adb pair <ip>:<port>` + code) then
`adb connect <ip>:<port>` if needed. Re-run `devices -l` until the phone shows.

### Phase 0b — Unlock the phone (autonomous runs)

```
C:\Python314\python.exe "%USERPROFILE%\GitHub\editor-pack\skills\phone-media-bridge\scripts\unlock.py"
```

Wakes the phone, dismisses the keyguard swipe, types the vault PIN (`phone-1:password`, read in-process only), verifies
unlock via dumpsys, and removes the lock barrier for the run (`stay_on_while_plugged_in=7`,
`screen_off_timeout=1800000` — reversible, printed in the script's JSON line). Exit `ALREADY_UNLOCKED` means the
phone was open; proceed. Exit `VAULT_ENTRY_MISSING` means the PIN was never stored — collect it with the popup
(`python "%USERPROFILE%\.agents\skills\secure-vault\vault.py" gui --request "phone-1:password"`, user types into the
popup, never chat), then re-run `unlock.py`. PIN rules: never print, log, or pass it via argv/env; `unlock.py`
enforces digits-only and refuses anything else. Probe state any time with `unlock.py --check-only`.

### Phase 1 — Inventory the phone

```
C:\Python314\python.exe "%USERPROFILE%\GitHub\editor-pack\skills\phone-media-bridge\scripts\inventory.py" --out "D:\EditorPackArchive\phone-media-bridge\inv.json"
```

Recurses each root max 2 levels deep (skips `Android/data`, `Android/obb`, `.thumbnails`, hidden dirs), keeps
video/photo extensions over `--min-mb` (default 5 MB), and writes the `--out` JSON (file list + per-folder
counts/sizes) plus a resume-safe `.jsonl` beside it (one object per file: path, size_bytes, mtime, ext, is_video;
re-runs dedup on path so nothing is recorded twice). Show the user the per-folder summary and let THEM pick what
to pull — never pull the whole phone unasked.

### Phase 2 — Pull what the user picks

```
C:\Python314\python.exe "...\phone-media-bridge\scripts\pull.py" --from-inventory "...\inv.jsonl" --select DCIM beach --dest "...\scripts\phone_pull"
```

`--select` takes one or more path substrings (matched case-insensitively, unioned with any positional remote
paths). Files land under `phone_pull/` mirroring their `/sdcard/` relative path, and files already present
locally with the same size are skipped, so a re-run resumes an interrupted pull. Report the pulled list + total
bytes; the footage is now ordinary local media — hand it to `..\..\reel-maker\SKILL.md` or
`..\..\clip-picker\SKILL.md` for the actual edit.

### Phase 3 — Push the finished reel back and hand off

```
C:\Python314\python.exe "...\phone-media-bridge\scripts\push_reel.py" "C:\path\to\reel_v1.mp4" --open-app
```

Creates `--remote-dir` (default `/sdcard/Movies/Reels`) via `adb shell mkdir -p`, pushes each reel, and with
`--open-app` launches the app (`--package`, default `com.instagram.android`, the main IG app used for posting —
the Edits app is `com.instagram.basel`, verified on-device 2026-09-20). Then print "handed off to user to post":
the user posts by hand on the phone. NEVER attempt posting automation — no intents with media pre-attached, no UI
scripting, no credential handling.

## Output

Pulled footage under `scripts\phone_pull\` (junction to the D:\ archive, mirroring `/sdcard/` relative paths);
pushed reel(s) in `/sdcard/Movies/Reels` on the phone; the Instagram app open on the phone when `--open-app` is
used; an unlocked, stay-awake phone after Phase 0b. Confirm each with the script's printed summary (pulled list +
bytes, push destinations, unlock JSON status, handoff line).

## Gotchas

- Windows consoles default to cp1252: every script here reconfigures stdout to UTF-8 first, so always run them
  under `C:\Python314\python.exe` as documented rather than piping through something that re-decodes.
- Charge-only USB cables are the commonest "no devices" cause on USB — if `devices -l` is empty but everything
  looks right, swap the cable before anything else. On wireless, the commonest cause is the phone leaving the
  Wi-Fi or Wireless debugging toggling itself off (check both on the phone).
- `adb devices` showing `unauthorized` means the RSA prompt is still pending on the phone — accept it there
  (tick "always allow" to stop it recurring), no script flag fixes that.
- Lock-screen PIN handling: the PIN lives ONLY in the DPAPI vault (`phone-1:password`). Never ask for it in chat,
  never print/log it, never pass it via argv or env — `unlock.py` reads it in-process via `vault.get_secret`.
  Missing entry → popup collect → re-run. Wrong PIN shows as `STILL_LOCKED` — verify the vault entry, don't
  hammer attempts.
- HEVC/HLG phone footage may need tone-mapping before SDR export — that guard is
  `..\..\video-tool-router\SKILL.md`'s job at edit/export time, not this skill's; pull the bytes as-is.
- Multiple devices attached: every script takes `--serial`; without it adb refuses and the scripts fail loudly
  rather than touching the wrong phone.

## Where the data actually lives

`scripts\phone_pull\` is a **directory junction**, not a real folder — it points at
`D:\EditorPackArchive\phone-media-bridge\phone_pull\` on external storage. Scripts and `SKILL.md` stay in this
repo; the (potentially large) pulled phone media lives on D:\ instead of bloating a git working tree. This is
transparent to every script here — they just read/write `phone_pull/...` like normal and the junction resolves
it — so nothing above this note changes in practice.

Re-create the junction if this repo is ever cloned fresh onto a new machine (junctions aren't real files, so
they don't survive `git clone`, and this path is gitignored anyway):

```powershell
mkdir D:\EditorPackArchive\phone-media-bridge\phone_pull -Force
cmd /c mklink /J "%USERPROFILE%\GitHub\editor-pack\skills\phone-media-bridge\scripts\phone_pull" "D:\EditorPackArchive\phone-media-bridge\phone_pull"
```

`mklink /J` (a junction, not `/D` a symlink) is deliberate: it needs no admin privilege / Developer Mode, matching
the convention in `..\..\ig-saved-collections\SKILL.md`. If the archive drive isn't attached, `phone_pull/` will
simply appear empty/missing rather than erroring — reattach the drive (or re-run the command above) before
pulling. `pull.py` falls back to a real local `phone_pull\` dir with a loud warning if the junction is missing,
so an interrupted setup fails obvious, never silent.
