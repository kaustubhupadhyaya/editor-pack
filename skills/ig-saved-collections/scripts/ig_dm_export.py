"""Export one Instagram DM thread, from its very first message, through the
account's saved ig-saved-collections session (no password login).

Pages backwards through direct_v2/threads/<id>/ until Instagram reports
has_older=false, checkpointing after every page so a throttle or crash can be
resumed by re-running the same command. Writes into --out:

  raw_pages.jsonl   every page exactly as received (checkpoint; newest first)
  messages.jsonl    one raw item per line, oldest first, de-duplicated
  transcript.txt    human-readable transcript, wrapped at 120 chars
  media/            photos, videos, voice notes and GIFs sent in the chat
  summary.json      counts, date span, pagination and integrity checks

Usage:
  python ig_dm_export.py --with <username> --out <dir>                        # main account
  python ig_dm_export.py --account example_account --thread <thread_id> --out <dir> --me example_account

The command supervises itself: the export runs in a child process whose
watchdog exits with code 75 when nothing has advanced for STALL_S seconds, and
the parent restarts it from the checkpoint (up to MAX_RESTARTS times).
"""
import argparse
import json
import os
import random
import subprocess
import sys
import textwrap
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, r"%USERPROFILE%\GitHub\editor-pack\skills\ig-saved-collections\scripts")
from ig_auth import get_client  # noqa: E402
from instagrapi.exceptions import ClientError, LoginRequired, ChallengeRequired  # noqa: E402

WRAP = 120
STALL_S = 300        # nothing advanced for this long -> the watchdog stops the child
WATCHDOG_EXIT = 75   # the supervising parent restarts on this code; the checkpoint makes it a resume
MAX_RESTARTS = 5


class Watchdog:
    """Exits the process when nothing has advanced for STALL_S seconds. The
    request timeout covers one stalled request; this covers any other hang
    (2026-10-19: an export sat 12 minutes on one page before anyone noticed)."""

    def __init__(self, stall_s=STALL_S):
        self.stall_s = stall_s
        self.last = time.monotonic()
        threading.Thread(target=self._watch, daemon=True).start()

    def beat(self):
        self.last = time.monotonic()

    def _watch(self):
        while True:
            time.sleep(10)
            idle = time.monotonic() - self.last
            if idle > self.stall_s:
                print(f"WATCHDOG: no progress for {idle:.0f}s; stopping so the parent resumes from the checkpoint",
                      flush=True)
                os._exit(WATCHDOG_EXIT)


watchdog = None


def beat():
    if watchdog:
        watchdog.beat()


def find_thread(cl, username, max_pages=30):
    """1:1 thread id with `username`, searching the inbox page by page."""
    params = {"visual_message_return_type": "unseen", "thread_message_limit": "1",
              "persistentBadging": "true", "limit": "20"}
    for _ in range(max_pages):
        inbox = cl.private_request("direct_v2/inbox/", params=params)["inbox"]
        beat()
        for t in inbox["threads"]:
            names = [u["username"] for u in t.get("users", [])]
            if not t.get("is_group") and names == [username]:
                return t["thread_id"]
        if not inbox.get("has_older"):
            break
        params["cursor"] = inbox["oldest_cursor"]
        time.sleep(random.uniform(1.0, 2.0))
    sys.exit(f"No 1:1 thread with {username} found in the inbox.")


def _with_default_timeout(session, timeout=(10, 60)):
    """instagrapi sends private API requests without a timeout, so one stalled
    connection hangs the export forever (2026-10-19: 12 minutes stuck on page 3)."""
    original = session.request

    def request(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original(method, url, **kwargs)

    session.request = request


def fetch_page(cl, thread_id, cursor, limit):
    params = {"visual_message_return_type": "unseen", "direction": "older", "limit": str(limit)}
    if cursor:
        params["cursor"] = cursor
    delay = 30
    for attempt in range(6):
        try:
            return cl.private_request(f"direct_v2/threads/{thread_id}/", params=params)["thread"]
        except (LoginRequired, ChallengeRequired):
            raise
        except (ClientError, requests.RequestException) as e:
            print(f"  page request failed ({type(e).__name__}: {e}); retry {attempt + 1}/6 in {delay}s", flush=True)
            beat()  # a deliberate wait, not a stall; the 120 s cap keeps it well under STALL_S
            time.sleep(delay)
            delay = min(delay * 2, 120)
    raise SystemExit("Giving up after 6 failed attempts; re-run the same command to resume from the checkpoint.")


def ts_of(item):
    return int(item["timestamp"]) / 1e6


def fmt_time(ts):
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")


def best_image(node):
    cands = (node.get("image_versions2") or {}).get("candidates") or []
    return max(cands, key=lambda c: c.get("width", 0) * c.get("height", 0))["url"] if cands else None


def best_video(node):
    vers = node.get("video_versions") or []
    return max(vers, key=lambda v: v.get("width", 0) * v.get("height", 0))["url"] if vers else None


def post_link(media):
    code = (media or {}).get("code")
    if not code:
        return None
    kind = "reel" if media.get("product_type") == "clips" else "p"
    return f"https://www.instagram.com/{kind}/{code}/"


def describe(item):
    """(text for the transcript, [(url, ext)] media to download)."""
    t = item.get("item_type")
    downloads = []
    if t == "text":
        return item.get("text", ""), downloads
    if t == "link":
        link = item.get("link", {})
        url = (link.get("link_context") or {}).get("link_url")
        return f"{link.get('text', '')}" + (f"  <{url}>" if url and url not in link.get("text", "") else ""), downloads
    if t == "like":
        return "\u2764\ufe0f", downloads
    if t == "media":
        m = item.get("media", {})
        if m.get("media_type") == 2 and best_video(m):
            downloads.append((best_video(m), "mp4"))
            return "[video]", downloads
        if best_image(m):
            downloads.append((best_image(m), "jpg"))
        return "[photo]", downloads
    if t in ("raven_media", "visual_media"):
        vm = item.get("visual_media") or item.get("raven_media") or {}
        m = vm.get("media", vm)
        if best_video(m):
            downloads.append((best_video(m), "mp4"))
        elif best_image(m):
            downloads.append((best_image(m), "jpg"))
        return "[disappearing photo/video" + ("" if downloads else ", no longer viewable") + "]", downloads
    if t == "voice_media":
        audio = ((item.get("voice_media") or {}).get("media") or {}).get("audio") or {}
        if audio.get("audio_src"):
            downloads.append((audio["audio_src"], "m4a"))
        dur = audio.get("duration")
        return f"[voice message{f' {dur / 1000:.0f}s' if dur else ''}]", downloads
    if t == "animated_media":
        img = ((item.get("animated_media") or {}).get("images") or {}).get("fixed_height") or {}
        url = img.get("webp") or img.get("url")
        if url:
            downloads.append((url, "webp" if img.get("webp") else "gif"))
        return "[GIF/sticker]", downloads
    if t == "media_share":
        m = item.get("media_share", {})
        owner = (m.get("user") or {}).get("username", "?")
        return f"[shared post by @{owner}: {post_link(m) or 'unavailable'}]" + _caption(m), downloads
    if t == "clip":
        m = (item.get("clip") or {}).get("clip", {})
        owner = (m.get("user") or {}).get("username", "?")
        return f"[shared reel by @{owner}: {post_link(m) or 'unavailable'}]" + _caption(m), downloads
    if t == "felix_share":
        m = (item.get("felix_share") or {}).get("video", {})
        return f"[shared video: {post_link(m) or 'unavailable'}]", downloads
    if t == "story_share":
        s = item.get("story_share", {})
        if s.get("media"):
            owner = (s["media"].get("user") or {}).get("username", "?")
            return f"[shared story by @{owner}]", downloads
        return f"[shared story: {s.get('message') or s.get('title') or 'unavailable'}]", downloads
    if t == "reel_share":
        r = item.get("reel_share", {})
        kind = {"reply": "replied to story", "reaction": "reacted to story", "mention": "mentioned in story"}.get(
            r.get("type"), f"story {r.get('type')}")
        owner = ((r.get("media") or {}).get("user") or {}).get("username")
        return f"[{kind}{f' by @{owner}' if owner else ''}] {r.get('text', '')}".rstrip(), downloads
    if t == "action_log":
        return f"[{(item.get('action_log') or {}).get('description', 'action')}]", downloads
    if t == "placeholder":
        p = item.get("placeholder", {})
        return f"[{p.get('title', '')} {p.get('message', '')}]".replace("[ ", "["), downloads
    if t == "video_call_event":
        return f"[{(item.get('video_call_event') or {}).get('description', 'call')}]", downloads
    if t == "profile":
        return f"[shared profile @{(item.get('profile') or {}).get('username', '?')}]", downloads
    if t == "location":
        return f"[shared location {(item.get('location') or {}).get('name', '')}]", downloads
    if t == "hashtag":
        return f"[shared hashtag #{(item.get('hashtag') or {}).get('name', '')}]", downloads
    if t and t.startswith("xma") or t == "generic_xma":
        xs = item.get(t) or []
        x = xs[0] if isinstance(xs, list) and xs else (xs if isinstance(xs, dict) else {})
        bits = [x.get("header_title_text"), x.get("title_text"), x.get("target_url")]
        return f"[{t}: " + " | ".join(b for b in bits if b) + "]", downloads
    return f"[{t}]", downloads


def _caption(m):
    cap = ((m.get("caption") or {}).get("text") or "").strip().replace("\n", " ")
    return f' "{cap[:150]}{"..." if len(cap) > 150 else ""}"' if cap else ""


def write_json_atomic(path, obj):
    """A run stopped mid-write must never leave a half-written checkpoint."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, path)


def trim_partial_line(path):
    """Drop a half-written last page left by a stopped run; that page is fetched again."""
    if not path.exists():
        return
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        path.write_bytes(data[:data.rfind(b"\n") + 1])
        print("trimmed a half-written page from the last run; fetching it again", flush=True)


def download(url, dest):
    for attempt in range(3):
        beat()
        try:
            r = requests.get(url, timeout=(10, 120))
            if r.status_code == 200 and r.content:
                dest.write_bytes(r.content)
                return True
            if r.status_code in (403, 404, 410):
                return False
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    return False


def main():
    global watchdog
    watchdog = Watchdog()  # started first so even the login check is covered
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=None, help="other ig-saved-collections account; omit for the main account")
    ap.add_argument("--me", default="kaustubh__upadhyaya", help="your username, shown on your own messages")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--with", dest="username")
    g.add_argument("--thread")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=20, help="messages per page request")
    ap.add_argument("--no-media", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    state_path, pages_path = out / "state.json", out / "raw_pages.jsonl"

    cl = get_client(account=args.account, allow_refresh=False)
    _with_default_timeout(cl.private)
    me_id = str(cl.authorization_data["ds_user_id"])
    thread_id = args.thread or find_thread(cl, args.username)

    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get("thread_id") not in (None, thread_id):
        sys.exit(f"{out} already holds a different thread ({state['thread_id']}).")
    if state.get("done"):
        print("Pagination already complete; rebuilding outputs only.")
    else:
        cursor, pages = state.get("cursor"), state.get("pages", 0)
        if pages:
            print(f"Resuming after page {pages}.")
        trim_partial_line(pages_path)
        seen_cursors = set(state.get("seen_cursors", []))
        while True:
            thread = fetch_page(cl, thread_id, cursor, args.limit)
            pages += 1
            meta = {k: thread.get(k) for k in ("thread_id", "thread_title", "is_group", "viewer_id")}
            meta["users"] = [{"pk": str(u["pk"]), "username": u["username"], "full_name": u.get("full_name")}
                             for u in thread.get("users", [])]
            with pages_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"page": pages, "cursor_in": cursor, "has_older": thread.get("has_older"),
                                    "oldest_cursor": thread.get("oldest_cursor"), "meta": meta,
                                    "items": thread.get("items", [])}, ensure_ascii=False) + "\n")
            items = thread.get("items", [])
            oldest = fmt_time(ts_of(items[-1])) if items else "-"
            print(f"page {pages}: {len(items)} items, oldest {oldest}, has_older={thread.get('has_older')}", flush=True)
            nxt = thread.get("oldest_cursor")
            done = not thread.get("has_older") or not nxt
            if not done and nxt in seen_cursors:
                print("  cursor repeated; stopping to avoid a loop")
                done = True
            seen_cursors.add(nxt)
            cursor = nxt
            state = {"thread_id": thread_id, "cursor": cursor, "pages": pages, "done": done,
                     "final_has_older": thread.get("has_older"), "seen_cursors": sorted(c for c in seen_cursors if c)}
            write_json_atomic(state_path, state)
            beat()
            if done:
                break
            time.sleep(random.uniform(1.0, 2.5))

    # ---- rebuild outputs from the checkpoint ----
    pages_raw = [json.loads(line) for line in pages_path.open(encoding="utf-8")]
    meta = pages_raw[0]["meta"]
    names = {u["pk"]: u["username"] for u in meta["users"]}
    names[me_id] = args.me
    by_id, dupes = {}, 0
    for p in pages_raw:
        for it in p["items"]:
            if it["item_id"] in by_id:
                dupes += 1
            by_id[it["item_id"]] = it
    items = sorted(by_id.values(), key=ts_of)

    with (out / "messages.jsonl").open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    media_dir = out / "media"
    media_ok = media_fail = media_reported = 0
    lines = [f"Instagram DM: {args.me} <->{', '.join(u['username'] for u in meta['users'])}",
             f"Thread {thread_id}; {len(items)} messages; exported {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}",
             "=" * 60]
    for it in items:
        text, dl = describe(it)
        sender = names.get(str(it.get("user_id")), str(it.get("user_id")))
        saved = []
        if dl and not args.no_media:
            media_dir.mkdir(exist_ok=True)
            stamp = datetime.fromtimestamp(ts_of(it)).strftime("%Y%m%d_%H%M%S")
            for n, (url, ext) in enumerate(dl):
                dest = media_dir / f"{stamp}_{it['item_id']}{f'_{n}' if n else ''}.{ext}"
                if dest.exists() or download(url, dest):
                    media_ok += 1
                    saved.append(f"media/{dest.name}")
                else:
                    media_fail += 1
            if media_ok + media_fail - media_reported >= 25:
                media_reported = media_ok + media_fail
                print(f"media: {media_ok} saved, {media_fail} failed", flush=True)
        if saved:
            text += " (" + ", ".join(saved) + ")"
        rep = it.get("replied_to_message")
        if rep:
            rtext, _ = describe(rep)
            text = f"(replying to: {rtext[:80]}) " + text
        reacts = ((it.get("reactions") or {}).get("emojis") or [])
        if reacts:
            text += "  {" + " ".join(f"{r.get('emoji')} {names.get(str(r.get('sender_id')), '?')}" for r in reacts) + "}"
        head = f"[{fmt_time(ts_of(it))}] {sender}: "
        wrapped = textwrap.wrap(text, width=WRAP, initial_indent=head, subsequent_indent=" " * 4,
                                replace_whitespace=False, drop_whitespace=True) or [head]
        lines.extend(wrapped)
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    ts = [ts_of(i) for i in items]
    summary = {
        "account": args.me, "thread_id": thread_id, "participants": meta["users"],
        "messages": len(items), "pages": len(pages_raw), "duplicates_across_pages": dupes,
        "first_message": fmt_time(ts[0]) if ts else None, "last_message": fmt_time(ts[-1]) if ts else None,
        "final_page_has_older": pages_raw[-1]["has_older"], "reached_beginning": pages_raw[-1]["has_older"] is False,
        "item_types": Counter(i.get("item_type") for i in items).most_common(),
        "by_sender": Counter(names.get(str(i.get("user_id")), "?") for i in items).most_common(),
        "media_saved": media_ok, "media_failed": media_fail,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=1, ensure_ascii=False))


def supervise():
    """Run the export in a child process and restart it from the checkpoint
    whenever its watchdog stops it, so a hang never waits for a human."""
    env = dict(os.environ, IG_DM_EXPORT_CHILD="1")
    for restart in range(MAX_RESTARTS + 1):
        rc = subprocess.run([sys.executable, "-u", __file__, *sys.argv[1:]], env=env).returncode
        if rc != WATCHDOG_EXIT:
            return rc
        if restart < MAX_RESTARTS:
            print(f"restart {restart + 1}/{MAX_RESTARTS}: resuming from the checkpoint", flush=True)
    print(f"watchdog stopped the export {MAX_RESTARTS + 1} times; giving up", flush=True)
    return WATCHDOG_EXIT


if __name__ == "__main__":
    if os.environ.get("IG_DM_EXPORT_CHILD"):
        main()
    else:
        sys.exit(supervise())
