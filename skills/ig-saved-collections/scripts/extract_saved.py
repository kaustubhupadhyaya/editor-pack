"""Extract information (post caption, owner, stats, audio, permalink -- and
optionally the video file and/or a SPOKEN-WORD TRANSCRIPT) out of every reel
in one of your saved collections, ONE REEL AT A TIME, walking the whole
collection.

Usage:
    python extract_saved.py --collection "Bike Edits" --out ./saved_export
    python extract_saved.py --collection "All posts" --out ./saved_export   # your WHOLE saved section
    python extract_saved.py --collection "Bike Edits" --out ./saved_export --download-video
    python extract_saved.py --collection "Bike Edits" --out ./saved_export --transcribe

Auth: reads IG_SESSIONID from a `.env` file in this folder (see .env.example).
Never passed on the command line. Credential stays on this machine.

How this walks the collection:
  Instagram serves saved items in pages of ~50 (a "chunk"). This script asks
  for one chunk, then loops over its items ONE AT A TIME -- for each item it
  pulls the caption/owner/stats/audio info, writes it immediately (so a run
  interrupted halfway through has already saved everything up to that point),
  optionally downloads the video and/or transcribes its speech, then sleeps
  briefly before the next item. Once a chunk is exhausted it asks Instagram
  for the next page's cursor and repeats, until Instagram reports no more
  pages -- i.e. it has walked the ENTIRE collection, not just the first page.

Two DIFFERENT kinds of "caption" this script can pull -- don't confuse them:
  - `caption`            = the text the poster typed below the reel (hashtags,
                            context). Always extracted, free (no download).
  - `spoken_transcript`  = what's actually SAID in the reel's audio, i.e. a
                            closed-caption-style transcript of speech. Only
                            extracted with --transcribe (opt-in: it requires
                            downloading the video and running Whisper, which
                            is much heavier than the metadata-only default --
                            don't turn it on unless the content is actually
                            wanted).

Output (in --out, one subfolder per collection) -- AGENT-READABLE, NOT
human-wrapped: this is bulk archival raw data that sits on external storage
and is only opened by an agent on demand, not read directly by a human in an
editor. One JSON object per line (JSONL) -- greppable, jq-able, and each
line is independently parseable with no unwrapping step, unlike a
word-wrapped text block. Long text (captions, transcripts) is stored as a
normal JSON string with real embedded newlines escaped the standard way --
don't reintroduce a word-wrapped .txt ledger here; that format is for the
human-facing Second Brain note (see to_second_brain.py), not this staging
archive.
  index.jsonl -- one JSON object per reel (owner, stats, post caption,
                 spoken transcript if extracted). Also what backs
                 resume/dedup on re-runs (keyed on `pk`).
  videos/     -- only created with --download-video; one mp4 per reel, named
                 by shortcode. With --transcribe alone (no --download-video)
                 the video is downloaded to a temp file, transcribed, and
                 deleted -- only the text is kept.
"""
import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

import requests

from ig_auth import get_client

FIELDS = [
    "pk", "shortcode", "permalink", "owner", "taken_at", "media_type", "product_type",
    "like_count", "view_count", "duration_s", "audio_title", "audio_artist",
    "caption", "spoken_transcript",
]


def dump_record(record: dict) -> str:
    return json.dumps({k: record.get(k) for k in FIELDS}, ensure_ascii=False)


def find_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    for p in Path(r"C:\Users").glob(r"*\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\ffmpeg.exe"):
        return str(p)
    raise SystemExit("ffmpeg not found on PATH -- required for --transcribe (extracts audio from the downloaded video).")


def download_video(cl, m, dest_dir: Path, attempts: int = 3) -> Path | None:
    """Fetch a reel's mp4 to dest_dir, returning its path (None on failure).

    Deliberately does NOT use instagrapi's clip_download(), for two reasons:
      1. TIMEOUT: clip_download -> video_download_by_url does
         `requests.get(url, stream=True, timeout=self.request_timeout)`, and
         instagrapi's default request_timeout is **1 second** -- fine for a
         JSON API call, hopeless for a multi-MB video, which is why a first
         full run lost ~30 transcripts to `Read timed out. (read timeout=1)`.
         Raising cl.request_timeout globally is NOT the fix: the same value
         is reused as `time.sleep(self.request_timeout)` between API calls,
         so a 30s timeout would mean a 30s sleep before every request.
      2. FEWER REQUESTS: clip_download re-fetches media_info_v1 to learn the
         video URL, but `m.video_url` is already populated on items returned
         by collection_medias_v1_chunk -- so going direct also removes one
         private-API call per reel, which lowers rate-limit/ban exposure.
    """
    url = str(m.video_url) if m.video_url else None
    if not url:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{m.code}.mp4"
    for attempt in range(1, attempts + 1):
        try:
            # (connect, read) -- generous read budget for the video body.
            with requests.get(url, stream=True, timeout=(10, 120)) as r:
                r.raise_for_status()
                with open(path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            return path
        except Exception as e:
            path.unlink(missing_ok=True)
            if attempt == attempts:
                print(f"    ! video download failed after {attempts} attempts: {e}")
                return None
            print(f"    . download attempt {attempt} failed ({type(e).__name__}), retrying...")
            time.sleep(2 * attempt)
    return None


def transcribe_reel(cl, m, model, ffmpeg_path: str, tmp_dir: Path, persistent_video_dir: Path | None, language: str | None) -> str | None:
    """Download (or reuse) the reel's video, extract audio, run stable-ts.
    Returns the plain-text transcript of speech, or None on failure/no audio."""
    video_path = None
    if persistent_video_dir:
        candidate = persistent_video_dir / f"{m.code}.mp4"
        if candidate.exists():
            video_path = candidate

    downloaded_to_tmp = False
    if video_path is None:
        video_path = download_video(cl, m, tmp_dir)
        if video_path is None:
            print("    ! transcript skipped (no video available)")
            return None
        downloaded_to_tmp = True

    audio_path = video_path.with_suffix(".transcribe.wav")
    try:
        subprocess.run(
            [ffmpeg_path, "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)],
            check=True, capture_output=True,
        )
        result = model.transcribe(str(audio_path), language=language, word_timestamps=False, vad=False)
        text = (result.text or "").strip()
        return text or None
    except Exception as e:
        print(f"    ! transcription failed: {e}")
        return None
    finally:
        audio_path.unlink(missing_ok=True)
        if downloaded_to_tmp:
            video_path.unlink(missing_ok=True)


def media_to_record(m) -> dict:
    music = getattr(m, "clips_music_attribution_info", None)
    audio_title = getattr(music, "song_name", None) if music else None
    audio_artist = getattr(music, "artist_name", None) if music else None
    is_video = m.media_type == 2  # 1=photo, 2=video/reel, 8=carousel
    return {
        "pk": str(m.pk),
        "shortcode": m.code,
        "permalink": f"https://www.instagram.com/reel/{m.code}/" if is_video else f"https://www.instagram.com/p/{m.code}/",
        "owner": m.user.username,
        "taken_at": m.taken_at.isoformat(),
        "media_type": m.media_type,
        "product_type": m.product_type,
        "caption": m.caption_text,
        "like_count": m.like_count,
        # play_count is the actual reel-views metric; view_count is frequently 0/stale on reels.
        "view_count": (getattr(m, "play_count", None) or m.view_count) if is_video else None,
        "duration_s": m.video_duration if is_video else None,
        "audio_title": audio_title,
        "audio_artist": audio_artist,
        "spoken_transcript": None,  # filled in by main() when --transcribe is passed
    }


def load_seen_pks(jsonl_path: Path, skip_missing_transcripts: bool = False) -> set:
    """PKs already extracted, so a re-run resumes instead of re-fetching.

    With skip_missing_transcripts=True, a video row whose spoken_transcript is
    empty is NOT counted as seen -- so --retry-missing-transcripts reprocesses
    exactly the reels whose transcript failed (usually a CDN timeout) without
    touching the ones that succeeded.
    """
    if not jsonl_path.exists():
        return set()
    seen = set()
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not row.get("pk"):
                continue
            if skip_missing_transcripts and row.get("media_type") == 2 and not (row.get("spoken_transcript") or "").strip():
                continue
            seen.add(row["pk"])
    return seen


def rewrite_outputs(jsonl_path: Path, records: list[dict]) -> None:
    """Rewrite index.jsonl from scratch, in order. Used by
    --retry-missing-transcripts, which updates rows in place rather than
    appending (the normal path is append-only)."""
    # Write to a temp file and swap in only after it fully succeeds. A crash
    # partway through a direct rewrite would otherwise truncate a file holding
    # hours of completed transcription.
    tmp = jsonl_path.with_suffix(".jsonl.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(dump_record(r) + "\n")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(jsonl_path)


def retry_missing(cl, collection_pk, args, out_dir: Path, jsonl_path: Path, whisper_model, ffmpeg_path) -> None:
    """Re-transcribe only the rows whose spoken_transcript came back empty.

    Walks the collection again (cheap listing calls) so it can get each reel's
    CURRENT video_url -- the URLs are signed and expire, so they can't be
    cached and reused later. Rows that already have a transcript are carried
    through untouched; index.jsonl is then rewritten in collection order.
    """
    if not jsonl_path.exists():
        raise SystemExit(f"Nothing to retry -- no existing extraction at {jsonl_path}.")
    existing = {}
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                row = json.loads(line)
                existing[row["pk"]] = row

    needs_retry = {
        pk for pk, row in existing.items()
        if row.get("media_type") == 2 and not (row.get("spoken_transcript") or "").strip()
    }
    print(f"{len(existing)} rows on disk; {len(needs_retry)} video(s) missing a transcript.")
    if not needs_retry:
        print("Nothing to retry.")
        return

    ordered: list[dict] = []
    fixed = 0
    next_max_id = ""
    while True:
        items, next_max_id = cl.collection_medias_v1_chunk(collection_pk, max_id=next_max_id)
        if not items:
            break
        for m in items:
            key = str(m.pk)
            if key not in existing:
                continue  # showed up since the last run; a normal (non-retry) run will pick it up
            if key in needs_retry:
                print(f"  retrying @{m.user.username} {m.code} ...")
                record = media_to_record(m)
                record["spoken_transcript"] = transcribe_reel(
                    cl, m, whisper_model, ffmpeg_path,
                    tmp_dir=out_dir / "_tmp", persistent_video_dir=None,
                    language=(args.language or None),
                )
                if record["spoken_transcript"]:
                    fixed += 1
                ordered.append(record)
                time.sleep(args.item_sleep)
            else:
                ordered.append(existing[key])
        if not next_max_id:
            break
        time.sleep(args.page_sleep)

    rewrite_outputs(jsonl_path, ordered)
    still_missing = len(needs_retry) - fixed
    print(f"\nRetry done: {fixed} transcript(s) recovered, {still_missing} still missing -> {jsonl_path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collection", required=True, help='Collection name, e.g. "Bike Edits", or "All posts" for the whole saved section.')
    ap.add_argument("--account", default=None, help="Named account (uses .env.<account>). Omit for the default account (.env).")
    ap.add_argument("--out", default="saved_export", help="Output root folder.")
    ap.add_argument("--download-video", action="store_true", help="Also download each reel's video file (slower, heavier requests).")
    ap.add_argument("--transcribe", action="store_true", help="Also transcribe each reel's SPOKEN audio (closed-caption-style text of what's said, not the post caption). Opt-in: downloads video + runs Whisper, much heavier than the default. Skipped for non-video items.")
    ap.add_argument("--whisper-model", default="base", help="stable-ts/Whisper model size for --transcribe (default: base).")
    ap.add_argument("--language", default="en", help='Language hint for --transcribe (default: en). Pass "" to auto-detect.')
    ap.add_argument("--limit", type=int, default=0, help="Stop after N new items (0 = no limit, walk the whole collection).")
    ap.add_argument("--item-sleep", type=float, default=2.0, help="Seconds to sleep between items.")
    ap.add_argument("--page-sleep", type=float, default=6.0, help="Seconds to sleep between pages (~50 items each).")
    ap.add_argument("--publish-to-second-brain", action="store_true", help="After extracting, publish the result as a note in the Second Brain vault's 00_Inbox (see to_second_brain.py).")
    ap.add_argument("--retry-missing-transcripts", action="store_true", help="Reprocess ONLY already-extracted video rows whose spoken_transcript is empty (e.g. lost to a CDN timeout), leaving successful rows untouched. Implies --transcribe.")
    args = ap.parse_args()

    if args.retry_missing_transcripts:
        args.transcribe = True

    whisper_model = None
    ffmpeg_path = None
    if args.transcribe:
        import stable_whisper  # lazy: pulls in torch -- only load it when actually transcribing
        ffmpeg_path = find_ffmpeg()
        print(f"Loading Whisper model {args.whisper_model!r} for transcription (first run downloads it)...")
        whisper_model = stable_whisper.load_model(args.whisper_model)

    cl = get_client(account=args.account)
    pk = cl.collection_pk_by_name(args.collection)

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in args.collection).strip()
    # Namespace by account only when one was given, so the default account's
    # output path is unchanged from before multi-account support existed.
    out_dir = Path(args.out) / args.account / safe_name if args.account else Path(args.out) / safe_name
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "index.jsonl"
    video_dir = out_dir / "videos"

    if args.retry_missing_transcripts:
        retry_missing(cl, pk, args, out_dir, jsonl_path, whisper_model, ffmpeg_path)
        if args.publish_to_second_brain:
            from to_second_brain import publish
            print(f"Published to Second Brain -> {publish(args.collection, args.account, Path(args.out))}")
        return

    seen = load_seen_pks(jsonl_path)
    print(f"Resuming: {len(seen)} items already extracted for {args.collection!r}." if seen else f"Starting fresh extraction of {args.collection!r}.")

    new_count = 0
    next_max_id = ""
    hit_limit = False
    with open(jsonl_path, "a", encoding="utf-8") as jf:
        while True:
            items, next_max_id = cl.collection_medias_v1_chunk(pk, max_id=next_max_id)
            if not items:
                break

            for m in items:  # one reel at a time
                if str(m.pk) in seen:
                    continue

                record = media_to_record(m)
                new_count += 1
                print(f"  [{new_count}] @{record['owner']} {record['shortcode']} — {(record['caption'] or '')[:60]!r}")

                if args.download_video and record["media_type"] == 2:
                    download_video(cl, m, video_dir)

                if args.transcribe and record["media_type"] == 2:
                    print("    transcribing speech...")
                    record["spoken_transcript"] = transcribe_reel(
                        cl, m, whisper_model, ffmpeg_path,
                        tmp_dir=out_dir / "_tmp",
                        persistent_video_dir=video_dir if args.download_video else None,
                        language=(args.language or None),
                    )

                jf.write(dump_record(record) + "\n")
                jf.flush()
                seen.add(str(m.pk))

                if args.limit and new_count >= args.limit:
                    print(f"\nHit --limit {args.limit}, stopping.")
                    hit_limit = True
                    break

                time.sleep(args.item_sleep)

            if hit_limit or not next_max_id:
                break
            time.sleep(args.page_sleep)

    print(f"\nDone. {new_count} new reels extracted -> {jsonl_path}")

    if args.publish_to_second_brain:
        from to_second_brain import publish
        note_path = publish(args.collection, args.account, Path(args.out))
        print(f"Published to Second Brain -> {note_path}")


if __name__ == "__main__":
    main()
