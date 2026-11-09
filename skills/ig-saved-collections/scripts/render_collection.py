"""Turn the collector's listing, the downloaded media and reel_content.py's results into one folder per saved
collection: a README.md a human reads and an index.jsonl an agent reads. Reads local files only (no network).

Reads under <root>:
  _state/collections.json, _state/pages/*.jsonl, _state/membership.json  (saved_collector.py)
  _state/media.jsonl, _media/                                             (saved_media.py)
  _items/<code>/[slide_NN/]content.json + transcript.txt, frames/, onscreen_text.txt   (reel_content.py)
Writes, for every named collection and for "_Not in any collection":
  <Collection>/README.md            numbered items: @owner, link, date, mode, caption, transcript, on-screen text and
                                    the screenshots inline (wrapped at 120 characters)
  <Collection>/index.jsonl          one JSON object per item, the same facts in machine form
  <Collection>/items/<NNN>_<code>/  info.json plus copies of the item's transcript, frames, on-screen text and photos
                                    (D: is exFAT: no links, so the small files are copied; videos stay in _media/)
  <root>/README.md                  one line per collection with its counts
Safe to rerun at any time: it rewrites these files from the current state and never deletes anything. An older
index.jsonl in another format (extract_saved.py's) is kept as index.<date>.jsonl.
"""
import argparse
import json
import re
import shutil
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\EditorPackArchive\ig-saved-collections\saved_export")
UNSORTED = "_Not in any collection"
WRAP = 120
FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.endswith("\n") and line.strip()]


def feed_items(state_dir, key):
    """Items of one listed feed in Instagram's order (newest saved first), each code once."""
    seen, out = set(), []
    for page in read_jsonl(state_dir / "pages" / f"{key}.jsonl"):
        for m in page["items"]:
            if m.get("code") and m["code"] not in seen:
                seen.add(m["code"])
                out.append(m)
    return out


def folder_names(collections):
    """Windows-safe, unique folder names. Names that clash ignoring case (For her / For her, extroversion /
    Extroversion) all get the end of their collection id."""
    def clean(name):
        s = re.sub(r"\s+", " ", FORBIDDEN.sub("_", name or "untitled")).strip().rstrip(". ")
        return s or "untitled"
    groups = defaultdict(list)
    for c in collections:
        groups[clean(c["name"]).lower()].append(c)
    names = {}
    for members in groups.values():
        for c in members:
            names[c["id"]] = clean(c["name"]) + (f" - {c['id'][-7:]}" if len(members) > 1 else "")
    return names


def ist(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else None


def kind_of(m):
    t = m.get("media_type")
    if t == 8:
        return "carousel"
    if t == 2:
        return "reel" if m.get("product_type") == "clips" else "video"
    return "photo"


def audio_of(m):
    cm = m.get("clips_metadata") or {}
    music = ((cm.get("music_info") or {}).get("music_asset_info")) or {}
    if music.get("title"):
        return {"title": music.get("title"), "artist": music.get("display_artist"), "original": False}
    orig = cm.get("original_sound_info") or {}
    if orig:
        return {"title": orig.get("original_audio_title"),
                "artist": (orig.get("ig_artist") or {}).get("username"), "original": True}
    return None


def mmss(seconds):
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}" if seconds else ""


def block(text):
    """A fenced block keeps the author's line breaks and the 120-character wrap in both raw and preview."""
    lines = []
    for para in (text or "").splitlines() or [""]:
        lines += textwrap.wrap(para, WRAP, break_long_words=False, break_on_hyphens=False) or [""]
    return "```text\n" + "\n".join(lines).strip("\n") + "\n```\n"


def load_media(state_dir):
    final = {}
    for r in read_jsonl(state_dir / "media.jsonl"):
        final[r["stem"]] = r
    return final


def item_parts(m, media):
    """(stem, slide) for every downloadable part of a saved item, as saved_media.py names them."""
    code = m["code"]
    if m.get("media_type") == 8 and m.get("carousel_media"):
        return [(f"{code}_{i:02d}", f"slide_{i:02d}") for i in range(1, len(m["carousel_media"]) + 1)]
    return [(code, None)]


def copy_part(src_dir, dst_dir):
    """Copy reel_content.py's small outputs for one part. Returns the content.json data (or None)."""
    cj = src_dir / "content.json"
    if not cj.exists():
        return None
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in ("content.json", "transcript.txt", "onscreen_text.txt"):
        if (src_dir / name).exists():
            shutil.copy2(src_dir / name, dst_dir / name)
    if (src_dir / "frames").is_dir():
        (dst_dir / "frames").mkdir(exist_ok=True)
        for f in (src_dir / "frames").iterdir():
            if not (dst_dir / "frames" / f.name).exists():
                shutil.copy2(f, dst_dir / "frames" / f.name)
    return json.loads(cj.read_text(encoding="utf-8"))


def build_item(n, m, col_dir, root, media, names_by_id, member_names):
    code = m["code"]
    item_dir = col_dir / "items" / f"{n:03d}_{code}"
    rel_item = f"items/{n:03d}_{code}"
    parts, content, missing, pending = [], [], 0, 0
    for stem, slide in item_parts(m, media):
        row = media.get(stem)
        status = row["status"] if row else "not downloaded yet"
        src = root / "_items" / code / slide if slide else root / "_items" / code
        dst = item_dir / slide if slide else item_dir
        rel = f"{rel_item}/{slide}" if slide else rel_item
        file_rel = None
        if row and row["status"] == "ok":
            mf = root / "_media" / row["file"]
            if row["kind"] == "image":
                dst.mkdir(parents=True, exist_ok=True)
                if not (dst / row["file"]).exists():
                    shutil.copy2(mf, dst / row["file"])
                file_rel = f"{rel}/{row['file']}"
            else:
                file_rel = f"../_media/{row['file']}"
        else:
            missing += 1
        parts.append({"slide": slide, "kind": (row or {}).get("kind"), "file": file_rel, "status": status})
        c = copy_part(src, dst) if row and row["status"] == "ok" else None
        if row and row["status"] == "ok" and not c:
            pending += 1
        if c:
            content.append({
                "slide": slide, "mode": c.get("mode"), "language": c.get("language"),
                "transcript": c.get("transcript"), "transcript_unreliable": c.get("transcript_unreliable"),
                "onscreen_text": c.get("onscreen_text"),
                "frames": [f"{rel}/{f['file']}" for f in c.get("frames") or []],
                "frame_times": [f.get("t") for f in c.get("frames") or []],
            })
    cap = (m.get("caption") or {}).get("text") if isinstance(m.get("caption"), dict) else None
    rec = {
        "n": n, "code": code, "pk": str(m.get("pk")),
        "permalink": f"https://www.instagram.com/{'reel' if m.get('product_type') == 'clips' else 'p'}/{code}/",
        "owner": (m.get("user") or {}).get("username"), "owner_name": (m.get("user") or {}).get("full_name"),
        "posted": ist(m.get("taken_at")), "type": kind_of(m), "duration_s": m.get("video_duration"),
        "likes": m.get("like_count"), "comments": m.get("comment_count"),
        "plays": m.get("play_count") or m.get("ig_play_count") or m.get("view_count"),
        "audio": audio_of(m), "caption": cap, "collections": member_names,
        "media": parts, "content": content,
        "status": "media missing" if missing else ("content pending" if pending else "done"),
    }
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / "info.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def render_item(rec):
    modes = ", ".join(sorted({f"{c['mode']}" + (f" ({c['language']})" if c.get("language") else "")
                              for c in rec["content"] if c.get("mode")})) or rec["status"]
    head = f"### {rec['n']:03d} · @{rec['owner']} · {rec['type']}"
    if rec.get("duration_s"):
        head += f" {mmss(rec['duration_s'])}"
    head += f" · {modes}"
    meta = [rec["permalink"], f"posted {(rec['posted'] or '')[:10]}"]
    if rec.get("audio") and rec["audio"].get("title"):
        a = rec["audio"]
        meta.append(f"audio: {a['title']}" + (f" — {a['artist']}" if a.get("artist") else "")
                    + (" (original)" if a.get("original") else ""))
    others = [c for c in rec["collections"]]
    if len(others) > 1:
        meta.append("in: " + ", ".join(others))
    out = [head, "", " · ".join(meta), ""]
    if rec.get("caption"):
        out += ["**Caption**", "", block(rec["caption"])]
    for c in rec["content"]:
        label = f" — {c['slide'].replace('_', ' ')}" if c.get("slide") else ""
        if c.get("transcript"):
            out += [f"**Transcript{label}**" + (" (unreliable)" if c.get("transcript_unreliable") else ""), "",
                    block(c["transcript"])]
        if c.get("onscreen_text"):
            out += [f"**On-screen text{label}**", "", block(c["onscreen_text"])]
        if c.get("frames"):
            out += [" ".join(f"![{mmss(t) or '0:00'}]({f.replace(' ', '%20')})"
                             for f, t in zip(c["frames"], c["frame_times"])), ""]
    for p in rec["media"]:
        if p["kind"] == "image" and p.get("file"):
            out += [f"![photo]({p['file'].replace(' ', '%20')})", ""]
        elif p["kind"] == "video" and p.get("file"):
            out += [f"[video file]({p['file'].replace(' ', '%20')})", ""]
        elif p["status"] != "ok":
            out += [f"_media {p['status']}_", ""]
    return "\n".join(out) + "\n"


def keep_legacy_index(col_dir):
    idx = col_dir / "index.jsonl"
    if not idx.exists():
        return
    first = idx.open(encoding="utf-8").readline()
    try:
        if "n" in json.loads(first) and "content" in json.loads(first):
            return
    except ValueError:
        pass
    stamp = datetime.fromtimestamp(idx.stat().st_mtime).strftime("%Y-%m-%d")
    dest, k = col_dir / f"index.{stamp}.jsonl", 2
    while dest.exists():
        dest, k = col_dir / f"index.{stamp}-{k}.jsonl", k + 1
    idx.rename(dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", default=None)
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    root = Path(args.root) if args.root else (ROOT / args.account if args.account else ROOT)
    state_dir = root / "_state"
    cols = json.loads((state_dir / "collections.json").read_text(encoding="utf-8"))["collections"]
    named = [c for c in cols if c["type"] == "MEDIA"]
    names = folder_names(named)
    by_id = {c["id"]: c for c in named}
    media = load_media(state_dir)
    membership = json.loads((state_dir / "membership.json").read_text(encoding="utf-8")) \
        if (state_dir / "membership.json").exists() else {"source": "named_walks"}

    all_items = feed_items(state_dir, "all")
    members = defaultdict(list)  # collection id -> items in Instagram's order
    if membership["source"] == "saved_collection_ids":
        for m in all_items:
            for cid in m.get("saved_collection_ids") or []:
                if str(cid) in by_id:
                    members[str(cid)].append(m)
    else:
        for c in named:
            members[c["id"]] = feed_items(state_dir, c["id"])
    placed = {m["code"] for items in members.values() for m in items}
    unsorted = [m for m in all_items if m["code"] not in placed]
    names_of_code = defaultdict(list)
    for cid, items in members.items():
        for m in items:
            names_of_code[m["code"]].append(by_id[cid]["name"])

    summary = []
    targets = [(names[c["id"]], c, members.get(c["id"], [])) for c in named] + [(UNSORTED, None, unsorted)]
    for folder, col, items in targets:
        col_dir = root / folder
        col_dir.mkdir(parents=True, exist_ok=True)
        keep_legacy_index(col_dir)
        recs = [build_item(n, m, col_dir, root, media, names, names_of_code.get(m["code"], []))
                for n, m in enumerate(items, 1)]
        with (col_dir / "index.jsonl").open("w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        modes = defaultdict(int)
        for r in recs:
            for c in r["content"]:
                modes[c["mode"]] += 1
        status = defaultdict(int)
        for r in recs:
            status[r["status"]] += 1
        title = col["name"] if col else "Not in any collection (saved, but in no named collection)"
        ig_count = col["media_count"] if col else None
        intro = (f"# Instagram saved — {title}\n\n"
                 + textwrap.fill(
                     f"{len(recs)} items served by Instagram"
                     + (f" (Instagram's own count: {ig_count}; the difference is posts deleted or made private "
                        f"after saving)" if ig_count is not None else "")
                     + f". Content modes: {dict(sorted(modes.items())) or 'none yet'}. Item status: "
                     + f"{dict(sorted(status.items())) or 'none'}. Rendered {datetime.now():%Y-%m-%d %H:%M} IST.",
                     WRAP, break_long_words=False)
                 + "\n\n---\n\n")
        (col_dir / "README.md").write_text(intro + "\n".join(render_item(r) for r in recs), encoding="utf-8")
        summary.append((folder, ig_count, len(recs), modes, status))

    lines = [f"# Instagram saved collections — {root.name}", "",
             f"Rendered {datetime.now():%Y-%m-%d %H:%M} IST from saved_collector.py's listing "
             f"(membership from {membership['source']}).", "",
             "| Collection | Instagram's count | Items | Transcripts | Screenshot sets | Status |", "|---|---|---|---|---|---|"]
    for folder, ig_count, n, modes, status in summary:
        transcripts = sum(v for k, v in modes.items() if k in ("speech", "singing", "mixed", "unsure"))
        visual = sum(v for k, v in modes.items() if k != "speech")
        lines.append(f"| [{folder}]({folder.replace(' ', '%20')}/README.md) | {ig_count if ig_count is not None else '—'}"
                     f" | {n} | {transcripts} | {visual} | {dict(sorted(status.items()))} |")
    (root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Rendered {len(summary)} folders under {root}")


if __name__ == "__main__":
    main()
