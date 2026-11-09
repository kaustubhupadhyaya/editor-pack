"""Turn every saved Instagram item into content that can be read offline: a transcript when someone speaks,
screenshots plus the text printed on screen when the audio is only music, and both when that is unclear.

Filing a voiceover as "music" loses its content for good, so every unclear case gets both outputs.
Per manifest line the script writes into that item's out_dir:

  content.json       the source of truth, rewritten atomically after every pass (passes_done says how far it got)
  transcript.txt     only when a transcript was made, wrapped at 120 chars
  frames/            JPEGs named by timestamp (t0003.2s.jpg), at most MAX_FRAMES
  onscreen_text.txt  the text OCR read from the frames, or from the image, in order of first appearance

Passes run in this order, each loading only its own model (the PC often has under 1 GB of free RAM):

  classify    ffprobe + ffmpeg decode, Silero VAD (seconds of speech), AudioSet classifier (Speech/Music/Singing)
  transcribe  faster-whisper, only for items whose decision needs a transcript
  visual      ffmpeg frames + RapidOCR for items that need pictures, and OCR of image items

Usage:
  C:\\Python314\\python.exe reel_content.py --manifest manifest.jsonl [--passes classify,transcribe,visual]
                                          [--threads auto|2|4] [--limit N] [--window 04:00-10:00]

The manifest has one JSON object per line: {"key", "media", "kind": "video"|"audio"|"image", "out_dir"}.
A pass skips items whose content.json already lists it in passes_done, so re-running the same command resumes.

The command supervises itself like ig_dm_export.py: the work runs in a child process whose watchdog exits with code
75 when nothing has advanced for STALL_S seconds, and the parent restarts it from the checkpoints (up to
MAX_RESTARTS times). The job-application bots own the machine from 04:00 to 10:00 IST: a batch that was started
outside that window finishes its current item when 04:00 arrives, frees its model and waits until 10:00 IST.
"""
import argparse
import gc
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import zlib
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

FFMPEG_DIR = (r"%USERPROFILE%\AppData\Local\Microsoft\WinGet\Packages"
              r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin")
WHISPER_DIR = Path(r"D:\DevCaches\faster-whisper\small")
AST_DIR = Path(r"D:\DevCaches\hf-models\ast")    # MIT/ast-finetuned-audioset-10-10-0.4593
AST_BYTES = 346_404_948                          # model.safetensors is smaller while the download is unfinished

STALL_S = int(os.environ.get("REEL_CONTENT_STALL_S", 300))   # nothing advanced this long -> watchdog stops the child
WATCHDOG_EXIT = 75   # the supervising parent restarts on this code; content.json makes it a resume
MAX_RESTARTS = 5
CHILD_ENV = "REEL_CONTENT_CHILD"
STARTED_ENV = "REEL_CONTENT_STARTED_IN_WINDOW"   # decided once by the parent, so a restart at 04:10 still pauses
LOAD_STALL_S = 900   # the watchdog's patience while a model is imported and loaded
IDLE_LOG_S = 300     # while paused for the appliers window, say so this often
WRAP = 120
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PASSES = ("classify", "transcribe", "visual")
SR = 16000
PROBE_TIMEOUT_S = 30
DECODE_TIMEOUT_S = 120
FRAMES_TIMEOUT_S = 150

# ---- decision thresholds ----
# Calibrated on 2026-10-22 against the 308 real reel audios and the 2026-10-19 Whisper transcripts: SILENT_DB,
# VAD_THRESHOLD, MIN_SPEECH_S, CLEAR_SPEECH_S, CLEAR_RATIO, MIN_CHARS, LOGPROB_MIN, SKIP_SURE_P.
SILENT_DB = -55.0        # RMS level (dBFS) below which a track counts as silent (308 audios: quietest -36.9)
VAD_THRESHOLD = 0.5      # Silero speech probability
MIN_SPEECH_S = 1.5       # less VAD speech than this = nobody speaks
CLEAR_SPEECH_S = 6.0     # without the classifier: this much VAD speech, at this share of the clip, is clearly speech
CLEAR_RATIO = 0.35       # (empty-transcript items: 0 of 61 reach it; long-transcript items: 90 of 101 do)
MIN_CHARS = 15           # a shorter transcript is not coherent
LOGPROB_MIN = -1.0       # Whisper's mean avg_logprob below this = not coherent
                         # (no_speech_prob is kept as evidence only: it reached 0.64 on valid Hindi speech)
SKIP_LANGS = {"kn", "te", "ml", "ta"}   # the small model loops forever on Kannada and labels it as these
SKIP_SURE_P = 0.6        # below this probability the label is often another language (2 of 11 on 2026-10-19 were
                         # English-like speech), so those get a guarded decode instead of a skip
# Checked on 2026-10-22 on 153 of the 308 real audios (AST run stopped at the appliers window): 0 of 43 items with a
# real spoken transcript were filed music_only; 23 of 31 empty-transcript items became music_only; the 10
# music_only items that had any old text were all song fragments or hallucinations (Speech score <= 0.07).
VOICED_MIN_S = 1.0       # a classifier window is "voiced" when VAD heard this much speech inside it
MUSIC_MIN = 0.35         # AudioSet Music score needed to call an item music-only
SPEECH_LOW = 0.40        # music-only needs every window's Speech score below this ...
SINGING_LOW = 0.40       # ... and Singing below this
SPEECH_MIN = 0.30        # a voiced window is speech-led when Speech >= this and above Music and Singing
SINGING_MIN = 0.30
LED_SHARE = 0.60         # share of voiced windows that must be speech-led (or singing-led)
AST_WIN_S = 10.0
AST_MAX_WINDOWS = 12
SPEECH_LABELS = ["Speech", "Male speech, man speaking", "Female speech, woman speaking",
                 "Child speech, kid speaking", "Conversation", "Narration, monologue", "Speech synthesizer"]
MUSIC_LABELS = ["Music"]
SINGING_LABELS = ["Singing", "Choir", "Rapping", "Male singing", "Female singing", "Child singing"]

# ---- screenshots and on-screen text ----
SCENE_CUT = 0.30         # ffmpeg scene score that counts as a cut
FRAME_EVERY_S = 4        # ... plus at least one frame this often
MAX_CANDIDATES = 200
MAX_FRAMES = 12
MAX_EDGE = 1280          # long edge of a saved frame
DUP_BITS = 6             # dHash (16 x 16 gradient bits) distance up to this ...
DUP_MAD = 1.0            # ... and mean gray-level difference of the 32 x 32 thumbnails up to this = same picture
                         # (the 4 real mp4s: identical shot <= 3 bits and <= 0.7; a typed word 10 bits and 1.4)
OCR_MIN_SCORE = 0.5
IMAGE_MAX_EDGE = 2000


class Watchdog:
    """Exits the process when nothing has advanced for STALL_S seconds. A subprocess timeout covers one stuck
    ffmpeg; this covers any other hang (a model call, a read from a stalled drive)."""

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


class patience:
    """A cold `import transformers` took 80 s here and a model load can stall on a busy disk, so the watchdog is more
    patient while one loads; a load that hangs still ends the process after LOAD_STALL_S."""

    def __enter__(self):
        if watchdog:
            self.normal, watchdog.stall_s = watchdog.stall_s, LOAD_STALL_S
            watchdog.beat()

    def __exit__(self, *exc):
        if watchdog:
            watchdog.stall_s = self.normal
            watchdog.beat()


def tool(name):
    path = os.path.join(FFMPEG_DIR, name + ".exe")
    return path if os.path.exists(path) else name   # falls back to PATH


def run(cmd, timeout):
    """Every subprocess call has a timeout. It is checked every 10 s while the watchdog is kept alive, because a
    long saved video legitimately takes longer than the watchdog's 5 minutes to decode, and a fixed short timeout
    failed every such video on every rerun."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    deadline = time.monotonic() + timeout
    while True:
        try:
            out, err = proc.communicate(timeout=10)
            break
        except subprocess.TimeoutExpired:
            beat()
            if time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise RuntimeError(f"{Path(cmd[0]).stem} timed out after {timeout:.0f}s")
    beat()
    if proc.returncode:
        tail = err.decode("utf-8", "replace").strip()[-300:]
        raise RuntimeError(f"{Path(cmd[0]).stem} exit {proc.returncode}: {tail}")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def scaled(base, duration, per_second):
    """A timeout that grows with the media's length (saved videos run from 3 s to an hour)."""
    return max(base, (duration or 0) * per_second)


def replace_file(tmp, path):
    for attempt in range(6):
        try:
            return os.replace(tmp, path)
        except PermissionError:   # Windows: a virus scanner or indexer holds the file for a moment
            if attempt == 5:
                raise
            time.sleep(0.5)


def write_json_atomic(path, obj):
    """A run stopped mid-write must never leave a half-written checkpoint."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    replace_file(tmp, path)


def write_text_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    replace_file(tmp, path)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")   # this PC's clock is IST


# ---------------------------------------------------------------- the appliers window and the thread count

def parse_window(text):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", text)
    if not m:
        sys.exit(f"--window must look like 04:00-10:00, got {text!r}")
    a, b, c, d = map(int, m.groups())
    return a * 60 + b, c * 60 + d


def in_window(window, when=None):
    when = when or datetime.now()
    minute = when.hour * 60 + when.minute
    start, end = window
    return start <= minute < end if start < end else minute >= start or minute < end


def clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class Batch:
    """What every pass needs to know about this run."""

    def __init__(self, args):
        self.threads_arg = args.threads
        self.window = parse_window(args.window)
        started = os.environ.get(STARTED_ENV)
        self.started_in_window = in_window(self.window) if started is None else started == "1"

    def threads(self):
        if self.threads_arg != "auto":
            return int(self.threads_arg)
        return 2 if in_window(self.window) else 4

    def must_pause(self):
        """Only a batch that was running when the window began pauses; one started inside it was asked to run
        there, at the 2 threads that --threads auto gives."""
        return not self.started_in_window and in_window(self.window)

    def wait_out_window(self):
        end = clock(self.window[1])
        print(f"paused for the appliers window until {end} IST", flush=True)
        last = float("-inf")
        while in_window(self.window):
            beat()   # a deliberate wait, not a stall
            if time.monotonic() - last >= IDLE_LOG_S:
                print(f"idle: waiting for the appliers window to end at {end} IST (now {datetime.now():%H:%M})",
                      flush=True)
                last = time.monotonic()
            time.sleep(15)
        print(f"resuming at {datetime.now():%H:%M} IST", flush=True)


# ---------------------------------------------------------------- manifest and content.json

def read_manifest(path, limit):
    items = []
    with open(path, encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            missing = [k for k in ("key", "media", "kind", "out_dir") if not item.get(k)]
            if missing or item["kind"] not in ("video", "audio", "image"):
                sys.exit(f"manifest line {n}: need key, media, kind (video|audio|image), out_dir; got {item}")
            items.append(item)
    return items[:limit] if limit else items


def new_content(item):
    return {"key": item["key"], "media": item["media"], "kind": item["kind"], "duration_s": None,
            "has_audio": None, "audio_level_db": None, "vad": None, "ast": None, "language": None,
            "language_prob": None, "whisper_speech_s": None, "mode": None, "mode_reasons": [],
            "needs": {"transcript": False, "visual": False}, "transcript": None, "transcript_unreliable": None,
            "avg_logprob": None, "no_speech_prob": None, "frames": [], "onscreen_text": None,
            "passes_done": [], "processed_at": None}


def load_content(item):
    path = Path(item["out_dir"]) / "content.json"
    if path.exists():
        try:
            c = json.loads(path.read_text(encoding="utf-8"))
            if c.get("key") == item["key"] and c.get("media") == item["media"]:
                return c
        except (OSError, ValueError):
            pass   # an unreadable checkpoint is redone from scratch
    return new_content(item)


def save_content(item, c):
    out = Path(item["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    write_json_atomic(out / "content.json", c)


# ---------------------------------------------------------------- media: probe, decode, VAD, classifier

def probe(path):
    out = run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type",
               "-of", "json", str(path)], PROBE_TIMEOUT_S).stdout
    info = json.loads(out)
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, ValueError):
        duration = None
    return duration, any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def load_audio(path, duration=None):
    """16 kHz mono float32 via ffmpeg. faster-whisper's own decoder passes an option PyAV 19 removed
    (open(..., metadata_errors=...)), so we decode here and hand it the samples."""
    out = run([tool("ffmpeg"), "-nostdin", "-loglevel", "error", "-i", str(path), "-vn", "-f", "s16le", "-ac", "1",
               "-ar", str(SR), "-"], scaled(DECODE_TIMEOUT_S, duration, 0.5)).stdout
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768.0


def level_db(audio):
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    return round(20 * math.log10(max(rms, 1e-6)), 1)


def vad_stats(audio, duration):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    options = VadOptions(threshold=VAD_THRESHOLD, min_speech_duration_ms=250, min_silence_duration_ms=300,
                         speech_pad_ms=0)
    segments = [[round(s["start"] / SR, 2), round(s["end"] / SR, 2)] for s in get_speech_timestamps(audio, options)]
    speech_s = round(sum(e - s for s, e in segments), 2)
    return {"speech_s": speech_s, "speech_ratio": round(speech_s / duration, 3) if duration else 0.0,
            "segments": segments}


def window_spans(duration):
    """10 s windows, at most AST_MAX_WINDOWS, spread evenly over a long clip; the last one ends at the clip's end."""
    if duration <= AST_WIN_S:
        return [(0.0, round(duration, 2))]
    n = math.ceil(duration / AST_WIN_S)
    if n <= AST_MAX_WINDOWS:
        starts = [i * AST_WIN_S for i in range(n - 1)] + [duration - AST_WIN_S]
    else:
        starts = np.linspace(0, duration - AST_WIN_S, AST_MAX_WINDOWS)
    return [(round(float(s), 2), round(float(s) + AST_WIN_S, 2)) for s in starts]


class AudioSetClassifier:
    """AST fine-tuned on AudioSet, CPU only. Scores are sigmoids (AudioSet is multi-label), so Speech and Music can
    both be high for a voiceover over a music bed."""

    def __init__(self, threads):
        weights = AST_DIR / "model.safetensors"   # checked first: the imports below take 80 s on a cold start
        if not weights.exists() or weights.stat().st_size != AST_BYTES:
            raise OSError(f"{weights} is not the complete {AST_BYTES:,} byte model "
                          f"({weights.stat().st_size if weights.exists() else 0:,} bytes on disk)")
        import torch
        from transformers import ASTFeatureExtractor, ASTForAudioClassification
        torch.set_num_threads(threads)
        self.torch = torch
        self.extractor = ASTFeatureExtractor.from_pretrained(str(AST_DIR), local_files_only=True)
        self.model = ASTForAudioClassification.from_pretrained(str(AST_DIR), local_files_only=True).eval()
        self.index_labels()

    def index_labels(self):
        self.labels = {int(i): name for i, name in self.model.config.id2label.items()}
        ids = {name: i for i, name in self.labels.items()}
        for group in (SPEECH_LABELS, MUSIC_LABELS, SINGING_LABELS):
            absent = [name for name in group if name not in ids]
            if absent:
                raise ValueError(f"labels missing from the model: {absent}")
        self.speech_ids = [ids[n] for n in SPEECH_LABELS]
        self.music_ids = [ids[n] for n in MUSIC_LABELS]
        self.singing_ids = [ids[n] for n in SINGING_LABELS]

    def classify(self, audio, duration):
        windows = []
        for t0, t1 in window_spans(duration):
            clip = audio[int(t0 * SR):int(t1 * SR)]
            if len(clip) < SR // 2:
                continue
            inputs = self.extractor(clip, sampling_rate=SR, return_tensors="pt")
            with self.torch.inference_mode():
                probs = self.torch.sigmoid(self.model(**inputs).logits[0])
            top = self.torch.topk(probs, 5)
            best = [[self.labels[int(i)], round(float(p), 3)] for p, i in zip(top.values, top.indices)]
            windows.append({"t0": t0, "t1": t1,
                            "speech": round(float(probs[self.speech_ids].max()), 3),
                            "music": round(float(probs[self.music_ids].max()), 3),
                            "singing": round(float(probs[self.singing_ids].max()), 3), "top": best})
            beat()
        if not windows:
            return None
        return {"windows": windows, "speech_max": max(w["speech"] for w in windows),
                "music_max": max(w["music"] for w in windows), "singing_max": max(w["singing"] for w in windows)}


# ---------------------------------------------------------------- the decision

def voiced_seconds(window, segments):
    return sum(max(0.0, min(e, window["t1"]) - max(s, window["t0"])) for s, e in segments)


def looping(text):
    """Whisper's failure mode on unsupported speech is one syllable repeated; it compresses extremely well."""
    raw = text.encode("utf-8")
    return len(raw) > 60 and len(raw) / max(1, len(zlib.compress(raw))) > 3.0


def transcript_coherent(c):
    """(ok, why not). Statistical only: long enough, not looping, and Whisper itself was not doubtful."""
    text = c["transcript"] or ""
    if len(text) < MIN_CHARS:
        return False, f"transcript has {len(text)} chars"
    if c["transcript_unreliable"]:
        return False, "transcript loops"
    if c["avg_logprob"] is not None and c["avg_logprob"] < LOGPROB_MIN:
        return False, f"Whisper avg_logprob {c['avg_logprob']} < {LOGPROB_MIN}"
    return True, ""


def classifier_mode(c, reasons):
    ast, vad = c["ast"], c["vad"]
    s, m, g = ast["speech_max"], ast["music_max"], ast["singing_max"]
    scores = f"classifier Speech {s:.2f}, Music {m:.2f}, Singing {g:.2f}"
    if vad["speech_s"] < MIN_SPEECH_S:
        if m >= MUSIC_MIN and s < SPEECH_LOW and g < SINGING_LOW:
            reasons.append(f"VAD speech {vad['speech_s']} s < {MIN_SPEECH_S} s; {scores}")
            return "music_only"
        reasons.append(f"VAD speech {vad['speech_s']} s < {MIN_SPEECH_S} s but not clearly music: {scores}")
        return "unsure"
    voiced = [w for w in ast["windows"] if voiced_seconds(w, vad["segments"]) >= VOICED_MIN_S] or ast["windows"]
    speech_led = sum(w["speech"] >= SPEECH_MIN and w["speech"] > w["music"] and w["speech"] > w["singing"]
                     for w in voiced)
    singing_led = sum(w["singing"] >= SINGING_MIN and w["singing"] > w["speech"] for w in voiced)
    evidence = f"VAD speech {vad['speech_s']} s; {scores}; speech-led {speech_led}/{len(voiced)} voiced windows"
    if speech_led / len(voiced) >= LED_SHARE:
        reasons.append(evidence)
        return "speech"
    if singing_led / len(voiced) >= LED_SHARE:
        reasons.append(f"{evidence}; singing-led {singing_led}")
        return "singing"
    reasons.append(f"{evidence}; singing-led {singing_led}: speech and music mixed")
    return "mixed"


def vad_mode(c, reasons):
    """No classifier: VAD seconds first, then Whisper's own signals once the transcript exists."""
    vad, heard = c["vad"], c["language"] is not None
    reasons.append("AudioSet classifier unavailable: decided from VAD and Whisper signals")
    if vad["speech_s"] < MIN_SPEECH_S:
        reasons.append(f"VAD speech {vad['speech_s']} s < {MIN_SPEECH_S} s")
        if heard and len(c["transcript"] or "") < MIN_CHARS:
            reasons.append("Whisper found no speech either; music itself is not confirmed")
            return "music_only"
        return "unsure"
    reasons.append(f"VAD speech {vad['speech_s']} s ({vad['speech_ratio']:.0%} of the clip)")
    if vad["speech_s"] >= CLEAR_SPEECH_S and vad["speech_ratio"] >= CLEAR_RATIO:
        return "speech"
    return "unsure"


def decide(c):
    """(mode, reasons, needs) from everything known about the item so far. Classify and transcribe both call it,
    so the verdict always reflects the latest evidence."""
    kind, reasons = c["kind"], []
    picture = kind in ("video", "image")

    def result(mode, transcript, visual):
        if visual and not picture:
            reasons.append("audio-only file: no screenshots possible")
        return mode, reasons, {"transcript": transcript, "visual": visual and picture}

    if kind == "image":
        reasons.append("image: on-screen text only")
        return result("image", False, True)
    if not c["has_audio"]:
        reasons.append("no audio stream")
        return result("no_audio", False, True)
    if c["audio_level_db"] < SILENT_DB:
        reasons.append(f"silent track ({c['audio_level_db']} dB < {SILENT_DB} dB)")
        return result("no_audio", False, True)
    heard = c["language"] is not None
    if heard and c["language"] in SKIP_LANGS and (c["whisper_speech_s"] or 0) >= MIN_SPEECH_S \
            and not transcript_coherent(c)[0]:
        reasons.append(f"Whisper detected {c['language']} (p={c['language_prob']}); the small model loops on "
                       "Kannada-family speech, so no transcript")
        return result("unsupported_language", False, True)
    mode = classifier_mode(c, reasons) if c["ast"] else vad_mode(c, reasons)
    if mode == "speech" and heard:
        ok, why = transcript_coherent(c)
        if not ok:
            reasons.append(f"speech verdict not confirmed: {why}")
            mode = "unsure"
    if mode == "speech":
        return result(mode, True, False)
    if mode == "music_only":
        return result(mode, False, True)
    return result(mode, True, True)


def apply_decision(c):
    c["mode"], c["mode_reasons"], c["needs"] = decide(c)


# ---------------------------------------------------------------- the pass runner

def run_pass(name, items, batch, applies, make_worker, process):
    """Runs one pass over the items that still need it. The worker owns this pass's model; it is dropped while
    paused for the appliers window and rebuilt afterwards."""
    todo, waiting = [], 0
    for item in items:
        c = load_content(item)
        if name in c["passes_done"]:
            continue
        verdict = applies(c)
        if verdict is None:
            waiting += 1
        elif verdict:
            todo.append((item, c))
    print(f"{name}: {len(items)} items, {len(todo)} to do, {len(items) - len(todo) - waiting} already done or not "
          f"needed, {waiting} waiting for an earlier pass", flush=True)
    worker, done, failed, started = None, 0, [], time.monotonic()
    for n, (item, c) in enumerate(todo, 1):
        if batch.must_pause():
            worker = None
            gc.collect()
            batch.wait_out_window()
        if worker is None:
            with patience():
                worker = make_worker(batch.threads())
        t0 = time.monotonic()
        try:
            line = process(worker, item, c)
            c["passes_done"].append(name)
            c["processed_at"] = now_iso()
            c.pop("error", None)
            save_content(item, c)
            done += 1
            print(f"[{name} {n}/{len(todo)}] key={item['key']} {line} in {time.monotonic() - t0:.1f}s", flush=True)
        except Exception as e:   # one bad file must not stop a 1,800-item batch; it is retried on the next run
            failed.append(item["key"])
            c["error"] = {"pass": name, "message": f"{type(e).__name__}: {e}"[:400], "at": now_iso()}
            save_content(item, c)
            print(f"[{name} {n}/{len(todo)}] key={item['key']} FAILED {type(e).__name__}: {str(e)[:200]}", flush=True)
        beat()
    spent = time.monotonic() - started
    print(f"{name} done: {done} ok, {len(failed)} failed in {spent:.0f}s"
          f"{f' ({spent / done:.2f} s/item)' if done else ''}{f'; failed keys {failed[:10]}' if failed else ''}",
          flush=True)
    del worker
    gc.collect()


# ---------------------------------------------------------------- pass 1: classify

class Classifier:
    def __init__(self, threads):
        self.ast = None
        try:
            self.ast = AudioSetClassifier(threads)
        except Exception as e:   # ImportError, OSError (incomplete weights), ValueError (labels)
            print(f"AudioSet classifier unavailable ({type(e).__name__}: {e}); deciding from VAD and Whisper signals",
                  flush=True)


def classify_item(worker, item, c):
    media, kind = item["media"], item["kind"]
    if not Path(media).is_file():
        raise FileNotFoundError(media)
    c.update(duration_s=None, has_audio=False, audio_level_db=None, vad=None, ast=None)
    if kind != "image":
        duration, c["has_audio"] = probe(media)
        if c["has_audio"]:
            audio = load_audio(media, duration)
            beat()
            duration = duration or len(audio) / SR
            c["audio_level_db"] = level_db(audio)
            c["vad"] = vad_stats(audio, duration)
            beat()
            if worker.ast and c["audio_level_db"] >= SILENT_DB:
                c["ast"] = worker.ast.classify(audio, duration)
        c["duration_s"] = round(duration, 2) if duration else None
    apply_decision(c)
    vad, ast = c["vad"] or {}, c["ast"]
    scores = f" S={ast['speech_max']:.2f} M={ast['music_max']:.2f} G={ast['singing_max']:.2f}" if ast else ""
    return (f"mode={c['mode']} speech_s={vad.get('speech_s')} dur={c['duration_s']} level={c['audio_level_db']}"
            f"{scores}")


def pass_classify(items, batch):
    run_pass("classify", items, batch, lambda c: True, Classifier, classify_item)


# ---------------------------------------------------------------- pass 2: transcribe

class Transcriber:
    def __init__(self, threads):
        from faster_whisper import WhisperModel
        self.model = WhisperModel(str(WHISPER_DIR), device="cpu", compute_type="int8", cpu_threads=threads)

    def transcribe(self, audio, duration):
        segments, info = self.model.transcribe(audio, beam_size=1, temperature=0.0, vad_filter=True,
                                               condition_on_previous_text=False, no_speech_threshold=0.6)
        out = {"language": info.language, "language_prob": round(info.language_probability, 2),
               "whisper_speech_s": round(info.duration_after_vad or 0, 1), "text": "", "unreliable": False,
               "avg_logprob": None, "no_speech_prob": None, "skipped": False, "stopped": ""}
        suspect = info.language in SKIP_LANGS and out["whisper_speech_s"] >= MIN_SPEECH_S
        if suspect and info.language_probability >= SKIP_SURE_P:
            out["skipped"] = True   # segments are lazy: not consuming them costs nothing
            return out
        budget = max(60.0, 4 * duration)
        if suspect:   # an unsure Kannada-family label: try, but stop at the first sign of the looping failure
            budget = min(budget, max(20.0, duration))
        texts, weighted, weight, no_speech, started = [], 0.0, 0.0, [], time.monotonic()
        for seg in segments:   # decoding happens here, one 30 s window at a time
            beat()
            if seg.text.strip():
                texts.append(seg.text.strip())
            span = max(seg.end - seg.start, 0.01)
            weighted, weight = weighted + seg.avg_logprob * span, weight + span
            no_speech.append(seg.no_speech_prob)
            if looping(" ".join(texts)):
                out["unreliable"], out["stopped"] = True, "looping output"
                break
            if time.monotonic() - started > budget:
                out["unreliable"], out["stopped"] = True, f"time budget {budget:.0f}s"
                break
        out["text"] = " ".join(texts).strip()
        if weight:
            out["avg_logprob"] = round(weighted / weight, 3)
            out["no_speech_prob"] = round(sum(no_speech) / len(no_speech), 3)
        return out


def transcribe_item(worker, item, c):
    r = worker.transcribe(load_audio(item["media"], c["duration_s"]), c["duration_s"] or 0)
    beat()
    out = Path(item["out_dir"])
    c.update(language=r["language"], language_prob=r["language_prob"], whisper_speech_s=r["whisper_speech_s"],
             transcript=None if r["skipped"] else r["text"], transcript_unreliable=r["unreliable"],
             avg_logprob=r["avg_logprob"], no_speech_prob=r["no_speech_prob"])
    apply_decision(c)
    if r["stopped"]:
        c["mode_reasons"].append(f"transcription stopped: {r['stopped']}")
    if c["transcript"]:
        write_text_atomic(out / "transcript.txt", textwrap.fill(c["transcript"], WRAP) + "\n")
    else:
        (out / "transcript.txt").unlink(missing_ok=True)
    return (f"lang={r['language']}({r['language_prob']}) mode={c['mode']} chars={len(c['transcript'] or '')} "
            f"lp={r['avg_logprob']} nsp={r['no_speech_prob']}{' SKIPPED-LANGUAGE' if r['skipped'] else ''}"
            f"{' UNRELIABLE' if r['unreliable'] else ''}")


def pass_transcribe(items, batch):
    def applies(c):
        if "classify" not in c["passes_done"]:
            return None
        return c["needs"]["transcript"] and c["kind"] != "image"

    run_pass("transcribe", items, batch, applies, Transcriber, transcribe_item)


# ---------------------------------------------------------------- pass 3: screenshots and on-screen text

def read_image(path):
    import cv2
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None:   # formats this OpenCV build cannot decode
        from PIL import Image, ImageOps
        img = np.array(ImageOps.exif_transpose(Image.open(path)).convert("RGB"))[:, :, ::-1].copy()
    edge = max(img.shape[:2])
    if edge > IMAGE_MAX_EDGE:
        scale = IMAGE_MAX_EDGE / edge
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return img


def signature(path):
    """(dHash bits, thumbnail). The hash alone cannot tell two slides that differ only by their text on one flat
    background; the thumbnail difference can."""
    import cv2
    gray = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE)
    grid = cv2.resize(gray, (17, 16), interpolation=cv2.INTER_AREA)
    thumb = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    return (grid[:, 1:] > grid[:, :-1]).ravel(), thumb


def same_picture(a, b):
    return int(np.count_nonzero(a[0] != b[0])) <= DUP_BITS and float(np.abs(a[1] - b[1]).mean()) <= DUP_MAD


def extract_frames(media, work, threads, duration=None):
    """Candidate frames: the first one, every scene cut, and at least one every FRAME_EVERY_S seconds.
    Returns [(t, path)]; showinfo reports each selected frame's timestamp in order."""
    work.mkdir(parents=True, exist_ok=True)
    scale = f"scale='if(gt(iw,ih),min({MAX_EDGE},iw),-2)':'if(gt(iw,ih),-2,min({MAX_EDGE},ih))'"
    select = f"select='eq(n,0)+gt(scene,{SCENE_CUT})+gte(t-prev_selected_t,{FRAME_EVERY_S})'"
    proc = run([tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "info", "-threads", str(threads), "-i",
                str(media), "-an", "-vf", f"{select},showinfo,{scale}", "-fps_mode", "vfr", "-frames:v",
                str(MAX_CANDIDATES), "-q:v", "3", str(work / "c%04d.jpg")], scaled(FRAMES_TIMEOUT_S, duration, 2))
    times = [float(t) for t in re.findall(r"pts_time:\s*(-?[0-9.]+)", proc.stderr.decode("utf-8", "replace"))]
    files = sorted(work.glob("c*.jpg"))
    return [(max(t, 0.0), f) for t, f in zip(times, files)]


def pick_frames(candidates):
    kept = []
    for t, path in candidates:
        sig = signature(path)
        if not any(same_picture(sig, k[2]) for k in kept):
            kept.append((t, path, sig))
    if len(kept) > MAX_FRAMES:
        kept = [kept[i] for i in sorted(set(np.linspace(0, len(kept) - 1, MAX_FRAMES).round().astype(int)))]
    return [(t, path) for t, path, _ in kept]


def normalise(text):
    """No spaces at all: RapidOCR reads the same line with different spacing in different frames."""
    return re.sub(r"[\W_]+", "", text.lower())


def seen_before(key, seen):
    return any(key == s or (abs(len(key) - len(s)) <= 3 and SequenceMatcher(None, key, s).ratio() >= 0.88)
               for s in seen)


def merge_lines(per_frame):
    """Lines repeated across frames collapse to their first appearance; near-identical OCR variants too."""
    seen, merged = [], []
    for lines in per_frame:
        for text in lines:
            key = normalise(text)
            if len(key) >= 2 and not seen_before(key, seen):
                seen.append(key)
                merged.append(text)
    return merged


class Ocr:
    def __init__(self, threads):
        import rapidocr_onnxruntime.utils as ocr_utils
        real = getattr(ocr_utils, "REAL_SESSION_OPTIONS", ocr_utils.SessionOptions)   # rebuilt after a pause
        ocr_utils.REAL_SESSION_OPTIONS = real

        def limited():   # RapidOCR 1.2.3 has no thread setting; its sessions read this module-level name
            opts = real()
            opts.intra_op_num_threads, opts.inter_op_num_threads = threads, 1
            return opts

        ocr_utils.SessionOptions = limited
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR()
        self.threads = threads

    def lines(self, img):
        result, _ = self.engine(img)
        beat()
        return [x[1].strip() for x in (result or []) if float(x[2]) >= OCR_MIN_SCORE and x[1].strip()]


def visual_item(worker, item, c):
    out = Path(item["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = out / "frames"
    if item["kind"] == "image":
        per_frame, c["frames"] = [worker.lines(read_image(item["media"]))], []
    else:
        work = out / "frames.tmp"
        shutil.rmtree(work, ignore_errors=True)
        chosen = pick_frames(extract_frames(item["media"], work, worker.threads, c["duration_s"]))
        shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir()
        c["frames"], per_frame = [], []
        for t, path in chosen:
            name = f"t{t:06.1f}s.jpg"
            if (frames_dir / name).exists():   # two frames inside the same tenth of a second
                name = f"t{t:07.2f}s.jpg"
            os.replace(path, frames_dir / name)
            c["frames"].append({"file": f"frames/{name}", "t": round(t, 2)})
            per_frame.append(worker.lines(read_image(frames_dir / name)))
        shutil.rmtree(work, ignore_errors=True)
    lines = merge_lines(per_frame)
    c["onscreen_text"] = "\n".join(lines)
    if lines:
        write_text_atomic(out / "onscreen_text.txt", c["onscreen_text"] + "\n")
    else:
        (out / "onscreen_text.txt").unlink(missing_ok=True)
    return f"frames={len(c['frames'])} ocr_lines={sum(map(len, per_frame))} kept_lines={len(lines)}"


def pass_visual(items, batch):
    def applies(c):
        if "classify" not in c["passes_done"]:
            return None
        return c["needs"]["visual"]

    run_pass("visual", items, batch, applies, Ocr, visual_item)


# ---------------------------------------------------------------- entry points

def parse_args():
    ap = argparse.ArgumentParser(description="Transcript for speech, screenshots + on-screen text for music, "
                                             "both when unclear")
    ap.add_argument("--manifest", required=True, help="JSONL: key, media, kind (video|audio|image), out_dir")
    ap.add_argument("--passes", default=",".join(PASSES), help="comma list of classify,transcribe,visual")
    ap.add_argument("--threads", default="auto", help="auto = 2 during the appliers window, 4 otherwise; or 2 | 4")
    ap.add_argument("--limit", type=int, default=0, help="only the first N manifest lines")
    ap.add_argument("--window", default="04:00-10:00", help="IST window the appliers own the machine")
    args = ap.parse_args()
    args.passes = [p.strip() for p in args.passes.split(",") if p.strip()]
    bad = [p for p in args.passes if p not in PASSES]
    if bad or args.threads != "auto" and not args.threads.isdigit():
        ap.error(f"unknown pass {bad} or --threads {args.threads!r}")
    return args


def main():
    global watchdog
    watchdog = Watchdog()  # started first so even the model imports are covered
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):   # absent under pythonw
            stream.reconfigure(encoding="utf-8", errors="replace")   # keys and OCR text are not cp1252
    args = parse_args()
    items = read_manifest(args.manifest, args.limit)
    batch = Batch(args)
    print(f"{len(items)} items; passes {','.join(args.passes)}; threads {args.threads} (now {batch.threads()}); "
          f"appliers window {clock(batch.window[0])}-{clock(batch.window[1])} IST "
          f"({'started inside it' if batch.started_in_window else 'started outside it'})", flush=True)
    runners = {"classify": pass_classify, "transcribe": pass_transcribe, "visual": pass_visual}
    for name in PASSES:
        if name in args.passes:
            runners[name](items, batch)
    print("ALL DONE", flush=True)


def supervise():
    """Run the work in a child process and restart it from the checkpoints whenever its watchdog stops it,
    so a hang never waits for a human."""
    args = parse_args()
    env = dict(os.environ, **{CHILD_ENV: "1", STARTED_ENV: "1" if in_window(parse_window(args.window)) else "0"})
    for restart in range(MAX_RESTARTS + 1):
        rc = subprocess.run([sys.executable, "-u", __file__, *sys.argv[1:]], env=env).returncode
        if rc != WATCHDOG_EXIT:
            return rc
        if restart < MAX_RESTARTS:
            print(f"restart {restart + 1}/{MAX_RESTARTS}: resuming from the checkpoints", flush=True)
    print(f"watchdog stopped the run {MAX_RESTARTS + 1} times; giving up", flush=True)
    return WATCHDOG_EXIT


if __name__ == "__main__":
    if os.environ.get(CHILD_ENV):
        main()
    else:
        sys.exit(supervise())
