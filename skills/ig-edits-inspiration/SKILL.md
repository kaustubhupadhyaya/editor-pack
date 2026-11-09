---
name: ig-edits-inspiration
description: >-
  Mine the Instagram Edits app for edit inspiration and a cutting roadmap, then build the reel on the PC via
  editor-pack. Use when the user says "inspiration from Edits", "what's trending on Edits", "make a reel like the
  trending X template", "roadmap for editing this reel", or points at an Edits template/effect to copy. Produces a
  reel-maker-shaped recipe (cuts, layout, captions, audio) from phone captures plus optional self-driven UI
  navigation. NOT for pulling media off the phone (phone-media-bridge), NOT for building the reel itself (reel-maker),
  NOT for posting.
---

# ig-edits-inspiration

The Edits app is inspiration and a roadmap ONLY. The actual video project is built on the PC through editor-pack.
Capture cheaply from the phone, extract a recipe, hand off to the sibling skills that do the real work.

Capture script: `scripts/capture.py` (run by absolute path). Recipe shape follows
[reel-maker SKILL.md](..\..\reel-maker\SKILL.md) Phase 2 (cut timestamps/pacing, layout/crop geometry, on-screen text
style, caption timing via stable-ts, audio choice). Tone-map guard context lives in
[video-tool-router SKILL.md](..\..\video-tool-router\SKILL.md).

## Doctrine (the user's requirement)

The Edits app exists to give inspiration and a roadmap on how exactly to edit — use it for that, then continue
building the video project on the PC through editor-pack, taking whatever agentic path is most optimal in speed and
tokens spent.

**(1) The agent MAY drive the phone UI itself once the phone is unlocked** (`adb shell input
tap/swipe/text/keyevent`, app launches via `monkey`). Read-only capture (screencap + uiautomator) stays the
preferred, token-cheap path — drive the UI only when capture alone can't reach the screen (login walls, deep
tabs, template previews needing a tap). The 2026-09-16 read-only restriction is SUPERSEDED as of 2026-09-20 by
explicit user instruction: if the phone is unlocked (see Phase 0), navigate anywhere and do anything needed to get
the recipe. Asking the user to navigate is now the fast-path fallback, not the rule.

**(2) The phone is never the editing engine — the recipe is executed on the PC by sibling skills (reel-maker +
clip-picker + video-tool-router).**

**(3) NEVER auto-post — finished reels are pushed to the phone and handed to the user to post.**

## When to use

Triggers: "inspiration from Edits", "what's trending on Edits", "make a reel like the trending X template",
"roadmap for editing this reel", or any request to copy an Edits template/effect/transition into a PC-built reel.

NOT-for: pulling media off the phone (that is phone-media-bridge), building/assembling the reel (that is reel-maker
once the recipe exists), choosing encoders or tone-mapping (that is video-tool-router), posting anything.

## Workflow

### Phase 0 — Device check + unlock

Run `adb devices -l` (via the resolved adb path — `--adb` flag, else `ADB` env var, else PATH, else the WinGet
platform-tools fallback). If the list is empty, STOP and print the checklist: same Wi-Fi + Wireless debugging ON
for wireless (re-pair/re-connect if needed), or data cable (not charge-only) / File Transfer mode / USB debugging /
RSA prompt for USB. Then unlock for autonomous navigation:

```powershell
C:\Python314\python.exe %USERPROFILE%\GitHub\editor-pack\skills\phone-media-bridge\scripts\unlock.py
```

(`VAULT_ENTRY_MISSING` → collect `phone-1:password` via the vault popup, then re-run. See
[phone-media-bridge SKILL.md](..\..\phone-media-bridge\SKILL.md) Phase 0b.) `capture.py`
enforces the device check itself and exits non-zero with the checklist when no device is present.

### Phase 1 — Capture (budget-capped: max 4 screenshots per session)

Either ask the user to navigate Edits to the screen of interest (fast path), or drive there yourself: launch with
`adb shell monkey -p com.instagram.basel -c android.intent.category.LAUNCHER 1` and navigate with
`adb shell input tap x y` / `swipe` / `keyevent`. Prefer the fewest taps that reach the screen. Then capture
read-only:

```powershell
C:\Python314\python.exe <skill>\scripts\capture.py --name trending --dir <project>
C:\Python314\python.exe <skill>\scripts\capture.py --name template_beatdrop --dir <project>
```

Each run writes `edits_<name>.png` (binary-safe `adb exec-out screencap -p`) plus `edits_<name>.xml` (`adb shell
uiautomator dump` + `adb pull`, best-effort), downscales oversized shots to max width 1080 as `.jpg` (quality 85),
and prints one JSON line with the paths. Prefer reading the XML text labels over re-reading screenshots — the XML
usually names the template/effect/tab without spending image tokens.

### Phase 2 — Recipe extraction

Read the captures and produce a RECIPE markdown following reel-maker's recipe shape: cut count/pacing, layout +
aspect, on-screen text/caption style + timing, audio/trend, effects observed (transitions, filters, speed ramps,
overlays). Explicitly mark unknowns as unknown instead of guessing — a template preview may not reveal exact cut
timestamps or the underlying audio track, and that is fine; reel-maker fills those from the reference download or
user decisions later.

### Phase 3 — Hand-off

Invoke the [reel-maker SKILL.md](..\..\reel-maker\SKILL.md) flow with the recipe plus the user footage folder (from
the phone-media-bridge junction). This skill's job ends at the recipe file; reel-maker owns the build.

## Token-economy rules

- uiautomator XML text first, screenshots second. If the XML answers the question, skip the image read entirely.
- Screenshots are pre-downscaled by `capture.py` (max width 1080, JPEG quality 85) — read them as-is, never ask for
  full-res re-captures.
- Never re-read the same capture twice in one session. Read once, take notes, move on.
- Hard cap: 4 captures per session. If 4 screens did not yield the recipe, stop and ask the user what to prioritize
  rather than capturing more.
- Self-navigation costs taps and screenshots: only drive the UI when the user hasn't already put the screen in
  front of you. A user-navigated screen is always cheaper than an agent-driven one.

## Output

Recipe file: `<project>\edits_recipe_<topic>_v1.md` (versioned, kept in the project dir). Report its path plus a
one-paragraph summary of the observed style (pacing, layout, text, audio) and the unknowns reel-maker must resolve.

## Gotchas

- Edits package is `com.instagram.basel` (verified on-device 2026-09-20 via `pm list packages` on vivo 1951).
- `uiautomator dump` can fail on secure/DRM screens — treat XML failure as non-fatal and work from the screenshot.
- Always use the `adb exec-out screencap -p > file.png` form (binary-safe stdout); never text-mode the PNG bytes.
- Windows consoles default to cp1252 — every script here reconfigures stdout to UTF-8 with `errors="replace"`.
- UI driving is for REACHING screens (launch, tab switches, opening a template), not for editing inside Edits —
  the edit itself always happens on the PC. Never type credentials anywhere except the lock-screen PIN via
  `unlock.py` (vault-sourced, never chat).
