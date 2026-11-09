"""Autonomous auth spine for yt-saved-playlists.

Everything here follows the secure-vault's own doctrine: prefer a persistent,
already-authenticated browser profile (session reuse) over re-supplying a
password. A password is touched only when a session is genuinely dead, and it
is read IN-PROCESS from the DPAPI vault -- never printed, logged, or passed on
a command line.

Public API
----------
get_service()                 -> googleapiclient service; loads token.json,
                                 refreshes it, and (only if the refresh token
                                 is dead) auto-regrants by driving the OAuth
                                 consent screen through the authenticated
                                 google-5 profile -- no human step.
export_cookies_txt(dest=None) -> Path to a Netscape cookies.txt written from
                                 the profile's live cookies (via Playwright's
                                 context.cookies(), which returns DECRYPTED
                                 values -- this is how we sidestep Chrome's v20
                                 App-Bound Encryption entirely). Feeds yt-dlp
                                 --cookies for Watch Later / saved-from-others.
is_profile_authenticated()    -> bool; headless check of the profile session.
refresh_profile_session_interactive()
                              -> last-resort ONLY: opens a VISIBLE browser,
                                 prefills the vault email/password, and waits
                                 for the human to clear any 2FA. Rarely needed
                                 (the profile session persists for months).

Why the profile, not client-secret alone: the Data API token can die (a
Testing-mode app revokes refresh tokens every 7 days). When it does, the
consent screen must be clicked through again -- which we do autonomously by
reusing the profile's established Google login, not by logging in afresh.
"""
from __future__ import annotations

import http.server
import os
import socket
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SKILL_DIR = Path(__file__).resolve().parent.parent
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
CLIENT_SECRET = SKILL_DIR / "client_secret.json"
TOKEN = SKILL_DIR / "token.json"

# The authenticated automation profile for the channel-owning account.
# (secure-vault stores these under %USERPROFILE%\.secrets\profiles\<label>.)
PROFILE_LABEL = "google-5"
PROFILE_DIR = os.path.expandvars(rf"%USERPROFILE%\.secrets\profiles\{PROFILE_LABEL}")
VAULT_DIR = r"%USERPROFILE%\.agents\skills\secure-vault"

# Cookie NAMES (never values) that signal a live Google/YouTube session.
_AUTH_COOKIE_NAMES = {"SID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO", "SAPISID"}


# --------------------------------------------------------------- Data API auth

def _load_token_creds() -> Credentials | None:
    if not TOKEN.exists():
        return None
    return Credentials.from_authorized_user_file(str(TOKEN), SCOPES)


def _save(creds: Credentials) -> None:
    TOKEN.write_text(creds.to_json(), encoding="utf-8")


def get_service():
    """Return an authenticated youtube v3 service, self-healing a dead token."""
    creds = _load_token_creds()

    if creds and creds.valid:
        return build("youtube", "v3", credentials=creds)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds)
            return build("youtube", "v3", credentials=creds)
        except RefreshError:
            print("token.json refresh failed (refresh token likely revoked) -> auto-regrant via profile...")

    if not CLIENT_SECRET.exists():
        sys.exit(
            f"{CLIENT_SECRET} not found. Follow references/api-setup.md to create the OAuth client "
            "and download its JSON there."
        )

    creds = _regrant_via_profile()
    _save(creds)
    print("token.json rewritten by autonomous regrant.")
    return build("youtube", "v3", credentials=creds)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _regrant_via_profile(headless: bool = False) -> Credentials:
    """Run the installed-app OAuth flow but drive the consent screen through the
    already-authenticated profile instead of opening the system browser. No
    human interaction -- the automation clicks through -- but it must run HEADED:
    Google serves a headless browser a degraded 'Signed out' account chooser,
    while a headed one gets the real consent flow the profile session satisfies."""
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    port = _free_port()
    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )

    captured: dict[str, str] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            if "code" in params:
                captured["code"] = params["code"][0]
                self.wfile.write(b"Authorization complete. You can close this tab.")
            else:
                captured["error"] = params.get("error", ["unknown"])[0]
                self.wfile.write(b"Authorization failed.")

        def log_message(self, *a):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()

    try:
        _click_through_consent(auth_url, port, headless=headless)
    finally:
        t.join(timeout=5)
        server.server_close()

    if "code" not in captured:
        raise RuntimeError(
            f"OAuth regrant did not capture an authorization code "
            f"(error={captured.get('error', 'none')}). The consent UI may have changed, "
            "or the profile session expired -- run refresh_profile_session_interactive()."
        )
    flow.fetch_token(code=captured["code"])
    return flow.credentials


def _click_through_consent(auth_url: str, port: int, headless: bool = False) -> None:
    """Resilient, URL-aware click loop over Google's OAuth consent, reusing the
    signed-in profile. The flow (verified live) is:
      signin/oauth/delegation      -> click the account row ([data-identifier])
      signin/oauth/warning         -> "Continue" (the unverified-app notice)
      .../v2/consentsummary        -> "Continue"/"Allow" (grant the scope)
      -> redirect to our localhost, where the caller's server catches the code.
    Buttons are matched by ROLE + exact name (get_by_text is too greedy and was
    clicking stray 'Continue'/account text on the wrong page). Runs headed."""
    from playwright.sync_api import sync_playwright

    deadline = time.time() + 90
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=headless,
                                                   viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(auth_url, wait_until="domcontentloaded", timeout=45000)
            while time.time() < deadline:
                url = page.url or ""
                if f"localhost:{port}" in url:
                    break
                if "delegation" in url or "accountchooser" in url:
                    try:
                        tile = page.locator("[data-identifier]")
                        if tile.count() and tile.first.is_visible():
                            tile.first.click(timeout=2500)
                    except Exception:
                        pass
                else:
                    for label in ("Continue", "Allow", "Select all", "Advanced"):
                        try:
                            b = page.get_by_role("button", name=label, exact=True)
                            if b.count() and b.first.is_visible():
                                b.first.click(timeout=2500)
                                break
                        except Exception:
                            continue
                page.wait_for_timeout(1300)
        finally:
            ctx.close()


# --------------------------------------------------------- profile -> cookies

def _cookie_txt_line(c: dict) -> str | None:
    name = c.get("name")
    value = c.get("value")
    domain = c.get("domain", "")
    if not name or value is None or not domain:
        return None
    include_sub = "TRUE" if domain.startswith(".") else "FALSE"
    path = c.get("path", "/")
    secure = "TRUE" if c.get("secure") else "FALSE"
    expires = c.get("expires", -1)
    expiry = 0 if not expires or expires < 0 else int(expires)
    return "\t".join([domain, include_sub, path, secure, str(expiry), name, value])


def export_cookies_txt(dest: str | os.PathLike | None = None, headless: bool = True) -> Path:
    """Write a Netscape cookies.txt for yt-dlp from the profile's LIVE cookies.
    Playwright returns decrypted values, so Chrome's v20 App-Bound Encryption
    (which defeats yt-dlp's --cookies-from-browser) is never in the picture."""
    from playwright.sync_api import sync_playwright

    dest = Path(dest) if dest else (SKILL_DIR / "yt-cookies.txt")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=headless)
        try:
            cookies = ctx.cookies()
        finally:
            ctx.close()

    keep = [c for c in cookies if any(d in c.get("domain", "") for d in ("youtube.com", "google.com"))]
    lines = ["# Netscape HTTP Cookie File", "# Written by yt_auth.export_cookies_txt (profile session).", ""]
    lines += [ln for c in keep if (ln := _cookie_txt_line(c))]
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


# ---------------------------------------------------------- session liveness

def is_profile_authenticated(headless: bool = True) -> bool:
    """True if the profile still has a usable signed-in YouTube session.
    Reads cookie NAMES only; never returns or prints a value."""
    if not os.path.isdir(PROFILE_DIR):
        return False
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=headless)
        try:
            names = {c["name"] for c in ctx.cookies("https://www.youtube.com") if c.get("value")}
        finally:
            ctx.close()
    return bool(_AUTH_COOKIE_NAMES & names)


# ------------------------------------------------ last-resort interactive login

def refresh_profile_session_interactive(timeout_s: int = 600) -> bool:
    """Open a VISIBLE browser, prefill the vault email/password, and let the
    human clear any 2FA/challenge. Used ONLY when the profile session is dead
    (rare). The password is read in-process from the DPAPI vault and typed into
    Google's real page -- never printed, logged, or placed on argv. Returns True
    once a signed-in session is detected."""
    from playwright.sync_api import sync_playwright

    sys.path.insert(0, VAULT_DIR)
    import vault  # noqa: E402  (local, DPAPI-backed)

    email = vault.get_secret(PROFILE_LABEL, "email")
    password = vault.get_secret(PROFILE_LABEL, "password")
    if not email:
        print("No email in vault for", PROFILE_LABEL, "-- cannot prefill; sign in manually in the window.")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PROFILE_DIR, headless=False,
                                                    viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://accounts.google.com/ServiceLogin?continue=https://www.youtube.com/",
                  wait_until="domcontentloaded", timeout=45000)
        try:
            if email and page.locator('input[type="email"]').count() > 0:
                page.fill('input[type="email"]', email, timeout=8000)
                page.get_by_role("button", name="Next").first.click(timeout=8000)
                page.wait_for_timeout(2500)
            if password and page.locator('input[type="password"]').count() > 0:
                page.fill('input[type="password"]', password, timeout=8000)
                page.get_by_role("button", name="Next").first.click(timeout=8000)
        except Exception as e:
            print(f"(prefill raised {type(e).__name__}; complete the login in the window.)")

        print("Complete any 2FA / 'is this you' step in the browser window now.")
        deadline = time.time() + timeout_s
        ok = False
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            names = {c["name"] for c in ctx.cookies("https://www.youtube.com") if c.get("value")}
            if _AUTH_COOKIE_NAMES & names:
                ok = True
                break
        ctx.close()
    print("Profile session refreshed." if ok else "Timed out; session not established.")
    return ok


if __name__ == "__main__":
    # Manual smoke: `python yt_auth.py {service|cookies|check}`
    what = sys.argv[1] if len(sys.argv) > 1 else "check"
    if what == "check":
        print("profile_authenticated:", is_profile_authenticated())
    elif what == "service":
        svc = get_service()
        me = svc.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        print("service OK; channel:", me)
    elif what == "cookies":
        print("wrote:", export_cookies_txt())
    else:
        sys.exit("usage: python yt_auth.py {service|cookies|check}")
