"""Refresh IG_SESSIONID for one account by logging into Instagram through a
real (Playwright Chromium) browser with that account's IG_USERNAME/IG_PASSWORD.

Usage:
    python refresh_session.py                    # default account (.env)
    python refresh_session.py --account example_account # named account (.env.example_account)

Normally you never run this directly -- ig_auth.get_client() invokes it
automatically whenever the stored sessionid stops working. It exists so you
don't have to hand-copy the sessionid out of DevTools every time it expires.

Flow: open instagram.com/accounts/login in a VISIBLE browser window, fill the
credentials, submit, then wait for a `sessionid` cookie to appear and write it
into that account's env file (only the IG_SESSIONID line is touched).

Why the window is always visible, never headless: Instagram puts a live
reCAPTCHA/checkpoint challenge in front of logins from a device it doesn't
recognize -- which a first run always is. There is no way to satisfy that
challenge without a human, and this script deliberately does not attempt to
(no fingerprint spoofing, no CAPTCHA-solving service -- that would be
adversarial evasion of Instagram's own anti-bot control, not something this
script does). So: the window opens, you complete whatever Instagram asks for
(a checkbox, a 2FA code, a "was this you" tap) once, and the script detects
the resulting sessionid cookie and finishes on its own from there.

Each account gets its OWN persistent browser profile
(.pw-profile.<account>/, or .pw-profile/ for the default account) -- reusing
one profile across accounts would mean the browser is already logged into
account A and silently hands back account A's session when asked to refresh
account B. Once a given account's profile has been through a real login,
Instagram generally recognizes that device on later runs for that account and
skips the challenge -- this becomes a rare interruption, not a per-run one.

Credentials come from the secure-vault entry "instagram-main" (or
"instagram-<account>"; fields email = the Instagram username, password), read
in-process and never printed, and only then from the env file. The vault entry
exists for the case where the 2026-10 account unlock changed the password: the
user types the new one into the vault popup, and the stale IG_PASSWORD line is
removed from the env file so an old password is never typed into Instagram.
"""
import argparse
import os
import re
import sys
import time

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from ig_auth import account_env_path, account_profile_dir

LOGIN_URL = "https://www.instagram.com/accounts/login/"
WAIT_S = 600  # time to complete any human step (CAPTCHA/2FA) in the opened window
VAULT_DIR = r"%USERPROFILE%\.agents\skills\secure-vault"


def vault_credentials(account: str | None) -> tuple[str | None, str | None]:
    """(username, password) from the DPAPI secure-vault, or (None, None) when it has no entry for this account."""
    try:
        sys.path.insert(0, VAULT_DIR)
        import vault
    except ImportError:
        return None, None
    label = f"instagram-{account}" if account else "instagram-main"
    return vault.get_secret(label, "email"), vault.get_secret(label, "password")


def signed_in(page, context) -> bool:
    """A sessionid cookie alone is not enough: after the account lock the profile can keep a dead one, and then
    Instagram still shows the login form."""
    try:
        on_login = "/accounts/login" in page.url or page.locator('input[name="pass"], input[name="password"]').count() > 0
    except Exception:  # the page is mid-navigation; ask again on the next poll
        return False
    return bool(get_sessionid(context)) and not on_login


def get_sessionid(context) -> str | None:
    for c in context.cookies("https://www.instagram.com"):
        if c["name"] == "sessionid" and c["value"]:
            return c["value"]
    return None


def write_env_sessionid(env_path, sessionid: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    for i, ln in enumerate(lines):
        if re.match(r"^\s*IG_SESSIONID\s*=", ln):
            lines[i] = f"IG_SESSIONID={sessionid}"
            break
    else:
        lines.append(f"IG_SESSIONID={sessionid}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default=None, help="Named account (uses .env.<account> and .pw-profile.<account>/). Omit for the default account (.env).")
    args = ap.parse_args()

    env_path = account_env_path(args.account)
    profile_dir = account_profile_dir(args.account)
    if not env_path.exists():
        sys.exit(f"{env_path} doesn't exist. Copy .env.example to {env_path.name} and fill it in first.")

    load_dotenv(env_path, override=True)
    username, password = vault_credentials(args.account)
    source = "the secure vault"
    if not (username and password):
        username, password = os.environ.get("IG_USERNAME"), os.environ.get("IG_PASSWORD")
        source = env_path.name
    if not (username and password):
        sys.exit(f"No Instagram username/password in the secure vault or {env_path.name} -- can't auto-refresh.")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir), headless=False, viewport={"width": 1280, "height": 900}
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        sid = get_sessionid(context)
        if signed_in(page, context):
            print("Already logged in from the saved browser profile.")
            context.close()
            write_env_sessionid(env_path, sid)
            print(f"IG_SESSIONID refreshed in {env_path} (value not shown).")
            return

        try:
            # Instagram's login form fields were named "email"/"pass" (not
            # "username"/"password" despite the placeholder text) on 2026-08-29;
            # both spellings are tried. The submit control is a plain
            # input[type=submit] labeled "Log in", not a <button>.
            print(f"Signing in with the credentials from {source} (values not shown).")
            page.fill('input[name="email"], input[name="username"]', username, timeout=15000)
            page.fill('input[name="pass"], input[name="password"]', password)
            page.get_by_text("Log in", exact=True).click(timeout=10000)
            print("Submitted login form.")
        except Exception as e:
            print(f"(form fill/submit raised {e!r} -- Instagram may have shown something unexpected; check the window.)")

        print("If Instagram is asking for a CAPTCHA, 2FA code, or confirmation in the window, complete it now.")
        print(f"Waiting up to {WAIT_S // 60} minutes for login to finish...")
        deadline = time.time() + WAIT_S
        sid = None
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            if signed_in(page, context):
                sid = get_sessionid(context)
                break

        context.close()

    if not sid:
        sys.exit("Timed out waiting for login to complete. Re-run and finish the browser steps before the timeout.")

    write_env_sessionid(env_path, sid)
    print(f"IG_SESSIONID refreshed in {env_path} (value not shown).")


if __name__ == "__main__":
    main()
