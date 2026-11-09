#!/usr/bin/env python3
"""Read a Pinterest board (or a single pin) through the user's own signed-in Pinterest session. Read-only.

Uses the persistent Chrome profile of the vault entry (default pinterest-1) from the secure-vault skill, in one browser
launch: the sign-in cookie is checked first, and only when it is missing does pinterest_login.ensure_signed_in sign in
(the fastest known path: Google session -> Pinterest's real Google button -> FedCM account chooser via CDP).
The board is opened in that browser and the pin data Pinterest's own page loads (BoardFeedResource / PinResource
responses) is collected while scrolling to the end; Pinterest refuses the same data API when it is called directly (403),
so nothing is fetched behind the page's back. It never saves, pins, follows or clicks anything.

    python read_board.py <board or pin URL> [--out DIR] [--download] [--account pinterest-1]

Writes <out>/<board slug>/board.json (one entry per pin: id, url, title, description, link, image = original-size URL,
dominant colour) and, with --download, the original images as <out>/<slug>/<pin id>.<ext>. Default <out>: D:\\PinterestBoards.
"""
from __future__ import annotations

import argparse
import json
import os
import re

import sys
import time
import urllib.request
from pathlib import Path

VAULT_SKILL = Path(r"%USERPROFILE%\.agents\skills\secure-vault")
sys.path.insert(0, str(VAULT_SKILL))
import browser_session as bs  # noqa: E402
from pinterest_login import ensure_signed_in, signed_in  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def pin_record(p: dict) -> dict:
    imgs = p.get("images") or {}
    img = (imgs.get("orig") or imgs.get("736x") or imgs.get("474x") or {}).get("url")
    return {"id": p.get("id"), "url": f"https://www.pinterest.com/pin/{p.get('id')}/", "title": p.get("title") or p.get("grid_title") or "",
            "description": (p.get("description") or "").strip(), "link": p.get("link") or "", "image": img,
            "color": p.get("dominant_color"), "created_at": p.get("created_at")}


def read(url: str, account: str) -> tuple[dict, list[dict]]:
    pins: dict[str, dict] = {}
    board: dict = {}
    pin_id = (re.search(r"/pin/(\d+)", url) or [None, None])[1]

    def on_resp(r):
        if "/resource/" not in r.url:
            return
        name = r.url.split("/resource/")[1].split("/")[0]
        if name not in ("BoardFeedResource", "BoardResource", "PinResource", "BoardSectionPinsResource"):
            return
        try:
            data = (r.json().get("resource_response") or {}).get("data")
        except Exception:
            return
        if name == "BoardResource" and isinstance(data, dict):
            board.update({"name": data.get("name"), "id": data.get("id"), "pin_count": data.get("pin_count"),
                          "privacy": data.get("privacy"), "url": "https://www.pinterest.com" + (data.get("url") or "")})
        elif name == "PinResource" and isinstance(data, dict) and data.get("id"):
            pins[data["id"]] = pin_record(data)
        elif isinstance(data, list):
            for p in data:
                if isinstance(p, dict) and p.get("type") == "pin":
                    pins[p["id"]] = pin_record(p)

    t0 = time.time()
    lap = lambda what: os.environ.get("PB_TIMING") and print(f"  [{time.time() - t0:5.1f} s] {what}", flush=True)
    with bs.session(account) as page:
        lap("browser up")
        # one browser launch: sign in inside this session (cookie check first, no page load) only when needed
        if not ensure_signed_in(page, account):
            raise SystemExit(f"Pinterest sign-in for {account} did not complete; see {bs.profile_dir(account) / 'login_status.json'}")
        lap("signed in")
        page.on("response", on_resp)
        bs.visit(page, url)
        page.wait_for_selector('a[href*="/pin/"]', timeout=30000)
        page.wait_for_timeout(1500)
        lap("board page loaded")
        if not signed_in(page):   # a stale cookie: the site signed the profile out on this visit
            if not ensure_signed_in(page, account):
                raise SystemExit("the profile is not signed in to Pinterest")
            bs.visit(page, url)
            page.wait_for_selector('a[href*="/pin/"]', timeout=30000)
        # initial pins are server-rendered into the page's JSON: read them too
        try:
            raw = page.evaluate("() => { const e = document.getElementById('__PWS_INITIAL_PROPS__'); return e ? e.textContent : '' }")
            for m in re.finditer(r'"id":"(\d{10,20})","type":"pin"', raw or ""):
                pins.setdefault(m.group(1), {"id": m.group(1), "url": f"https://www.pinterest.com/pin/{m.group(1)}/"})
        except Exception:
            pass
        if not pin_id:
            # scroll until the board ends: Pinterest's "Find some ideas for this board:" follows the last pin (substring
            # match; the exact-text selector never matched because of the colon), or no new pins for 4 scrolls
            still, last = 0, -1
            while still < 4:
                page.mouse.wheel(0, 3500)
                page.wait_for_timeout(1000)
                n = len(pins) + len(page.query_selector_all('a[href*="/pin/"]'))
                still = still + 1 if n == last else 0
                last = n
                if page.query_selector("text=Find some ideas for this board") and still >= 1:
                    break
            lap("scrolled to the end")
        # pins seen only in the DOM (no JSON captured): keep their image from the grid
        for a in page.query_selector_all('a[href*="/pin/"]'):
            href = a.get_attribute("href") or ""
            m = re.search(r"/pin/(\d+)", href)
            if not m or (pin_id and m.group(1) != pin_id):
                continue
            if m.group(1) not in pins or not pins[m.group(1)].get("image"):
                img = a.query_selector("img")
                src = img.get_attribute("src") if img else None
                pins.setdefault(m.group(1), {"id": m.group(1), "url": f"https://www.pinterest.com/pin/{m.group(1)}/"})
                if src and not pins[m.group(1)].get("image"):
                    pins[m.group(1)]["image"] = re.sub(r"/\d+x/", "/originals/", src)
        board.setdefault("url", url)
        if not board.get("name") and not pin_id:
            h1 = page.query_selector("h1")
            board["name"] = h1.inner_text().strip() if h1 else None
    if pin_id:
        pins = {k: v for k, v in pins.items() if k == pin_id}
    return board, list(pins.values())


def download(pins: list[dict], folder: Path) -> int:
    n = 0
    for p in pins:
        if not p.get("image"):
            continue
        urls = [p["image"]] + ([p["image"].replace("/originals/", "/736x/")] if "/originals/" in p["image"] else [])
        for u in urls:
            ext = Path(u.split("?")[0]).suffix or ".jpg"
            dest = folder / f"{p['id']}{ext}"
            if dest.exists():
                n += 1
                break
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": "https://www.pinterest.com/"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    dest.write_bytes(r.read())
                n += 1
                break
            except Exception as exc:
                last = exc
        else:
            print(f"  image {p['id']}: {last}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--out", default=r"D:\PinterestBoards")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--account", default="pinterest-1")
    a = ap.parse_args()
    board, pins = read(a.url, a.account)
    m = re.search(r"/pin/(\d+)", a.url)
    slug = f"pin-{m.group(1)}" if m else [p for p in re.sub(r"^https?://[^/]+", "", a.url).split("?")[0].split("/") if p][1]
    folder = Path(a.out) / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "board.json").write_text(json.dumps({"board": board, "read_at": time.strftime("%Y-%m-%d %H:%M:%S"), "pins": pins},
                                                  indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{board.get('name') or slug}: {len(pins)} pins (board says {board.get('pin_count')}) -> {folder / 'board.json'}")
    if a.download:
        print(f"downloaded {download(pins, folder)} images to {folder}")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
