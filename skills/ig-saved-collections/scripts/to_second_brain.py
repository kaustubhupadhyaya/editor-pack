"""Publish extracted saved collections as text notes in the user's Second Brain vault (Google Drive).

One note per collection goes to 30_Resources/instagram/ (where the vault's routing table puts reference material,
and where the 2026-08-29 "Better" note already lives). The note is written by the vault's own second-brain tool,
`brain.py file --update`, which sets the frontmatter (title/tags/created/updated/summary, filed_by: agent) and keeps
the folder's INDEX.md current; a rerun refreshes the same note and keeps its created date. Notes carry text only:
caption, spoken transcript and on-screen text. Screenshots stay on D: (the note names the folder holding them).

Reads the index.jsonl in each collection folder: the render_collection.py format (fields n/code/content/...) or the
older extract_saved.py format (shortcode/spoken_transcript/...).

Usage:
  python to_second_brain.py --all                         # every rendered collection folder of the main account
  python to_second_brain.py --folder "Gym bro"            # one folder
  python to_second_brain.py --collection "Better" --account example_account   # an extract_saved.py collection
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import textwrap
from datetime import date
from pathlib import Path

ROOT = Path(r"D:\EditorPackArchive\ig-saved-collections\saved_export")
VAULT = Path(r"<second-brain>")
NOTE_FOLDER = "30_Resources/instagram"
BRAIN = Path(r"%USERPROFILE%\.agents\skills\second-brain\scripts\brain.py")
LINE_WIDTH = 120  # the note is read by a human -- see [[wrapped-text-output-preference]]


def wrap_field(text: str | None, width: int = LINE_WIDTH, indent: str = "  ") -> str:
    """Word-wrap to `width` characters, keeping the author's own line breaks."""
    if not text:
        return ""
    out = []
    for para in text.splitlines() or [text]:
        out += textwrap.wrap(para, width=max(20, width - len(indent)), break_long_words=False,
                             break_on_hyphens=False) or [""]
    return "\n".join(indent + ln if ln else "" for ln in out)


def slugify(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def render_new(r: dict, folder_path: Path) -> str:
    modes = ", ".join(sorted({c["mode"] + (f" ({c['language']})" if c.get("language") else "")
                              for c in r["content"] if c.get("mode")})) or r["status"]
    audio = r.get("audio") or {}
    lines = ["=" * LINE_WIDTH, f"[{r['n']}] @{r['owner']} — {r['code']} — {r['type']} — {modes}", "=" * LINE_WIDTH,
             f"Link      : {r['permalink']}",
             f"Posted    : {(r.get('posted') or '')[:10]}"
             + (f"      Duration: {float(r['duration_s']):.0f} s" if r.get("duration_s") else ""),
             f"Likes     : {r.get('likes')}      Plays: {r.get('plays')}"]
    if audio.get("title"):
        lines.append(f"Audio     : {audio['title']}" + (f" — {audio['artist']}" if audio.get("artist") else "")
                     + (" (original sound)" if audio.get("original") else ""))
    if len(r.get("collections") or []) > 1:
        lines.append(f"Also in   : {', '.join(r['collections'])}")
    lines += ["", "Post caption:", wrap_field(r.get("caption")) or "  (none)", ""]
    for c in r["content"]:
        label = f" ({c['slide'].replace('_', ' ')})" if c.get("slide") else ""
        if c.get("transcript"):
            lines += [f"Spoken transcript{label}" + (" (unreliable)" if c.get("transcript_unreliable") else "") + ":",
                      wrap_field(c["transcript"]), ""]
        if c.get("onscreen_text"):
            lines += [f"On-screen text{label}:", wrap_field(c["onscreen_text"]), ""]
        if c.get("frames"):
            lines += [f"Screenshots{label}: {len(c['frames'])} in "
                      f"{folder_path / Path(c['frames'][0]).parent}", ""]
    if r["status"] != "done":
        lines += [f"Status: {r['status']}", ""]
    return "\n".join(lines) + "\n"


def render_legacy(r: dict, n: int) -> str:
    try:
        dur = f"{float(r['duration_s']):.1f}s"
    except (TypeError, ValueError):
        dur = "n/a (not a video)"
    audio = f"{r['audio_title']} — {r['audio_artist']}" if r.get("audio_title") else "(none / original audio)"
    return "\n".join([
        "=" * LINE_WIDTH, f"[{n}] @{r['owner']} — {r['shortcode']}", "=" * LINE_WIDTH,
        f"Permalink : {r['permalink']}", f"Saved     : {r['taken_at']}", f"Duration  : {dur}",
        f"Likes     : {r['like_count']}      Views: {r['view_count']}", f"Audio     : {audio}", "",
        "Post caption:", wrap_field(r.get("caption")) or "  (none)", "",
        "Spoken transcript:", wrap_field(r.get("spoken_transcript")) or "  (not extracted)", "",
    ]) + "\n"


def existing_note(slug: str) -> str | None:
    for p in (VAULT / NOTE_FOLDER).glob(f"*-instagram-saved-{slug}.md"):
        return p.stem
    return None


def file_note(title: str, slug: str, summary: str, body: str, tags: list[str]) -> str:
    if not (VAULT / "AGENTS.md").exists():
        raise SystemExit(f"Second Brain vault not found at {VAULT} (is Google Drive for Desktop running?)")
    name = existing_note(slug) or f"instagram-saved-{slug}"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(body)
    cmd = [sys.executable, str(BRAIN), "file", "--folder", NOTE_FOLDER, "--title", title, "--summary", summary,
           "--tags", ",".join(tags), "--privacy", "private", "--name", name, "--update",
           "--source", f"Instagram saved collections, ig-saved-collections skill, {date.today().isoformat()}",
           "--body-file", tmp.name]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    if out.returncode != 0:
        raise SystemExit(f"brain.py file failed for {title!r}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def publish_folder(col_dir: Path, account: str | None = None) -> str:
    rows = [json.loads(line) for line in (col_dir / "index.jsonl").open(encoding="utf-8") if line.strip()]
    readme = col_dir / "README.md"
    heading = readme.open(encoding="utf-8").readline().strip() if readme.exists() else ""
    name = heading.split("— ", 1)[1] if "— " in heading else col_dir.name
    acct = account or "kaustubh__upadhyaya"
    slug = slugify(col_dir.name + (f"-{account}" if account else ""))
    if rows and "content" in rows[0]:
        body = "".join(render_new(r, col_dir) for r in rows)
        modes = {}
        for r in rows:
            for c in r["content"]:
                modes[c["mode"]] = modes.get(c["mode"], 0) + 1
        speech = sum(v for k, v in modes.items() if k in ("speech", "singing", "mixed", "unsure"))
        summary = (f"{len(rows)} items saved in the Instagram collection '{name}' (@{acct}): each post's caption, "
                   f"{speech} spoken transcripts and the on-screen text read from screenshots of the other reels.")
    else:
        body = "".join(render_legacy(r, i) for i, r in enumerate(rows, 1))
        summary = (f"{len(rows)} reels saved in the Instagram collection '{name}' (@{acct}), each with the poster's "
                   f"caption and, where available, a transcript of the words spoken in the reel.")
    title = f"Instagram Saved — {name}" + (f" ({account})" if account else "")
    intro = (f"# {title}\n\n"
             + textwrap.fill(f"Source: Instagram saved collection '{name}' on @{acct}, {len(rows)} items. Post caption "
                             f"= what the poster typed. Spoken transcript = local Whisper transcription of reels where "
                             f"someone speaks (best-effort). On-screen text = OCR of screenshots taken from reels whose "
                             f"audio is only music. Screenshots and videos are on D: in {col_dir}.",
                             LINE_WIDTH, break_long_words=False, break_on_hyphens=False)
             + "\n\n" + "-" * LINE_WIDTH + "\n\n")
    return file_note(title, slug, summary, intro + body, ["instagram", "saved-reels", slugify(name) or "collection"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="every collection folder rendered by render_collection.py")
    g.add_argument("--folder", help="one collection folder name under the root")
    g.add_argument("--collection", help="an extract_saved.py collection name (older layout)")
    ap.add_argument("--account", default=None)
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    root = Path(args.root) if args.root else (ROOT / args.account if args.account else ROOT)

    if args.all:
        folders = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith("_")
                         and (d / "index.jsonl").exists() and (d / "README.md").exists())
        folders += [root / "_Not in any collection"] if (root / "_Not in any collection" / "index.jsonl").exists() else []
    elif args.folder:
        folders = [root / args.folder]
    else:
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in args.collection).strip()
        folders = [root / safe]
    for d in folders:
        print(f"Published -> {publish_folder(d, args.account)}", flush=True)


def publish(collection: str, account: str | None, out_root: Path) -> str:
    """extract_saved.py --publish-to-second-brain calls this."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in collection).strip()
    return publish_folder((out_root / account if account else out_root) / safe, account)


if __name__ == "__main__":
    main()
