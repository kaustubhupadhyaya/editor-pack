"""List the authenticated user's OWN YouTube playlists (public/unlisted/PRIVATE)
plus the Liked Videos playlist id, via the official YouTube Data API v3.

Usage:
    python list_playlists.py

First run: opens a browser for the one-time OAuth grant (read-only scope) --
see references/api-setup.md for the 10-minute Google Cloud setup that produces
client_secret.json. A token.json is cached next to it afterwards (both are
gitignored; credentials never leave this machine).

What this CANNOT list (Google-imposed, not a bug here):
  - Watch Later (API returns empty since Sept 2016) -> cookbook.md, yt-dlp path
  - Playlists saved from OTHER channels (no API endpoint) -> same
"""
import json

# Auth (token load/refresh, and autonomous regrant if the refresh token dies)
# lives in yt_auth so every script in this skill shares one self-healing path.
from yt_auth import get_service


def main():
    yt = get_service()

    playlists, tok = [], None
    while True:
        r = yt.playlists().list(
            part="snippet,status,contentDetails", mine=True, maxResults=50, pageToken=tok
        ).execute()
        playlists += r.get("items", [])
        tok = r.get("nextPageToken")
        if not tok:
            break

    ch = yt.channels().list(part="contentDetails,snippet", mine=True).execute()
    channel = ch["items"][0]
    liked_id = channel["contentDetails"]["relatedPlaylists"].get("likes")
    handle = channel["snippet"]["title"]

    print(f"Playlists created by {handle} (via YouTube Data API):\n")
    for p in playlists:
        title = p["snippet"]["title"]
        privacy = p["status"]["privacyStatus"]
        count = p["contentDetails"]["itemCount"]
        print(f"  {title!r:45} id={p['id']}  [{privacy}]  {count} videos")
    if liked_id:
        print(f"\n  'Liked Videos'                    id={liked_id}  [system playlist]")

    print("\nNOT listable via the API (Google limitation): Watch Later, and playlists saved from other")
    print("channels. See references/cookbook.md for the yt-dlp cookie path that covers those.")

    out = [
        {
            "id": p["id"],
            "title": p["snippet"]["title"],
            "privacy": p["status"]["privacyStatus"],
            "item_count": p["contentDetails"]["itemCount"],
        }
        for p in playlists
    ]
    if liked_id:
        out.append({"id": liked_id, "title": "Liked Videos", "privacy": "system", "item_count": None})
    with open("playlists.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {len(out)} playlists to playlists.json")


if __name__ == "__main__":
    main()
