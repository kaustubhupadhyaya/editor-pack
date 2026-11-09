---
name: phone-reel-pipeline
description: >-
  Build a reel end to end from phone footage with a single call: check the Android device, unlock it via the vault
  PIN, optionally take trend inspiration from the Edits app (self-navigated), pull media to the PC, build the reel,
  push it back, and hand off to the user to post. Use for "end to end reel from phone", "make a reel from my phone
  footage", "phone to reel", "pull from phone and make a reel", or any request that starts on the phone and ends as
  a posted reel. Pure PC editing with no phone involved goes to reel-maker directly. Inspiration-only ("what's
  trending", no build yet) goes to ig-edits-inspiration directly.
---

# phone-reel-pipeline

The end-to-end Android → Instagram-reel pipeline. The phone supplies footage (and
optionally trend inspiration); the PC does all the editing; the phone receives the
finished reel for the user to post by hand. This skill orchestrates its siblings —
it contains no scripts of its own and duplicates none of their logic. A single call
runs the whole chain: the agent unlocks the phone itself (vault PIN, no user
touching the phone) and navigates the Edits app itself when inspiration is wanted.

**Sibling skills in this repo** — phone-reel-pipeline routes to them, don't
reimplement them: [phone-media-bridge](..\..\phone-media-bridge\SKILL.md) owns the
device connection plus keyguard unlock plus media pull/push, [ig-edits-inspiration](..\..\ig-edits-inspiration\SKILL.md)
owns Edits-app inspiration (read-only capture plus self-driven navigation on an unlocked phone),
[reel-maker](..\..\reel-maker\SKILL.md) owns
the PC reel build (and itself delegates to [clip-picker](..\..\clip-picker\SKILL.md)
and [video-tool-router](..\..\video-tool-router\SKILL.md) — never call those two
directly from here). Read the sibling SKILL.md at its relative path before each
phase; their docs are authoritative for flags, paths, and gotchas.

## When to use

Triggers: "end to end reel from phone", "make a reel from my phone footage",
"phone to reel", "pull from phone and make a reel", or any task that spans device
→ PC → device in one run.

NOT for:

- Pure PC editing with footage already on disk and no phone involved → invoke
  [reel-maker](..\..\reel-maker\SKILL.md) directly.
- Inspiration-only ("what's trending", "roadmap for this edit", no build yet) →
  invoke [ig-edits-inspiration](..\..\ig-edits-inspiration\SKILL.md) directly.
- Posting → always the user, on the phone, by hand. No skill in this repo posts,
  schedules, or uploads anything — auto-posting was explicitly declined.

## Workflow

### Phase 0 — Device check + unlock

Run `adb devices -l`. Locate adb via the bridge scripts' chain (--adb flag,
ADB env, PATH, then the WinGet platform-tools fallback). If the device list is
empty, STOP and print this user checklist:

1. Wireless: phone on the same Wi-Fi as the PC, Wireless debugging ON (re-pair with
   code + `adb connect <ip>:<port>` if the link is gone).
2. USB: data-capable cable (not charge-only), File Transfer (MTP) mode, USB debugging
   enabled, RSA fingerprint prompt accepted on the phone.

If the device shows as `unauthorized`, do not proceed — ask the user to accept the
RSA prompt on the phone, then re-run `adb devices -l`. Only continue once the
device lists as `device`. Then unlock for the autonomous run (no user touch needed):

```powershell
C:\Python314\python.exe %USERPROFILE%\GitHub\editor-pack\skills\phone-media-bridge\scripts\unlock.py
```

`ALREADY_UNLOCKED` → proceed. `VAULT_ENTRY_MISSING` → collect `phone-1:password` via the vault popup
(`python "%USERPROFILE%\.agents\skills\secure-vault\vault.py" gui --request "phone-1:password"`), then re-run.
Full rules in [phone-media-bridge](..\..\phone-media-bridge\SKILL.md) Phase 0b.

### Phase 1 — Inspiration (OPTIONAL, only if the user wants trend-guided edits)

Ask one question: trend-guided (Edits inspiration) or straight build (reference
link or freeform)? If the user wants trend guidance, hand off to
[ig-edits-inspiration](..\..\ig-edits-inspiration\SKILL.md): it captures from the Edits app
(read-only capture preferred; self-driven UI navigation allowed on the unlocked phone per its doctrine) and
returns a reel-maker-shaped recipe. That recipe becomes the
build spec for Phase 3. Asking the user to navigate Edits themselves is the fast-path fallback, not a
requirement — the agent drives when the user hasn't. If the user skips inspiration, go straight to Phase 2 —
[reel-maker](..\..\reel-maker\SKILL.md) Phase 1 already accepts a reference link,
so any link the user provided travels with the handoff unchanged.

### Phase 2 — Media pull

Hand off to [phone-media-bridge](..\..\phone-media-bridge\SKILL.md): run its
inventory step, then show the user a compact per-folder summary (folder name, clip
count, total size — not a full file dump) and ask ONE question (multi-select)
which folders/clips to pull. Then run its pull step. Pulled footage lands in the
junction `skills\phone-media-bridge\scripts\phone_pull` (physically on
`D:\EditorPackArchive`). Footage stays on D: — editors read from there, never
copy it to C:. Resume-safe: if the phone unplugged mid-pull, re-run the pull; see
Gotchas.

### Phase 3 — PC build

Invoke the [reel-maker](..\..\reel-maker\SKILL.md) flow with the Phase 1 recipe
(or reference link) plus the Phase 2 footage folder. reel-maker delegates to
clip-picker + video-tool-router itself — do not duplicate that routing here. Two
things to carry across explicitly: (a) phone footage is often HEVC/HDR, so the
[video-tool-router](..\..\video-tool-router\SKILL.md) HLG 10-bit guard applies
(tone-map before grading/caption-burn, or colors wash out); (b) HEVC decode is
slow, so expect Phase 3 to dominate the runtime (see clip-picker gotchas).

### Phase 4 — Push back (APPROVAL GATE)

**HARD RULE: NEVER push anything to the phone without the user's explicit
approval for that exact file in this run.** Finishing Phase 3 does NOT imply
permission to push. Stop at the end of Phase 3 and report the local PC
path(s), duration, and size; ask "push to phone?" and proceed only on a yes.
A blanket "make reels" instruction never counts as push approval.

Once approved, hand off to [phone-media-bridge](..\..\phone-media-bridge\SKILL.md) push step
(`push_reel.py`): push the finished `reel_<name>_vN.mp4` to `/sdcard/Movies/Reels`
on the phone. Verify the file exists on-device (`adb shell ls`) and report its
byte size against the PC copy.

### Phase 5 — Hand-off (the user posts)

Run the phone-media-bridge step that opens Instagram on the phone
(`push_reel.py --open-app`). Then STOP: the USER posts, by hand, in the Instagram
app. NEVER any auto-posting — explicitly declined. Report: the reel path (phone
path + PC path), the version number, and a short what-to-check-before-posting
list (captions readable, audio present, correct aspect).

## Output

- `reel_<name>_vN.mp4` on the phone (`/sdcard/Movies/Reels/`) + the PC copy kept
  in the project folder (versioned; never overwrite v1).
- An end-to-end status report: per-phase pass / skipped / failed, wall-clock
  timings per phase, which sibling skill handled each phase, and honest caveats
  (see below). Example shape: Phase 0 pass (device `<serial>`, unlocked via vault, 5s) → Phase 1
  skipped (user chose straight build) → Phase 2 pass (3 clips, 1.2 GB, 4 min) →
  Phase 3 pass (`reel_<name>_v1.mp4`, 22 min) → Phase 4 pass (push verified,
  sizes match) → Phase 5 handed off (Instagram opened, user to post).

## Gotchas

- Junction missing after a fresh clone: `skills\phone-media-bridge\scripts\phone_pull`
  is a junction to `D:\EditorPackArchive` and does not survive a fresh clone —
  re-create it per the phone-media-bridge docs before Phase 2.
- Phone unplugged mid-run: pulls are resume-safe (re-run the pull step); pushes
  are not — re-run `push_reel.py` and re-verify with `adb shell ls`.
- Phone re-locking mid-run: `unlock.py` sets stay-awake + 30-min timeout, but a reboot
  or manual lock re-arms the keyguard — re-run `unlock.py` (PIN stays in the vault, no
  user action needed) and continue.
- HEVC decode is slow (clip-picker gotcha): contact sheets and motion scoring over
  phone footage take longer than over H.264 — say so up front, don't shrink the
  sampling to compensate.
- Never auto-post: no UI automation of the Instagram post flow, no scheduling, no
  uploads. Opening the app for the user is the last automated step.

## Honest-reporting rules

- Report what was actually verified vs assumed, per phase: device serial seen in
  `adb devices -l` (not "phone connected"), unlock verified via dumpsys (not "phone open"),
  pull byte counts matched (not "files
  copied"), push verified with `adb shell ls` + size match, Instagram opened
  (not "reel posted").
- If a phase was skipped (e.g. no inspiration), say so explicitly — never imply
  its output exists.
- Timing honesty: report measured wall-clock per phase; HEVC transcode/scoring
  time is real work, not overhead to hide.
- Flag quality caveats rather than shipping them silently: shaky picks, washed-out
  HDR, borrowed-audio repost limits (reel-maker Phase 5 rules apply).
