"""List the logged-in Instagram user's saved collections (name + id + count).

Usage:
    python list_collections.py
    python list_collections.py --account example_account

Auth: reads IG_SESSIONID from .env (or .env.<account> for a named account --
see .env.example). If the session has expired and IG_USERNAME/IG_PASSWORD are
also set there, a fresh sessionid is fetched automatically via a real browser
login (refresh_session.py) -- no manual DevTools copying needed. Credentials
never leave this machine.
"""
import argparse
import json

from ig_auth import get_client


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--account", default=None, help="Named account (uses .env.<account>). Omit for the default account (.env).")
    ap.add_argument("--username", help="Instagram username, only used to label output; not required.")
    args = ap.parse_args()

    cl = get_client(account=args.account)
    username = args.username or cl.username or args.account or f"user {cl.user_id}"

    collections = cl.collections()
    if not collections:
        print("No saved collections found.")
        return

    print(f"Saved collections for @{username}:\n")
    for c in collections:
        count = getattr(c, "media_count", None)
        count_str = f"{count} items" if count is not None else "? items"
        tag = "  <- this is your WHOLE saved section" if c.type == "ALL_MEDIA_AUTO_COLLECTION" else ""
        print(f"  {c.name!r:30} id={c.id:<20} {count_str}{tag}")

    out = [{"name": c.name, "id": c.id, "type": c.type, "media_count": getattr(c, "media_count", None)} for c in collections]
    out_name = f"collections.{args.account}.json" if args.account else "collections.json"
    with open(out_name, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {len(out)} collections to {out_name}")


if __name__ == "__main__":
    main()
