"""List every saved item of one Instagram account from inside that account's own signed-in browser.

Why the browser and not instagrapi: on 2026-10-19 the main account was locked after a run that, following a scripted
login, replayed the browser's session cookie from instagrapi's fake Android phone (a fresh device id on every process
start) for ~1,000 requests in a few hours. This script sends the same JSON requests Instagram's own website sends,
from inside the skill's persistent browser profile that holds the session, so the cookie, the device and the network
fingerprint are the ones Instagram already knows.

Stages, each resumable from <root>/_state/:
  collections  GET /api/v1/collections/list/              -> _state/collections.json (+ the request estimate)
  pilot        one small collection (default "Business")  -> _state/pages/<id>.jsonl, _state/pilot.json
  all          GET /api/v1/feed/saved/posts/ (All posts)  -> _state/pages/all.jsonl, then _state/membership.json
  named        GET /api/v1/feed/collection/<id>/posts/    -> _state/pages/<id>.jsonl, skipped when membership.json
               shows that All-posts items carry saved_collection_ids (checked against the pilot collection)
  auto         collections -> pilot -> all -> named, as far as the daily cap allows

Rules that keep the account safe (memory note ig-saved-collections-instagram-extractor):
  - at most --daily-cap requests per IST day (default 150), counted across runs in _state/requests.jsonl;
  - one request at a time, a random 6-12 s gap between them;
  - stop at once with exit code 76, never retried and never followed by another login: any non-200 status (401, 403,
    429, ...), a redirect to /accounts/login/ or /challenge/, a body that is not JSON, or JSON naming
    checkpoint_required / challenge_required / feedback_required / login_required / spam. The reason is written to
    _state/STOPPED.json, and later runs refuse to start until --clear-stop is passed on the user's word;
  - a network error or the 30 s request timeout retries at most twice, 60 s apart;
  - signed out at the start: refresh_session.py signs the same profile in once (the user's choice, 2026-10-22).
    Still signed out after that: exit 76.

Usage (main account; add --account <name> for another one):
  python saved_collector.py --stage collections
  python saved_collector.py --stage pilot [--pilot-collection "Business"]
  python saved_collector.py --stage auto

The command supervises itself like ig_dm_export.py: a watchdog exits with code 75 after 5 minutes without progress
and the parent restarts it from the checkpoint (at most 3 times). Every restart reopens the SAME browser profile.
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import psutil

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ig_auth import account_profile_dir  # noqa: E402  (path helper only; nothing here talks to instagrapi)
from ig_dm_export import Watchdog, trim_partial_line, write_json_atomic  # noqa: E402

ROOT = Path(r"D:\EditorPackArchive\ig-saved-collections\saved_export")
SITE = "https://www.instagram.com"
STOP_EXIT = 76
WATCHDOG_EXIT = 75
MAX_RESTARTS = 3
PAGE_SIZE = 50
STOP_MESSAGES = {"checkpoint_required", "challenge_required", "feedback_required", "login_required"}

# The website's own request shape (as gallery-dl's web client sends it): same-origin fetch, the browser adds the
# cookies, Sec-Fetch-* headers and its real user agent. X-IG-WWW-Claim is the value the site keeps in sessionStorage.
FETCH_JS = """
async ([path, timeoutMs]) => {
  const csrf = (document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]+)/) || [])[1] || "";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(path, {
      method: "GET", credentials: "include", signal: ctrl.signal,
      headers: {
        "Accept": "*/*",
        "X-CSRFToken": csrf,
        "X-IG-App-ID": "936619743392459",
        "X-ASBD-ID": "129477",
        "X-IG-WWW-Claim": sessionStorage.getItem("www-claim-v2") || "0",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    const claim = r.headers.get("x-ig-set-www-claim");
    if (claim) sessionStorage.setItem("www-claim-v2", claim);
    return {status: r.status, url: r.url, redirected: r.redirected, text: await r.text()};
  } catch (e) {
    return {status: 0, url: path, redirected: false, text: "", error: String(e)};
  } finally {
    clearTimeout(timer);
  }
}
"""

watchdog = None


class StopRun(Exception):
    """Instagram pushed back (or the session is gone): stop everything, retry nothing."""


class CapReached(Exception):
    pass


def beat():
    if watchdog:
        watchdog.beat()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def now_ist():
    return datetime.now().isoformat(timespec="seconds")


def pause(lo=6.0, hi=12.0):
    """The gap between two requests, kept alive for the watchdog."""
    end = time.monotonic() + random.uniform(lo, hi)
    while time.monotonic() < end:
        beat()
        time.sleep(min(5.0, max(0.0, end - time.monotonic())))


class Budget:
    """Requests per IST day, counted across runs and restarts from _state/requests.jsonl."""

    def __init__(self, path, cap):
        self.path, self.cap = path, cap
        self.day, self.used = None, 0
        self._recount()

    def _recount(self):
        self.day = datetime.now().strftime("%Y-%m-%d")
        rows = [json.loads(line) for line in self.path.open(encoding="utf-8")] if self.path.exists() else []
        self.used = sum(1 for r in rows if r["t"].startswith(self.day))

    def check(self):
        if datetime.now().strftime("%Y-%m-%d") != self.day:
            self._recount()
        if self.used >= self.cap:
            raise CapReached(f"daily cap reached: {self.used} of {self.cap} requests on {self.day} (IST)")

    def record(self, what, path, status, items=None, note=None):
        row = {"t": now_ist(), "what": what, "path": path.split("?")[0], "status": status}
        if items is not None:
            row["items"] = items
        if note:
            row["note"] = note
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.used += 1


def stop_reason(res, body):
    url = res.get("url") or ""
    if any(p in url for p in ("/accounts/login", "/challenge", "/accounts/suspended")):
        return f"redirected to {url.split('?')[0]}"
    if res.get("status") != 200:
        return f"HTTP {res.get('status')}"
    if body is None:
        return "the response is not JSON"
    msg = str(body.get("message") or "")
    if msg in STOP_MESSAGES or body.get("spam") or body.get("status") == "fail":
        return f"Instagram answered {msg or body.get('status') or 'spam'}"
    return None


class Session:
    def __init__(self, page, budget, state_dir):
        self.page, self.budget, self.state_dir = page, budget, state_dir

    def get(self, what, path):
        """One API request. Returns the JSON body, or raises StopRun/CapReached."""
        for attempt in range(3):
            self.budget.check()
            res = self.page.evaluate(FETCH_JS, [path, 30_000])
            beat()
            status = res.get("status", 0)
            if status == 0:
                self.budget.record(what, path, 0, note=(res.get("error") or "")[:200])
                if attempt < 2:
                    log(f"  network error ({res.get('error')}); retry {attempt + 1}/2 in 60 s")
                    pause(60, 60)
                    continue
                raise SystemExit("Network failed 3 times in a row; nothing was changed. Re-run later to resume.")
            try:
                body = json.loads(res.get("text") or "")
            except ValueError:
                body = None
            reason = stop_reason(res, body)
            n = len(body.get("items") or []) if isinstance(body, dict) else None
            self.budget.record(what, path, status, items=n, note=reason)
            if reason:
                write_json_atomic(self.state_dir / "STOPPED.json", {
                    "t": now_ist(), "reason": reason, "what": what, "path": path.split("?")[0], "status": status,
                    "url": (res.get("url") or "").split("?")[0], "body_start": (res.get("text") or "")[:500]})
                raise StopRun(reason)
            return body
        raise AssertionError("unreachable")


def walk_feed(sess, state, state_path, key, label, path):
    """Page through one saved feed to its end, checkpointing every page."""
    st = state["feeds"].setdefault(key, {"label": label, "cursor": "", "pages": 0, "items": 0, "done": False})
    if st["done"]:
        log(f"{label}: already complete ({st['items']} items in {st['pages']} pages)")
        return st
    pages_path = sess.state_dir / "pages" / f"{key}.jsonl"
    pages_path.parent.mkdir(exist_ok=True)
    trim_partial_line(pages_path)
    seen = set(st.get("seen_cursors", []))
    while True:
        q = {"count": str(PAGE_SIZE)}
        if st["cursor"]:
            q["max_id"] = st["cursor"]
        body = sess.get(key, f"{path}?{urlencode(q)}")
        items = [it.get("media", it) for it in body.get("items") or []]
        st["pages"] += 1
        nxt = body.get("next_max_id") if body.get("more_available") else None
        with pages_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"page": st["pages"], "t": now_ist(), "cursor_in": st["cursor"],
                                "more_available": body.get("more_available"), "next_max_id": nxt,
                                "items": items}, ensure_ascii=False) + "\n")
        done = not nxt
        if nxt and nxt in seen:
            log(f"  {label}: cursor repeated; treating the feed as complete")
            done, st["cursor_loop"] = True, True
        seen.add(nxt)
        st.update(cursor=nxt or "", done=done, items=st["items"] + len(items),
                  seen_cursors=sorted(c for c in seen if c))
        write_json_atomic(state_path, state)
        beat()
        log(f"{label}: page {st['pages']} -> {len(items)} items (total {st['items']}){'' if done else ', more'}"
            f" | requests today {sess.budget.used}/{sess.budget.cap}")
        if done:
            return st
        pause()


def load_collections(state_dir):
    p = state_dir / "collections.json"
    return json.loads(p.read_text(encoding="utf-8"))["collections"] if p.exists() else None


def stage_collections(sess, refresh=False):
    cols = load_collections(sess.state_dir)
    if cols and not refresh:
        return cols
    raw, max_id = [], ""
    while True:
        q = {"collection_types": '["ALL_MEDIA_AUTO_COLLECTION","MEDIA"]', "max_id": max_id}
        body = sess.get("collections", f"/api/v1/collections/list/?{urlencode(q)}")
        raw += body.get("items") or []
        if not body.get("more_available") or not body.get("next_max_id"):
            break
        max_id = body["next_max_id"]
        pause()
    cols = [{"id": str(c.get("collection_id")), "name": c.get("collection_name"), "type": c.get("collection_type"),
             "media_count": c.get("collection_media_count")} for c in raw]
    write_json_atomic(sess.state_dir / "collections.json", {"fetched_at": now_ist(), "collections": cols, "raw": raw})
    print_estimate(cols, sess.budget)
    return cols


def print_estimate(cols, budget):
    allc = next((c for c in cols if c["type"] == "ALL_MEDIA_AUTO_COLLECTION"), None)
    named = [c for c in cols if c["type"] == "MEDIA"]
    all_pages = math.ceil(((allc or {}).get("media_count") or 0) / PAGE_SIZE)
    named_pages = sum(max(1, math.ceil((c["media_count"] or 0) / PAGE_SIZE)) for c in named)
    log(f"{len(named)} named collections; All posts reports {(allc or {}).get('media_count')} items.")
    log(f"Request estimate at {PAGE_SIZE} items per page: All posts {all_pages} pages + named collections "
        f"{named_pages} pages (skipped if saved_collection_ids is verified). Used today: {budget.used}/{budget.cap}.")


def pick_pilot(cols, name):
    named = [c for c in cols if c["type"] == "MEDIA"]
    exact = [c for c in named if (c["name"] or "").strip().lower() == name.strip().lower()]
    if exact:
        return exact[0]
    small = sorted((c for c in named if (c["media_count"] or 0) >= 5), key=lambda c: c["media_count"])
    if not small:
        raise SystemExit("No named collection with at least 5 items to pilot on.")
    return small[0]


def stage_pilot(sess, state, state_path, cols, name):
    col = pick_pilot(cols, name)
    st = walk_feed(sess, state, state_path, col["id"], col["name"], f"/api/v1/feed/collection/{col['id']}/posts/")
    pages = [json.loads(line) for line in (sess.state_dir / "pages" / f"{col['id']}.jsonl").open(encoding="utf-8")]
    items = [m for p in pages for m in p["items"]]
    videos = [m for m in items if m.get("media_type") == 2]
    report = {
        "t": now_ist(), "collection": col, "pages": len(pages), "page_sizes": [len(p["items"]) for p in pages],
        "items": len(items), "instagram_count": col["media_count"],
        "media_types": {str(t): sum(1 for m in items if m.get("media_type") == t) for t in (1, 2, 8)},
        "with_saved_collection_ids": sum(1 for m in items if "saved_collection_ids" in m),
        "videos_with_video_versions": sum(1 for m in videos if m.get("video_versions")),
        "videos_with_dash_manifest": sum(1 for m in videos if m.get("video_dash_manifest")),
        "first_item_keys": sorted(items[0].keys()) if items else [],
    }
    write_json_atomic(sess.state_dir / "pilot.json", report)
    log(f"PILOT {col['name']!r}: {report['items']} of {col['media_count']} items in {report['pages']} page(s) "
        f"{report['page_sizes']}; types {report['media_types']}; saved_collection_ids on "
        f"{report['with_saved_collection_ids']}; video_versions on {report['videos_with_video_versions']}/{len(videos)}")
    return st


def check_membership(state_dir):
    """Trust All-posts saved_collection_ids only if every pilot item carries the pilot collection's id there."""
    pilot = json.loads((state_dir / "pilot.json").read_text(encoding="utf-8"))
    pid = pilot["collection"]["id"]
    pilot_codes = {m["code"] for line in (state_dir / "pages" / f"{pid}.jsonl").open(encoding="utf-8")
                   for m in json.loads(line)["items"]}
    in_all = {}
    for line in (state_dir / "pages" / "all.jsonl").open(encoding="utf-8"):
        for m in json.loads(line)["items"]:
            in_all[m["code"]] = [str(x) for x in (m.get("saved_collection_ids") or [])]
    matched = sum(1 for c in pilot_codes if pid in in_all.get(c, []))
    ok = bool(pilot_codes) and matched == len(pilot_codes)
    result = {"t": now_ist(), "source": "saved_collection_ids" if ok else "named_walks", "pilot_collection": pid,
              "pilot_items": len(pilot_codes), "matched_in_all_posts": matched}
    write_json_atomic(state_dir / "membership.json", result)
    log(f"membership: {matched}/{len(pilot_codes)} pilot items carry the pilot id in All posts -> {result['source']}")
    return ok


def kill_profile_browsers(profile_dir):
    """A watchdog exit leaves Chromium running on the profile, which then refuses a second launch. Matches the exact
    --user-data-dir, because ".pw-profile" is also the start of the other accounts' ".pw-profile.<account>"."""
    target = os.path.normcase(str(Path(profile_dir).resolve()))
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if (p.info["name"] or "").lower() != "chrome.exe":
                continue
            dirs = [a.split("=", 1)[1].strip('"') for a in p.info["cmdline"] or [] if a.startswith("--user-data-dir=")]
            if any(os.path.normcase(str(Path(d).resolve())) == target for d in dirs):
                p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def open_browser(pw, profile_dir):
    ctx = pw.chromium.launch_persistent_context(str(profile_dir), headless=False,
                                                viewport={"width": 1280, "height": 900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(60_000)
    return ctx, page


def signed_in(page, budget):
    budget.check()
    resp = page.goto(SITE + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    budget.record("page:home", "/", resp.status if resp else 0, note=page.url.split("?")[0])
    beat()
    if any(p in page.url for p in ("/challenge", "/accounts/suspended")):
        raise StopRun(f"Instagram shows {page.url.split('?')[0]}")
    cookie = any(c["name"] == "sessionid" and c["value"] for c in page.context.cookies(SITE))
    form = page.locator('input[name="pass"], input[name="password"]').count() > 0
    return cookie and not form and "/accounts/login" not in page.url


def scripted_sign_in(account, budget):
    """refresh_session.py fills the stored credentials into the same profile (the user's choice, 2026-10-22). It may
    wait up to 10 minutes for a human to clear a challenge, so the watchdog is kept alive meanwhile."""
    budget.check()
    cmd = [sys.executable, str(HERE / "refresh_session.py")] + (["--account", account] if account else [])
    proc = subprocess.Popen(cmd)
    deadline = time.monotonic() + 900
    while proc.poll() is None and time.monotonic() < deadline:
        beat()
        time.sleep(5)
    if proc.poll() is None:
        proc.kill()
    budget.record("login", "/accounts/login/", proc.returncode, note="refresh_session.py")
    return proc.returncode == 0


def main():
    global watchdog
    watchdog = Watchdog()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default=None, help="another ig-saved-collections account; omit for the main one")
    ap.add_argument("--root", default=None, help=f"archive root (default {ROOT}, or <ROOT>/<account>)")
    ap.add_argument("--stage", choices=["collections", "pilot", "all", "named", "auto"], required=True)
    ap.add_argument("--pilot-collection", default="Business")
    ap.add_argument("--daily-cap", type=int, default=150)
    ap.add_argument("--refresh-collections", action="store_true", help="list the collections again")
    ap.add_argument("--force-named", action="store_true", help="walk the named collections even when not needed")
    ap.add_argument("--no-login", action="store_true", help="stop instead of running refresh_session.py")
    ap.add_argument("--clear-stop", action="store_true", help="remove STOPPED.json (only on the user's word)")
    args = ap.parse_args()

    root = Path(args.root) if args.root else (ROOT / args.account if args.account else ROOT)
    state_dir = root / "_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    stopped = state_dir / "STOPPED.json"
    if stopped.exists():
        if not args.clear_stop:
            sys.exit(f"A previous run was stopped by Instagram ({json.loads(stopped.read_text())['reason']}). "
                     f"Nothing runs until the user says so; then pass --clear-stop.")
        stopped.rename(state_dir / f"STOPPED.{datetime.now():%Y%m%d-%H%M%S}.json")
    state_path = state_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"feeds": {}}
    budget = Budget(state_dir / "requests.jsonl", args.daily_cap)
    (state_dir / "collector.pid").write_text(str(os.getpid()))
    profile_dir = account_profile_dir(args.account)
    kill_profile_browsers(profile_dir)

    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            ctx, page = open_browser(pw, profile_dir)
            if not signed_in(page, budget):
                ctx.close()
                if args.no_login:
                    raise StopRun("the browser profile is signed out")
                log("Signed out: refresh_session.py signs this profile in once.")
                if not scripted_sign_in(args.account, budget):
                    raise StopRun("the scripted sign-in failed")
                ctx, page = open_browser(pw, profile_dir)
                if not signed_in(page, budget):
                    raise StopRun("still signed out after one sign-in")
            log(f"Signed in. Requests used today: {budget.used}/{budget.cap}.")
            sess = Session(page, budget, state_dir)

            cols = stage_collections(sess, refresh=args.refresh_collections or args.stage == "collections")
            if args.stage in ("pilot", "auto"):
                pause()
                stage_pilot(sess, state, state_path, cols, args.pilot_collection)
            if args.stage in ("all", "auto"):
                pause()
                walk_feed(sess, state, state_path, "all", "All posts", "/api/v1/feed/saved/posts/")
            if args.stage in ("named", "auto"):
                trusted = (state_dir / "pilot.json").exists() and state["feeds"].get("all", {}).get("done") \
                    and check_membership(state_dir)
                if trusted and not args.force_named:
                    log("Named collections skipped: All-posts items already say which collections they are in.")
                else:
                    for c in sorted((c for c in cols if c["type"] == "MEDIA"), key=lambda c: c["media_count"] or 0):
                        pause()
                        walk_feed(sess, state, state_path, c["id"], c["name"],
                                  f"/api/v1/feed/collection/{c['id']}/posts/")
            if args.stage == "auto":
                state["finished"] = now_ist()
                write_json_atomic(state_path, state)
            log(f"DONE: stage {args.stage}. Requests used today: {budget.used}/{budget.cap}.")
            ctx.close()
    except CapReached as e:
        log(f"PAUSED: {e}. Run the same command tomorrow to resume.")
    except StopRun as e:
        log(f"STOP: {e}. Nothing is retried; see {stopped}.")
        sys.exit(STOP_EXIT)
    finally:
        (state_dir / "collector.pid").unlink(missing_ok=True)


def supervise():
    """Run the collector in a child process and restart it from the checkpoint when its watchdog stops it."""
    env = dict(os.environ, SAVED_COLLECTOR_CHILD="1")
    for restart in range(MAX_RESTARTS + 1):
        rc = subprocess.run([sys.executable, "-u", __file__, *sys.argv[1:]], env=env).returncode
        if rc != WATCHDOG_EXIT:
            return rc
        if restart < MAX_RESTARTS:
            print(f"restart {restart + 1}/{MAX_RESTARTS}: resuming from the checkpoint", flush=True)
    print(f"watchdog stopped the collector {MAX_RESTARTS + 1} times; giving up", flush=True)
    return WATCHDOG_EXIT


if __name__ == "__main__":
    if os.environ.get("SAVED_COLLECTOR_CHILD"):
        main()
    else:
        sys.exit(supervise())
