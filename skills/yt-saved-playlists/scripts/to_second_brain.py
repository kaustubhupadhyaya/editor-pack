"""Publish an already-extracted YouTube playlist (extract_playlist.py's
index.jsonl) as ONE human-readable note in the Second Brain vault (Google
Drive), in 00_Inbox/ per that vault's AGENTS.md contract.

Selection happens HERE, at publish time -- not at extraction time. Extraction
is slow, append-only and resumable, so you run it once per playlist; curation
is cheap and re-cuttable, so one archive can yield several different notes with
zero re-fetching. That's why the interesting flags live on this script:

    --videos ID,ID              only these video ids/URLs
    --positions 1-10,14,20-22   only these playlist positions
    --match REGEX               title/channel matches REGEX
    --with-transcripts-only     drop videos that had no caption track
    --curation FILE             one id per line (# comments; optional "| note")
    --limit N                   cap after other filters
    --slug-suffix TAG           REQUIRED to cut a 2nd note from the same playlist

Selectors AND together. With NO selector and more than --max-unselected videos
the script refuses and lists the selectors, so you never accidentally inline
4,994 Liked Videos into one note.

Usage:
    python extract_playlist.py --playlist PLxxxx            # extract first
    python to_second_brain.py --playlist PLxxxx --positions 1-15
    # or one step:
    python extract_playlist.py --playlist PLxxxx --publish-to-second-brain --positions 1-15

Vault: $SECOND_BRAIN_DIR if set, else G:\\My Drive\\SecondBrain. Only the
extracted CONTENT is published there; this skill's scripts stay in editor-pack.
Re-running overwrites the note BODY from the current index.jsonl but preserves
the original `created:` date and bumps only `updated:`. HOME.md is never touched
(AGENTS.md reserves it for new areas/projects, not routine captures).
"""
import argparse
import json
import os
import re
import textwrap
from datetime import date
from pathlib import Path

DEFAULT_VAULT = Path(r"<second-brain>")
LINE_WIDTH = 120
MAX_NOTE_CHARS = 400_000  # hard backstop against a pathologically huge single note


# --------------------------------------------------- shared helpers (ported)

def wrap_field(text: str | None, width: int = LINE_WIDTH, indent: str = "") -> str:
    if not text:
        return ""
    effective = max(20, width - len(indent))
    paragraphs = text.splitlines() or [text]
    wrapped = [
        textwrap.fill(p, width=effective, break_long_words=False, break_on_hyphens=False) if p.strip() else ""
        for p in paragraphs
    ]
    joined = "\n".join(wrapped)
    if indent:
        joined = "\n".join(indent + ln if ln else "" for ln in joined.splitlines())
    return joined


def vault_root() -> Path:
    root = Path(os.environ["SECOND_BRAIN_DIR"]) if os.environ.get("SECOND_BRAIN_DIR") else DEFAULT_VAULT
    if not (root / "AGENTS.md").exists():
        raise SystemExit(
            f"Second Brain vault not found at {root} (no AGENTS.md there). "
            "Is Google Drive for Desktop signed in and synced? Set SECOND_BRAIN_DIR to override."
        )
    return root


def yaml_str(value: str) -> str:
    """YAML-safe double-quoted scalar. Mandatory for any user/playlist text: an
    unquoted value containing ': ' makes YAML read a nested mapping and the whole
    frontmatter block silently fails to parse -- invisible on inspection, but the
    note becomes unfindable by exactly the cloud connectors `summary` feeds."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-+", "-", s)


def safe_name(s: str) -> str:
    """Identical to extract_playlist.safe_name -- the on-disk folder key."""
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in s).strip()[:80] or "playlist"


# ---------------------------------------------------------------- rendering

def render_record(record: dict, n: int, note: str | None = None) -> str:
    title = record.get("title") or record.get("video_id", "")
    channel = record.get("channel") or "unknown channel"
    header = f"[{n}] {title} — {channel}"
    transcript = record.get("transcript")
    src = record.get("transcript_source") or "none"
    lines = [
        "=" * LINE_WIDTH,
        header,
        "=" * LINE_WIDTH,
        f"URL      : {record.get('url', '')}",
        f"Position : {record.get('position', n)}",
        f"Captions : {src}",
    ]
    if note:
        lines.append(f"Note     : {note}")
    lines += [
        "",
        "Transcript:",
        wrap_field(transcript, indent="  ") or "  (no caption track — music, Short, or captions disabled)",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- selection

def parse_positions(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def _video_id(s: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", s)
    return m.group(1) if m else s.strip()


def load_curation(path: Path) -> dict[str, str | None]:
    """id -> optional curator note. Lines: 'VIDEOID', or 'VIDEOID | some note'.
    Blank lines and #-comments ignored."""
    out: dict[str, str | None] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if "|" in ln:
            vid, note = ln.split("|", 1)
            out[_video_id(vid.strip())] = note.strip() or None
        else:
            out[_video_id(ln)] = None
    return out


def read_records(jsonl_path: Path) -> list[dict]:
    records = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def select_records(records, *, videos=None, positions=None, match=None,
                   with_transcripts_only=False, curation=None, limit=0,
                   max_unselected=60):
    """Apply AND-combined selectors. Returns (selected, curator_notes).
    Raises SystemExit with guidance if nothing was selected AND the playlist is
    larger than max_unselected (the 'don't dump 4,994 videos' guard)."""
    any_selector = bool(videos or positions or match or with_transcripts_only or curation or limit)
    if not any_selector and len(records) > max_unselected:
        raise SystemExit(
            f"This playlist has {len(records)} videos — too many to inline into one note without a selector.\n"
            "Choose a subset (they AND together):\n"
            "  --positions 1-20            first 20 by playlist position\n"
            "  --videos ID,ID             specific videos\n"
            "  --match 'regex'            title/channel matches\n"
            "  --with-transcripts-only    skip the no-caption videos\n"
            "  --curation file.txt        a hand-picked id list\n"
            "  --limit N                  or just cap the count\n"
            f"(Raise --max-unselected above {len(records)} to override and publish everything.)"
        )

    want_ids = {_video_id(v) for v in videos} if videos else None
    want_pos = positions if positions else None
    rx = re.compile(match, re.I) if match else None
    notes = curation or {}
    cur_ids = set(notes) if curation else None

    selected = []
    for r in records:
        if want_ids is not None and r.get("video_id") not in want_ids:
            continue
        if cur_ids is not None and r.get("video_id") not in cur_ids:
            continue
        if want_pos is not None and int(r.get("position", -1)) not in want_pos:
            continue
        if rx is not None and not (rx.search(r.get("title", "")) or rx.search(r.get("channel", ""))):
            continue
        if with_transcripts_only and not r.get("transcript"):
            continue
        selected.append(r)
        if limit and len(selected) >= limit:
            break
    return selected, notes


# ---------------------------------------------------------- title resolution

def resolve_playlist(playlist: str, scripts_dir: Path) -> str:
    """Return the playlist TITLE (the on-disk folder key). Accepts a title
    verbatim, or maps a known id via playlists.json."""
    pj = scripts_dir / "playlists.json"
    if pj.exists():
        data = json.loads(pj.read_text(encoding="utf-8"))
        for p in data:
            if playlist in (p.get("id"), p.get("title")):
                return p["title"]
    return playlist


# ------------------------------------------------------------- note assembly

def assert_frontmatter_parses(note_text: str) -> None:
    try:
        import yaml
    except ImportError:
        return
    block = note_text.split("---", 2)
    if len(block) < 3:
        raise SystemExit("Refusing to write: note is missing its YAML frontmatter block.")
    try:
        parsed = yaml.safe_load(block[1])
    except yaml.YAMLError as e:
        raise SystemExit(f"Refusing to write: frontmatter is not valid YAML ({e.__class__.__name__}). Fix yaml_str().\n{e}")
    missing = {"title", "tags", "created", "updated", "summary"} - set(parsed or {})
    if missing:
        raise SystemExit(f"Refusing to write: frontmatter missing required field(s): {sorted(missing)}")


def note_slug(playlist_title: str, slug_suffix: str | None) -> str:
    base = slugify(f"youtube-playlist-{playlist_title}")
    return f"{base}-{slugify(slug_suffix)}" if slug_suffix else base


def build_note(playlist_title, slug, selected, total, created, updated, curator_notes):
    k = len(selected)
    no_caption = sum(1 for r in selected if not r.get("transcript"))
    # Visible title stays clean; the slug (with any --slug-suffix) is what makes
    # multiple subsets of one playlist distinct files.
    title = f"YouTube Playlist — {playlist_title}"
    tags = ["youtube", "transcripts", slugify(playlist_title)]
    summary = (
        f"{k} of {total} video(s) from the YouTube playlist '{playlist_title}', "
        f"full caption transcripts inlined"
        + (f" ({no_caption} had no caption track)" if no_caption else "")
        + "."
    )
    frontmatter = (
        "---\n"
        f"title: {yaml_str(title)}\n"
        f"tags: [{', '.join(tags)}]\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"summary: {yaml_str(summary)}\n"
        "---\n\n"
    )
    intro_prose = (
        f"Source: YouTube playlist **{playlist_title}**. Showing {k} of {total} video(s) as of {updated}. "
        "Each entry is the video's title/channel/URL and its caption transcript (YouTube's own captions — "
        "creator-made where available, else auto-generated). Videos with no caption track are marked."
    )
    intro = (
        f"# {title}\n\n"
        + textwrap.fill(intro_prose, width=LINE_WIDTH, break_long_words=False, break_on_hyphens=False)
        + "\n\n" + "-" * LINE_WIDTH + "\n\n"
    )
    body = "".join(
        render_record(r, i, note=curator_notes.get(r.get("video_id")))
        for i, r in enumerate(selected, 1)
    )
    note = frontmatter + intro + body
    if len(note) > MAX_NOTE_CHARS:
        raise SystemExit(
            f"Refusing to write: note is {len(note):,} chars (> {MAX_NOTE_CHARS:,}). "
            "Narrow the selection (fewer --positions, --with-transcripts-only, a smaller --limit)."
        )
    return note, summary


def find_existing_note(inbox: Path, slug: str) -> Path | None:
    for p in inbox.glob(f"*-{slug}.md"):
        return p
    return None


def update_inbox_index(inbox: Path, note_path: Path, summary: str) -> None:
    """List the note in 00_Inbox/INDEX.md. Root-level captures are inserted under
    a '## Root captures' section placed BEFORE the first subfolder '## ' heading
    -- not appended at EOF, where they'd land visually inside whatever the last
    subfolder section happens to be (the bug the Instagram sibling still has)."""
    index_path = inbox / "INDEX.md"
    note_stem = note_path.stem
    bullet = f"- [[{note_stem}]] — {summary}"

    if not index_path.exists():
        index_path.write_text(f"# 00_Inbox — Unprocessed Captures\n\n## Root captures\n{bullet}\n", encoding="utf-8")
        return

    text = index_path.read_text(encoding="utf-8")
    if note_stem in text:
        index_path.write_text(
            re.sub(rf"^- \[\[{re.escape(note_stem)}\]\].*$", bullet, text, flags=re.MULTILINE),
            encoding="utf-8")
        return

    lines = text.splitlines()
    # Reuse the single "## Root captures" section wherever it already is; only
    # create one (before the first subfolder section) if none exists yet.
    root_hdr = next((i for i, l in enumerate(lines) if l.strip() == "## Root captures"), None)
    if root_hdr is not None:
        lines.insert(root_hdr + 1, bullet)
    else:
        first_section = next((i for i, l in enumerate(lines) if l.startswith("## ")), None)
        if first_section is None:
            lines += ["", "## Root captures", bullet]
        else:
            lines[first_section:first_section] = ["## Root captures", bullet, ""]
    text = "\n".join(lines)
    text = re.sub(r"(?m)^updated: .*$", f"updated: {date.today().isoformat()}", text, count=1)
    index_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


# -------------------------------------------------------------------- publish

def publish(playlist, out_root, *, videos=None, positions=None, match=None,
            with_transcripts_only=False, curation=None, limit=0,
            slug_suffix=None, max_unselected=60, scripts_dir=None) -> Path:
    scripts_dir = scripts_dir or Path(__file__).resolve().parent
    playlist_title = resolve_playlist(playlist, scripts_dir)
    jsonl_path = Path(out_root) / safe_name(playlist_title) / "index.jsonl"
    if not jsonl_path.exists():
        raise SystemExit(f"No extraction at {jsonl_path} — run extract_playlist.py for this playlist first.")

    records = read_records(jsonl_path)
    cur = load_curation(Path(curation)) if curation else None
    selected, notes = select_records(
        records, videos=videos, positions=positions, match=match,
        with_transcripts_only=with_transcripts_only, curation=cur, limit=limit,
        max_unselected=max_unselected)
    if not selected:
        raise SystemExit("Selection matched 0 videos — nothing to publish. Loosen the filters.")

    slug = note_slug(playlist_title, slug_suffix)
    root = vault_root()
    inbox = root / "00_Inbox"

    existing = find_existing_note(inbox, slug)
    created = date.today().isoformat()
    if existing:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})-", existing.name)
        if m:
            created = m.group(1)
    updated = date.today().isoformat()

    note, summary = build_note(playlist_title, slug, selected, len(records), created, updated, notes or {})
    assert_frontmatter_parses(note)
    note_path = inbox / f"{created}-{slug}.md"
    note_path.write_text(note, encoding="utf-8")
    if existing and existing != note_path:
        existing.unlink()

    update_inbox_index(inbox, note_path, summary)
    return note_path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--playlist", required=True, help="Playlist id or title (matches extract_playlist.py).")
    ap.add_argument("--source", default="yt_export", help="Root folder extract_playlist.py wrote to (its --out).")
    ap.add_argument("--videos", help="Comma-separated video ids/URLs to include.")
    ap.add_argument("--positions", help="Playlist positions, e.g. '1-10,14,20-22'.")
    ap.add_argument("--match", help="Include only videos whose title/channel matches this regex.")
    ap.add_argument("--with-transcripts-only", action="store_true", help="Drop videos that had no caption track.")
    ap.add_argument("--curation", help="File of hand-picked video ids (one per line; # comments; optional '| note').")
    ap.add_argument("--limit", type=int, default=0, help="Cap the number of videos after other filters.")
    ap.add_argument("--slug-suffix", help="Required to cut a SECOND note from the same playlist (else it overwrites).")
    ap.add_argument("--max-unselected", type=int, default=60, help="Refuse to publish a whole playlist bigger than this without a selector.")
    args = ap.parse_args()

    note_path = publish(
        args.playlist, Path(args.source),
        videos=[v.strip() for v in args.videos.split(",")] if args.videos else None,
        positions=parse_positions(args.positions) if args.positions else None,
        match=args.match, with_transcripts_only=args.with_transcripts_only,
        curation=args.curation, limit=args.limit, slug_suffix=args.slug_suffix,
        max_unselected=args.max_unselected)
    print(f"Published -> {note_path}")


if __name__ == "__main__":
    main()
