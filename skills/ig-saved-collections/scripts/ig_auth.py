"""Shared Instagram auth for the ig-saved-collections scripts.

Builds an authenticated instagrapi Client directly from IG_SESSIONID in the
skill's .env (or .env.<account> for a non-default account -- see
account_env_path()), WITHOUT calling login_by_sessionid() -- that method
follows its cookie setup with a noisy user-info fetch (users/{id}/info ->
info_stream -> public GraphQL) that Instagram soft-blocks after a few
attempts, killing the whole login even though the session cookie itself is
fine. We replicate only the part that matters (cookie + Authorization
header), which is set BEFORE that fetch in instagrapi's own
login_by_sessionid (mixins/auth.py).

Also deliberately does NOT persist/reload sessions via dump_settings /
load_settings: a settings roundtrip loses auth state (private API returns
login_required afterwards), so each process just rebuilds the header from the
sessionid -- which is free, involves no extra requests, and always works.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

SKILL_DIR = Path(__file__).resolve().parent.parent


def account_env_path(account: str | None) -> Path:
    """.env for the default account, .env.<account> for any other -- keeps
    multiple Instagram accounts' credentials/sessions from clobbering each
    other. Matches the naming documented in .env.example."""
    return SKILL_DIR / (f".env.{account}" if account else ".env")


def account_profile_dir(account: str | None) -> Path:
    """Separate Playwright browser profile per account, same reasoning."""
    return SKILL_DIR / (f".pw-profile.{account}" if account else ".pw-profile")


def client_from_sessionid(sessionid: str) -> Client:
    """Authenticated Client from a sessionid, zero extra API calls."""
    m = re.search(r"^\d+", sessionid)
    if not (isinstance(sessionid, str) and len(sessionid) > 30 and m):
        sys.exit("IG_SESSIONID doesn't look like a valid sessionid (should start with digits and be long).")
    user_id = m.group()
    cl = Client()
    cl.settings["cookies"] = {"sessionid": sessionid}
    cl.init()
    cl.authorization_data = {
        "ds_user_id": user_id,
        "sessionid": sessionid,
        "should_use_header_over_cookies": True,
    }
    cl.private.cookies.set("ds_user_id", user_id)
    cl.private.headers.update(cl.base_headers)
    cl.private.headers.update({"Authorization": cl.authorization})
    return cl


def get_client(account: str | None = None, allow_refresh: bool = True) -> Client:
    """Client from the account's .env IG_SESSIONID; on a dead session,
    auto-refresh it via refresh_session.py (Playwright browser login) when
    credentials are available, then retry once."""
    env_path = account_env_path(account)
    if not env_path.exists():
        sys.exit(
            f"{env_path} doesn't exist. Copy .env.example to {env_path.name} in this folder and fill it in "
            f"(see .env.example's header for how to set up a second account)."
        )
    load_dotenv(env_path, override=True)
    sessionid = os.environ.get("IG_SESSIONID")
    if not sessionid:
        if allow_refresh and _can_refresh():
            _refresh(account)
            return get_client(account=account, allow_refresh=False)
        sys.exit(f"IG_SESSIONID isn't set in {env_path.name}. Fill it in (or set IG_USERNAME/IG_PASSWORD for auto-login).")

    cl = client_from_sessionid(sessionid)
    try:
        # cheapest real check that the session works. NOTE: cl.collections()
        # can't be used here -- it swallows exceptions and returns [] on auth
        # failure, so hit the endpoint directly, which raises LoginRequired.
        cl.private_request(
            "collections/list/",
            params={"collection_types": '["ALL_MEDIA_AUTO_COLLECTION"]', "max_id": ""},
        )
        return cl
    except LoginRequired:
        if allow_refresh and _can_refresh():
            print(f"Session expired for {env_path.name} -- refreshing via browser login...")
            _refresh(account)
            return get_client(account=account, allow_refresh=False)
        sys.exit(
            "The sessionid has expired/been invalidated. Get a fresh one from the browser "
            f"(DevTools -> Application -> Cookies -> instagram.com -> sessionid) and update {env_path.name}, "
            "or set IG_USERNAME/IG_PASSWORD there to enable automatic refresh."
        )


def _can_refresh() -> bool:
    return bool(os.environ.get("IG_USERNAME") and os.environ.get("IG_PASSWORD"))


def _refresh(account: str | None) -> None:
    cmd = [sys.executable, str(Path(__file__).parent / "refresh_session.py")]
    if account:
        cmd += ["--account", account]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("Automatic session refresh failed -- see output above.")
    load_dotenv(account_env_path(account), override=True)
