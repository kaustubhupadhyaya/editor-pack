# YouTube Data API v3 — one-time OAuth setup (the "get the API" guide)

This is the official, sanctioned, **free** path to your own playlists (including private ones). No ban risk —
unlike Instagram, Google gives you a real API for this. Total time: ~10 minutes, once.

## Why you need OAuth (not just an API key)

- An **API key** alone can only read *public* playlists.
- Reading playlists with `mine=true` (yours, including **private**) requires **OAuth 2.0** — you grant the script
  read-only access to your own YouTube account, in your own browser, once.
- Scope used: `https://www.googleapis.com/auth/youtube.readonly` (read-only; the script can never modify anything).

## Setup steps (Google Cloud console, 2026 layout)

1. Go to **console.cloud.google.com** (sign in with the Google account that owns the YouTube channel).
2. Create a project (top bar → project picker → **New project** → any name, e.g. `yt-playlist-export`).
3. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
4. **Google Auth Platform** (the renamed "OAuth consent screen"):
   - User type: **External**.
   - Fill in the app name + your email (Branding section). Nothing else is required.
   - Under **Audience** (or **Test users** in the older UI): add your own Google account.
5. **Google Auth Platform → Clients → Create client**:
   - Application type: **Desktop app**. Name it anything.
   - **Download JSON** → save it as `client_secret.json` inside this skill's folder
     (`skills/yt-saved-playlists/` — it's gitignored, it will never be committed).
6. **Recommended — flip publishing status to "In production"** (Audience page → Publish app):
   - While the app stays in "Testing", Google kills your refresh token **every 7 days**.
   - "In production" without verification is fine for personal use — the only consequences are a scary
     "Google hasn't verified this app" interstitial (once) and a lifetime cap of 100 users (irrelevant; you're the
     only user).
   - **This is now optional, not required:** `yt_auth.get_service()` auto-regrants a dead token by driving the consent
     screen through the authenticated browser profile — no manual re-login. But in "Testing" that regrant fires weekly;
     publishing to Production makes the token permanent so it ~never fires. **The publish click itself cannot be
     automated** — there is no gcloud command, REST API, or Terraform resource for it (the internal API was shut down
     2026-03-19); it's a one-time ~30-second manual click. Do it once and forget it.
7. First run of any script here: a browser tab opens → pick your account → you'll see **"Google hasn't verified
   this app"** → click **Advanced → Go to <app> (unsafe)** → allow read-only YouTube access. The script saves a
   `token.json` next to `client_secret.json` and never asks again.

## Quota (you will never hit it)

Default: **10,000 units/day**. Every playlist-listing call costs **1 unit** per page of 50 results. Exporting a
library of 200 playlists averaging 100 videos costs ~400 units. `benchmark_captions.py`'s sampler adds `videos.list`
(1 unit per 50 ids) — negligible. Never use `search.list` (100 units/call) — the scripts here don't.

**Liked Videos IS reachable via the API** — `playlistItems.list(playlistId="LL")` pages it like any playlist (the
account's `channels.list → contentDetails.relatedPlaylists.likes` is literally `"LL"`). Only Watch Later and
playlists saved from other channels genuinely need the cookie path.

## What the API canNOT give you (and what fills the gap)

| Not available via API | Why | Gap-filler |
|---|---|---|
| **Watch Later** | Deliberately killed by Google in Sept 2016 — returns an empty list for everyone | yt-dlp + cookies (`:ytwatchlater`), or Google Takeout |
| **Watch History** | Same 2016 change | Google Takeout only |
| **Playlists saved from other channels** | No endpoint exists (feature request open since 2018) | yt-dlp + cookies on your channel's `/playlists` tab, or Google Takeout |

For the yt-dlp cookie path, see [cookbook.md](cookbook.md) — including the mandatory private-window cookie-export
ritual and the account-ban caveats that make us prefer the API for everything the API can do.

## Zero-risk alternative for a one-off full dump: Google Takeout

takeout.google.com → Deselect all → **YouTube and YouTube Music** → "All YouTube data included" → keep only
**playlists** → Export. Produces one CSV per playlist **including `Watch later.csv`** and private playlists.
No API, no cookies, no risk — but manual and slow (minutes to hours), so it's the fallback, not the pipeline.
