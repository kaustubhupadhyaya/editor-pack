---
name: pinterest-boards
description: >-
  Read the user's own Pinterest boards and pins (including secret boards) through their signed-in Pinterest session:
  every pin's id, title, description, source link and original-size image, saved as board.json plus the images. Use
  whenever a task needs a Pinterest board or pin URL the user gives (in.pinterest.com / pinterest.com), e.g. outfit or
  pose inspiration boards used as facecard goals, or when a logged-out fetch of a Pinterest link shows only a login
  wall. Read-only: never saves, pins, follows, comments or clicks anything on the account.
---

# pinterest-boards

## When to use

- The user gives a Pinterest board or pin link and a task needs its pictures or text: "can you access this board", "use
  the pins on my X board as goals", "download this pin".
- Pinterest shows logged-out visitors only a login wall for many boards (the user's `motorcycle-outfit-men` board
  redirected to "Explore the best of Pinterest"). Reading therefore goes through the user's own session.
- It does not post, save, organise or delete anything on Pinterest.

## How sign-in works (the secure-vault system, fastest known path)

Measured 2026-09-30 on the user's account:

| Case | Time |
|---|---|
| Saved session, whole board read | 10-11 s (3 s browser, instant cookie check, 4-5 s page, 2 s scroll) |
| Signed out of Pinterest and Google, automatic re-sign-in | 18 s, no phone prompt |

Steps, in order, with no trial between routes:
1. `_auth` cookie in the profile = 1: signed in, nothing loaded.
2. The Google session in the profile (`myaccount.google.com`). If missing: `accounts.google.com`, where Google shows
   "Choose an account" with the remembered, signed-out account. Pick it, wait for the visible password box, type the
   `google-1` password from the vault in-process.
3. `pinterest.com/login`: click the real Google button inside the zero-size `accounts.google.com` GSI iframe.
4. Chrome's native FedCM account chooser: select the account through CDP `FedCm.selectAccount`. Pinterest lands signed
   in.

The reader does all of this inside its own single browser launch (`pinterest_login.ensure_signed_in`).

### Details

- **Credentials:** vault entry `pinterest-1`: email + password, entered by the user in the vault popup, never in chat.
- **Session:** persistent Chrome profile `%USERPROFILE%\.secrets\profiles\pinterest-1\`. Profile-first: when it is
  signed in (Pinterest's `_auth` cookie = 1), nothing is typed.
- **When the session is new or expired:** `%USERPROFILE%\.agents\skills\secure-vault\pinterest_login.py` signs in.
  - The user's account is Google-connected: Pinterest answers a password with "Your account is connected to Google".
  - So it signs the profile in to Google with the matching vault Google entry (`google-1`, matched by email in-process),
    then chooses the account in Chrome's native FedCM dialog through CDP.
  - A phone prompt, captcha or code is never bypassed: a topmost notice asks the user to clear it in the Chrome window.
- A second Pinterest account would be `pinterest-2`: add it with
  `python %USERPROFILE%\.agents\skills\secure-vault\vault.py gui --request "pinterest-2:account"` and pass
  `--account pinterest-2`.

## Steps

1. `python %USERPROFILE%\GitHub\editor-pack\skills\pinterest-boards\scripts\read_board.py "<board or pin URL>" --download`
   - It signs in if needed, opens the board in the signed-in browser, scrolls to the end and collects the pin data the
     page itself loads. Pinterest's data API refuses direct calls (403), so nothing is fetched behind the page.
   - Output: `D:\PinterestBoards\<board slug>\board.json`, plus `<pin id>.jpg/png` originals with `--download`.
     `--out` changes the folder.
2. Open `board.json`, or the images with the Read tool, and hand them to the task (e.g. a facecard goal:
   `facecard.py gen --goal D:\PinterestBoards\<slug>\<pin id>.jpg ...`).

## Verified

2026-09-30, live: board `kaustubh0787/motorcycle-outfit-men` read as the user, 9 of 9 pins with original images (up to
1428x1424), second run reused the saved session with no login.
