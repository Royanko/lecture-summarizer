"""
BGU Lecture Summarizer — FastAPI Backend

Run:
    cd backend
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import concurrent.futures
import ctypes
import datetime
import json as _json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from typing import Literal

# ── Windows sleep prevention ───────────────────────────────────────────────────
# ES_CONTINUOUS | ES_SYSTEM_REQUIRED — tells Windows not to sleep while set.
_ES_CONTINUOUS      = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

def _prevent_sleep() -> None:
    """Prevent Windows from sleeping during a long-running job."""
    if platform.system() == "Windows":
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
            )
        except Exception:
            pass  # Non-fatal — worst case: laptop sleeps

def _allow_sleep() -> None:
    """Re-enable normal Windows sleep behaviour after a job finishes."""
    if platform.system() == "Windows":
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)  # type: ignore[attr-defined]
        except Exception:
            pass

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Environment ────────────────────────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).parent.parent
_env_file = _ROOT / ".env"
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=_env_file, override=True)
except ImportError:
    if _env_file.exists():
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from google import genai
from google.genai import types

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="BGU Lecture Summarizer API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept", "Origin", "X-Requested-With"],
    max_age=600,
)

# ── Constants ──────────────────────────────────────────────────────────────────
SKILLS_DIR         = _ROOT / "skills"
RECORDINGS_DIR     = _ROOT / "recordings"
SUMMARIES_DIR      = _ROOT / "summaries"
TRANSCRIPTIONS_DIR = _ROOT / "transcriptions"

AVAILABLE_MODELS: list[str] = ["gemini-2.5-flash", "gemini-2.5-pro"]

MIME_MAP: dict[str, str] = {
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}

LANG_HINTS: dict[str, str] = {
    "he": "The lecture is in Hebrew. Transcribe verbatim in Hebrew only.",
    "en": "The lecture is in English. Transcribe verbatim in English only.",
    "ar": "The lecture is in Arabic. Transcribe verbatim in Arabic only.",
}

def _load_summary_prompt() -> str:
    path = SKILLS_DIR / "summary_skill.txt"
    if not path.exists():
        raise FileNotFoundError(f"Summary skill file not found at {path}")
    return path.read_text(encoding="utf-8")


SUMMARY_SYSTEM_PROMPT: str = _load_summary_prompt()

COURSES: dict[str, str] = {
    "combinatorics":    "קומבינטוריקה — Combinatorics",
    "data_structures":  "מבני נתונים — Data Structures",
    "calculus":         'חדו"א — Calculus',
    "linear_algebra_2": "אלגברה לינארית 2 — Linear Algebra 2",
}

for _course_dir in COURSES:
    for _base in [RECORDINGS_DIR, SUMMARIES_DIR, TRANSCRIPTIONS_DIR]:
        (_base / _course_dir).mkdir(parents=True, exist_ok=True)

_jobs: dict[str, dict] = {}

# ── Timeout configuration ──────────────────────────────────────────────────────
# These are hard wall-clock timeouts enforced by a background thread.
# The Gemini SDK does not set a default timeout — without this, any
# hung network call blocks the background job thread forever.

UPLOAD_TIMEOUT_S      = 600   # 10 min  — large files can take time to upload
TRANSCRIBE_TIMEOUT_S  = 600   # 10 min  — long audio segments to transcribe
SUMMARIZE_TIMEOUT_S   = 180   #  3 min  — text-only generation is faster
MASTER_TIMEOUT_S      = 300   #  5 min  — aggregates many segment summaries

# Module-level thread pool for Gemini SDK calls.
# Using a persistent pool avoids the ThreadPoolExecutor shutdown-wait bug
# (executor.__exit__ blocks until workers finish, defeating the timeout).
_gemini_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="gemini",
)


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _log(msg: str) -> None:
    """Print a timestamped line to stdout, flushed immediately."""
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _timed(label: str, fn, *args, timeout: int, **kwargs):
    """
    Run fn(*args, **kwargs) in the shared thread pool with a hard timeout.

    If the call does not return within `timeout` seconds, raises TimeoutError.
    The underlying thread keeps running (the Gemini SDK's HTTP connection
    cannot be forcibly closed from the outside), but our calling code
    unblocks and can proceed — e.g. skip the segment or mark an error.

    Logs the start time, finish time, and elapsed seconds for every call.
    """
    _log(f"    ▶ START  {label}")
    t0 = time.monotonic()
    future = _gemini_pool.submit(fn, *args, **kwargs)
    try:
        result = future.result(timeout=timeout)
        elapsed = time.monotonic() - t0
        _log(f"    ✓ DONE   {label}  ({elapsed:.1f}s)")
        return result
    except concurrent.futures.TimeoutError:
        elapsed = time.monotonic() - t0
        _log(f"    ✗ TIMEOUT {label}  ({elapsed:.1f}s > {timeout}s limit)")
        raise TimeoutError(f"'{label}' timed out after {timeout}s")


# ══════════════════════════════════════════════════════════════════════════════
# UTILITY
# ══════════════════════════════════════════════════════════════════════════════

def _load_tutor_prompt() -> str:
    # Prefer the interactive chat skill; fall back to the legacy file if absent.
    for name in ("tutor_chat_skill.txt", "tutor_skill.txt"):
        path = SKILLS_DIR / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No tutor skill file found in {SKILLS_DIR}")


def _strip_correction_sections(text: str) -> str:
    if not text:
        return text
    text = re.sub(
        r'(?:^|\n)#{1,4}\s*(?:תיקון תמלול[^\n]*).*?(?=\n#{1,4}\s|\Z)',
        "\n", text, flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r'\*\*(?:תיקון תמלול[^\*]*)\*\*.*?(?=\*\*|\n#{1,4}|\Z)',
        "", text, flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r'(?:^|\n)(?:תיקון תמלול גולמי|raw transcript correction)[^\n]*\n?',
        "\n", text, flags=re.IGNORECASE,
    )
    return text.strip()


# ── Semantic tag recovery ─────────────────────────────────────────────────────
# Gemini sometimes outputs the heading format (e.g. #### 🔵 הגדרה: X) but
# drops the required [DEFINITION] tag line. The frontend parser needs the tag
# to render semantic cards, so we post-process to inject missing tags.

_HEADING_LABEL_TO_TAG: dict[str, str] = {
    "הגדרה":  "DEFINITION",
    "משפט":   "THEOREM",
    "טענה":   "CLAIM",
    "הוכחה":  "PROOF",
    "תרגיל":  "EXERCISE",
    "הערה":   "NOTE",
}

_ALL_TAGS_ALTERNATION = r"DEFINITION|THEOREM|CLAIM|PROOF|EXERCISE|NOTE"

def _ensure_semantic_tags(text: str) -> str:
    """Insert missing [TAG] lines after heading markers that contain a label keyword."""
    if not text:
        return text
    for keyword, tag in _HEADING_LABEL_TO_TAG.items():
        pattern = re.compile(
            r'(^####\s+[^\n]*' + re.escape(keyword) + r'[^\n]*\n)'
            r'(?!\s*\[(?:' + _ALL_TAGS_ALTERNATION + r')\])',
            re.MULTILINE,
        )
        text = pattern.sub(rf'\g<1>[{tag}]\n', text)
    return text


def _get_client(api_key: str) -> genai.Client:
    key = api_key or os.getenv("GOOGLE_API_KEY", "")
    if not key:
        raise ValueError("No Gemini API key. Set GOOGLE_API_KEY in backend/.env")
    return genai.Client(api_key=key)


# ══════════════════════════════════════════════════════════════════════════════
# CORE AI FUNCTIONS  (with full logging + timeouts)
# ══════════════════════════════════════════════════════════════════════════════

def split_video(input_path: str, output_dir: str, chunk_sec: int) -> list[str]:
    ext     = os.path.splitext(input_path)[1]
    pattern = os.path.join(output_dir, f"seg_%03d{ext}")
    _log(f"  ffmpeg splitting → {chunk_sec}s chunks, output: {output_dir}")
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", input_path, "-c", "copy", "-map", "0",
             "-segment_time", str(chunk_sec), "-f", "segment",
             "-reset_timestamps", "1", pattern],
            capture_output=True, text=True, timeout=600,
        )
        _log(f"  ffmpeg exit code: {result.returncode}")
        if result.stderr:
            _log(f"  ffmpeg stderr (last 300 chars): {result.stderr[-300:]}")
        if result.returncode == 0:
            chunks = sorted(
                os.path.join(output_dir, f)
                for f in os.listdir(output_dir)
                if f.startswith("seg_") and f.endswith(ext)
            )
            if chunks:
                _log(f"  ffmpeg produced {len(chunks)} chunk(s)")
                return chunks
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _log(f"  ffmpeg error: {e}")
    _log("  ffmpeg fallback: treating entire file as one chunk")
    return [input_path]


def _do_upload(client: genai.Client, file_path: str, file_name: str, mime_type: str):
    """The actual SDK call — run via _timed() so it has a hard timeout."""
    return client.files.upload(
        file=file_path,
        config=types.UploadFileConfig(display_name=file_name, mime_type=mime_type),
    )


def _do_generate(client: genai.Client, model: str, contents, config=None):
    """The actual generate_content SDK call — run via _timed()."""
    kwargs = dict(model=model, contents=contents)
    if config is not None:
        kwargs["config"] = config
    return client.models.generate_content(**kwargs)


def transcribe_segment(
    client: genai.Client,
    file_path: str,
    file_name: str,
    mime_type: str,
    lang_hint: str,
    model: str,
) -> str:
    file_mb = os.path.getsize(file_path) / 1_048_576
    _log(f"    File size: {file_mb:.1f} MB  |  MIME: {mime_type}")

    # ── 1. Upload file to Gemini Files API ────────────────────────────────────
    _log("    Step 1/4: Uploading to Gemini Files API…")
    gf = _timed(
        "files.upload", _do_upload,
        client, file_path, file_name, mime_type,
        timeout=UPLOAD_TIMEOUT_S,
    )
    _log(f"    Uploaded → name={gf.name}  state={gf.state.name}")

    # ── 2. Poll until ACTIVE ──────────────────────────────────────────────────
    _log("    Step 2/4: Waiting for Gemini to process file…")
    poll = 0
    while gf.state.name == "PROCESSING":
        time.sleep(4)
        poll += 1
        _log(f"      poll #{poll}: state={gf.state.name}")
        gf = client.files.get(name=gf.name)
        if poll > 75:
            try:
                client.files.delete(name=gf.name)
            except Exception:
                pass
            raise TimeoutError(f"File stuck in PROCESSING after {poll * 4}s: {file_name}")

    _log(f"    File state after polling: {gf.state.name}")
    if gf.state.name != "ACTIVE":
        raise RuntimeError(f"File processing failed (state={gf.state.name}): {file_name}")

    # ── 3. Extract frames (video only) ────────────────────────────────────────
    frame_parts: list[types.Part] = []
    frames_dir: str | None = None
    is_video = mime_type.startswith("video/")
    if is_video:
        _log("    Step 3a/4: Extracting frames every 2 minutes…")
        try:
            frames_dir = tempfile.mkdtemp(prefix="bgu_frames_")
            frame_pattern = os.path.join(frames_dir, "frame_%04d.jpg")
            ffmpeg_result = subprocess.run(
                ["ffmpeg", "-i", file_path, "-vf", "fps=1/120", "-q:v", "2", frame_pattern],
                capture_output=True, text=True, timeout=120,
            )
            if ffmpeg_result.returncode == 0:
                frame_files = sorted(
                    os.path.join(frames_dir, f)
                    for f in os.listdir(frames_dir)
                    if f.startswith("frame_") and f.endswith(".jpg")
                )
                for ff in frame_files:
                    with open(ff, "rb") as fh:
                        frame_parts.append(
                            types.Part.from_bytes(data=fh.read(), mime_type="image/jpeg")
                        )
                _log(f"    Extracted {len(frame_parts)} frame(s) for Gemini")
            else:
                _log(f"    Frame extraction failed (exit {ffmpeg_result.returncode}) — continuing without frames")
        except Exception as frame_exc:
            _log(f"    Frame extraction error (non-fatal): {frame_exc} — continuing without frames")
    else:
        _log("    Step 3a/4: Audio-only file — skipping frame extraction")

    # ── 4. Transcribe ─────────────────────────────────────────────────────────
    _log("    Step 3b/4: Calling generate_content for transcription…")
    prompt = (
        "You are transcribing a university lecture. You have access to the video and periodic screenshots.\n\n"
        "Transcribe everything spoken in the lecture completely and accurately.\n"
        "- Fix obvious speech-to-text errors using context\n"
        "- For content visible on the board/slides in the screenshots, add: [BOARD: <exact content>]\n"
        "- For mathematical content, use LaTeX notation: $formula$ for inline, $$formula$$ for block\n"
        "- Include timestamps in format [MM:SS] at the start of each new topic or every ~2 minutes\n"
        f"- {lang_hint}\n\n"
        "Output only the transcript. No commentary or meta-text."
    )
    contents = [types.Content(role="user", parts=[
        types.Part.from_uri(file_uri=gf.uri, mime_type=gf.mime_type),
        *frame_parts,
        types.Part(text=prompt),
    ])]
    resp = _timed(
        "generate_content(transcribe)", _do_generate,
        client, model, contents,
        timeout=TRANSCRIBE_TIMEOUT_S,
    )
    transcript_len = len(resp.text) if resp.text else 0
    _log(f"    Transcription received: {transcript_len} chars")

    # ── 5. Clean up local frames ──────────────────────────────────────────────
    if frames_dir and os.path.exists(frames_dir):
        shutil.rmtree(frames_dir, ignore_errors=True)
        _log("    Local frames deleted OK")

    # ── 6. Delete remote file ─────────────────────────────────────────────────
    _log("    Step 4/4: Deleting remote file…")
    try:
        client.files.delete(name=gf.name)
        _log("    Remote file deleted OK")
    except Exception as del_exc:
        _log(f"    Remote file delete failed (non-fatal): {del_exc}")

    return resp.text


def summarize_segment(
    transcript: str,
    language: str = "Hebrew",
    api_key: str = "",
    model: str = "gemini-2.5-flash",
) -> str:
    """Summarize a single transcript segment using Gemini."""
    if not transcript or not transcript.strip():
        return ""
    _log(f"    Summarising {len(transcript)} chars via Gemini…")
    try:
        client = _get_client(api_key)
        resp = _timed(
            "generate_content(summarize)", _do_generate,
            client, model,
            f"להלן תמלול הרצאה אקדמית. צור סיכום מקיף לפי ההנחיות.\n\nתמלול:\n{transcript}",
            types.GenerateContentConfig(system_instruction=SUMMARY_SYSTEM_PROMPT),
            timeout=SUMMARIZE_TIMEOUT_S,
        )
        text = resp.text or ""
        if not text.strip():
            _log("    Summary response was empty — returning placeholder")
            return "[הסיכום לא הוחזר מה-API. ייתכן שהתגובה נחסמה או הייתה ריקה. נסה שנית.]"
        _log(f"    Summary received: {len(text)} chars")
        text = _strip_correction_sections(text)
        text = _ensure_semantic_tags(text)
        return text
    except Exception as e:
        return f"[Summarization error: {e}]"


def generate_master_summary(
    chunk_summaries: list[dict],
    language: str = "Hebrew",
    api_key: str = "",
    model: str = "gemini-2.5-flash",
) -> str:
    """Generate a master summary from all chunk summaries using Gemini."""
    if not chunk_summaries:
        return ""
    combined = "\n\n---\n\n".join([
        f"### חלק {i + 1}\n{c.get('summary', '')}"
        for i, c in enumerate(chunk_summaries)
        if c.get("summary")
    ])
    _log(f"  Generating master summary from {len(chunk_summaries)} segment(s) via Gemini…")
    try:
        client = _get_client(api_key)
        resp = _timed(
            "generate_content(master_summary)", _do_generate,
            client, model,
            (
                "להלן סיכומי הסגמנטים של הרצאה אקדמית שלמה. "
                "אחד אותם לסיכום מאסטר אחד שמכיל:\n"
                "1. סקירה כללית של כל ההרצאה (5-7 משפטים)\n"
                "2. רצף מלא של כל יחידות התוכן (הגדרות, משפטים, הוכחות, תרגילים) לפי סדרן\n"
                "3. גלוסרי מאוחד של כל המונחים\n"
                "4. 8-10 שאלות חזרה על כל ההרצאה\n\n"
                f"{combined}"
            ),
            types.GenerateContentConfig(system_instruction=SUMMARY_SYSTEM_PROMPT),
            timeout=MASTER_TIMEOUT_S,
        )
        text = resp.text or ""
        if not text.strip():
            _log("  Master summary response was empty — returning concatenated fallback")
            return "\n\n---\n\n".join(
                f"**חלק {c['idx'] + 1}**\n{c.get('summary', '')}"
                for c in chunk_summaries
            )
        _log(f"  Master summary: {len(text)} chars")
        text = _strip_correction_sections(text)
        text = _ensure_semantic_tags(text)
        return text
    except Exception as e:
        return f"[Master summary error: {e}]"


def split_text_into_segments(text: str, words_per_chunk: int = 2500) -> list[str]:
    """
    Split a plain-text transcript into roughly equal chunks at paragraph
    boundaries.  Tries to keep each chunk close to `words_per_chunk` words.
    Falls back to the whole text as one segment if it cannot be split.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for para in paragraphs:
        para_words = len(para.split())
        # Start a new chunk when adding this paragraph would exceed the limit
        # — but only if we already have something in the current chunk.
        if current_words + para_words > words_per_chunk and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_words = para_words
        else:
            current.append(para)
            current_words += para_words

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text.strip()]


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

KB_TIMEOUT_S = 300  # 5 min — unified build call can be long

_KB_TAG_LABEL: dict[str, str] = {
    "DEFINITION": "הגדרה",
    "THEOREM":    "משפט",
    "CLAIM":      "טענה",
    "PROOF":      "הוכחה",
    "EXERCISE":   "תרגיל",
    "NOTE":       "הערה",
}

_KB_TAG_ORDER = ["DEFINITION", "THEOREM", "CLAIM", "PROOF", "EXERCISE", "NOTE"]

_BLOCK_RE = re.compile(
    r'(?:#{1,4}[^\S\n]*([^\n]+)\n+[^\S\n]*)?\[(DEFINITION|THEOREM|CLAIM|PROOF|EXERCISE|NOTE)\]',
    re.IGNORECASE,
)

# ── Skill loader ───────────────────────────────────────────────────────────────

def _load_kb_builder_prompt() -> str:
    path = SKILLS_DIR / "kb_builder_skill.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"kb_builder_skill.txt not found in {SKILLS_DIR}")


# ── Step 1: Extract raw blocks ─────────────────────────────────────────────────

def _extract_blocks(text: str) -> list[dict]:
    """Extract all semantic tag blocks from a summary markdown string."""
    hits = []
    for m in _BLOCK_RE.finditer(text):
        heading   = (m.group(1) or "").strip()
        tag_type  = m.group(2).upper()
        colon_idx = heading.find(":")
        subtitle  = heading[colon_idx + 1:].strip() if colon_idx >= 0 \
                    else re.sub(r'^[^\u05d0-\u05eaa-zA-Z0-9(]+', '', heading).strip()
        hits.append({"start": m.start(), "end": m.end(), "tag_type": tag_type, "subtitle": subtitle})

    blocks = []
    for i, hit in enumerate(hits):
        next_start = hits[i + 1]["start"] if i + 1 < len(hits) else len(text)
        body = re.sub(r'\n+---\s*$', '', text[hit["end"]:next_start]).strip()
        blocks.append({"type": hit["tag_type"], "subtitle": hit["subtitle"], "content": body})
    return blocks


# ── Step 2: Parse existing KB file ────────────────────────────────────────────

_KB_ITEM_RE = re.compile(
    r'<!--kb:([a-f0-9\-]+)-->\n#### ([^\n]+)\n\[(DEFINITION|THEOREM|CLAIM|PROOF|EXERCISE|NOTE)\]\n'
    r'([\s\S]*?)(?=\n<!--kb:|(?:\n---\n)|\Z)',
    re.IGNORECASE,
)

def _kb_load_existing(kb_path: pathlib.Path) -> list[dict]:
    """Parse existing KB into a flat list of item dicts via <!--kb:uuid--> anchors."""
    if not kb_path.exists():
        return []
    text  = kb_path.read_text(encoding="utf-8")
    items = []
    for m in _KB_ITEM_RE.finditer(text):
        item_id  = m.group(1)
        title    = m.group(2)
        tag_type = m.group(3).upper()
        content  = m.group(4).strip()
        preceding  = text[:m.start()]
        main_topic = sub_topic = ""
        for line in reversed(preceding.splitlines()):
            line = line.strip()
            if line.startswith("### ") and not sub_topic:
                sub_topic = line[4:].strip()
            elif line.startswith("## ") and not main_topic:
                main_topic = line[3:].strip()
            if main_topic and sub_topic:
                break
        items.append({
            "id": item_id, "tag_type": tag_type,
            "main_topic": main_topic or "כללי",
            "sub_topic":  sub_topic  or "כללי",
            "title": title, "content": content,
        })
    return items


# ── Step 3: Unified LLM build call ────────────────────────────────────────────

_KB_BUILD_USER_TEMPLATE = """\
קורס: {course_label}

## בלוקים חדשים מההרצאה האחרונה ({n_new} בלוקים):
```json
{new_blocks_json}
```

## תוכן ה-KB הקיים ({n_existing} פריטים):
{existing_section}

---
הוראות פלט:
מזג את הבלוקים החדשים לתוך ה-KB הקיים לפי כללי מערכת.
החזר מערך JSON בלבד — כל הפריטים (קיימים + חדשים/ממוזגים), ללא markdown fences.
כל פריט חייב לכלול: "id", "main_topic", "sub_topic", "tag_type", "title", "content".
- שמור על ה-id של פריטים קיימים שלא שונו.
- פריט חדש או ממוזג — השתמש ב-UUID חדש.
- tag_type חוקי: DEFINITION | THEOREM | CLAIM | PROOF | EXERCISE | NOTE
- content: Markdown תקני. LaTeX עם $...$. טבלאות עם |. בלוקי mermaid עם ```mermaid.
  כל מחרוזת JSON חייבת להיות escaped כראוי (newlines כ-\\n, quotes כ-\\").
"""

def _kb_build_with_llm(
    raw_blocks:   list[dict],
    existing:     list[dict],
    course_label: str,
    api_key:      str,
    model:        str,
) -> list[dict]:
    """
    Single LLM call using kb_builder_skill.txt as system instruction.
    Receives new raw blocks + existing KB items; returns the complete merged
    item list as a flat list of dicts.
    """
    system_prompt = _load_kb_builder_prompt()

    new_blocks_json = _json.dumps(
        [{"id": i, "tag_type": b["type"], "title": b["subtitle"], "content": b["content"]}
         for i, b in enumerate(raw_blocks)],
        ensure_ascii=False, indent=2,
    )

    if existing:
        existing_section = _json.dumps(
            [{"id": ex["id"], "tag_type": ex["tag_type"],
              "main_topic": ex["main_topic"], "sub_topic": ex["sub_topic"],
              "title": ex["title"], "content": ex["content"]}
             for ex in existing],
            ensure_ascii=False, indent=2,
        )
    else:
        existing_section = "(ריק — זוהי ההרצאה הראשונה)"

    user_msg = _KB_BUILD_USER_TEMPLATE.format(
        course_label     = course_label,
        n_new            = len(raw_blocks),
        new_blocks_json  = new_blocks_json,
        n_existing       = len(existing),
        existing_section = existing_section,
    )

    client = _get_client(api_key)
    resp   = _timed(
        "kb_build_with_llm", _do_generate,
        client, model, user_msg,
        types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.15,
        ),
        timeout=KB_TIMEOUT_S,
    )

    raw = resp.text.strip()
    raw = re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()
    items_raw = _json.loads(raw)

    # Normalise and assign UUIDs to any item missing one
    result = []
    existing_ids = {ex["id"] for ex in existing}
    for item in items_raw:
        item_id = str(item.get("id", "")).strip()
        if not item_id or item_id not in existing_ids:
            item_id = str(uuid.uuid4())
        result.append({
            "id":         item_id,
            "tag_type":   str(item.get("tag_type", "NOTE")).upper(),
            "main_topic": str(item.get("main_topic", "כללי")),
            "sub_topic":  str(item.get("sub_topic",  "כללי")),
            "title":      str(item.get("title",       "")),
            "content":    str(item.get("content",     "")),
        })
    return result


# ── Step 4: Render KB to markdown ─────────────────────────────────────────────

def _toc_anchor(text: str) -> str:
    """Generate a GitHub-style heading anchor from arbitrary text."""
    text = text.lower().strip()
    text = re.sub(r'\s+', '-', text)
    text = re.sub(r'[^\w\u0590-\u05ff\-]', '', text)
    return text


def _kb_render(items: list[dict], course_label: str) -> str:
    """
    Render the full KB from a flat item list.
    Layout:
      - File header
      - Auto-generated TOC (by topic/sub-topic)
      - Content sections (## main → ### sub → items sorted by tag order)
    Each item carries a <!--kb:uuid--> anchor for future parsing.
    """
    # ── Group ──────────────────────────────────────────────────────────────────
    tree: dict[str, dict[str, list[dict]]] = {}
    for item in items:
        mt = item.get("main_topic") or "כללי"
        st = item.get("sub_topic")  or "כללי"
        tree.setdefault(mt, {}).setdefault(st, []).append(item)

    def _tag_rank(item: dict) -> int:
        try:    return _KB_TAG_ORDER.index(item["tag_type"])
        except: return 99

    sorted_topics = sorted(tree)

    # ── Header ─────────────────────────────────────────────────────────────────
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"# 📚 ספר הידע — {course_label}\n\n",
        f"> עודכן לאחרונה: {ts}  \n",
        "> קובץ זה נוצר ומתעדכן אוטומטית עם כל הרצאה. "
        "מכיל את כל ההגדרות, המשפטים, הטענות, ההוכחות, התרגילים והערות לצורך הכנה לבחינות.\n\n",
    ]

    # ── TOC ────────────────────────────────────────────────────────────────────
    lines.append("---\n\n## 📋 תוכן עניינים\n\n")
    for mt in sorted_topics:
        mt_anchor = _toc_anchor(mt)
        n_mt = sum(len(v) for v in tree[mt].values())
        lines.append(f"- **[{mt}](#{mt_anchor})** ({n_mt} פריטים)\n")
        for st in sorted(tree[mt]):
            st_anchor = _toc_anchor(f"{mt}--{st}")
            n_st = len(tree[mt][st])
            lines.append(f"  - [{st}](#{st_anchor}) · {n_st}\n")
    lines.append("\n")

    # ── Content sections ───────────────────────────────────────────────────────
    for mt in sorted_topics:
        mt_anchor = _toc_anchor(mt)
        lines.append(f"\n---\n\n## {mt} {{#{mt_anchor}}}\n\n")
        for st in sorted(tree[mt]):
            st_anchor = _toc_anchor(f"{mt}--{st}")
            lines.append(f"### {st} {{#{st_anchor}}}\n\n")
            sorted_items = sorted(
                tree[mt][st],
                key=lambda x: (_tag_rank(x), x.get("title", ""))
            )
            for item in sorted_items:
                tag   = item["tag_type"]
                title = item["title"]
                label = _KB_TAG_LABEL.get(tag, tag)
                lines.append(f"<!--kb:{item['id']}-->\n")
                lines.append(f"#### {label}: {title}\n")
                lines.append(f"[{tag}]\n")
                lines.append(f"{item['content']}\n\n")

    return "".join(lines)


# ── Orchestrator ───────────────────────────────────────────────────────────────

def update_knowledge_base(
    course_dir: str,
    file_name:  str,
    master:     str,
    api_key:    str,
    model:      str,
) -> str:
    """
    KB update pipeline (kb_builder_skill.txt-driven):
      1. Extract semantic blocks from master summary
      2. Load existing KB items
      3. Single LLM call: merge new blocks into existing KB using kb_builder_skill
      4. Re-render full KB with TOC and save
    Falls back to simple append on any error.
    """
    kb_path      = SUMMARIES_DIR / course_dir / "knowledge_base.md"
    course_label = COURSES.get(course_dir, course_dir)

    # ── 1. Extract ─────────────────────────────────────────────────────────────
    raw_blocks = _extract_blocks(master)
    if not raw_blocks:
        _log("  [KB] No semantic blocks found in master summary — skipping")
        return str(kb_path)
    _log(f"  [KB] Extracted {len(raw_blocks)} block(s) from master summary")

    try:
        # ── 2. Load existing ────────────────────────────────────────────────────
        existing = _kb_load_existing(kb_path)
        _log(f"  [KB] Existing KB: {len(existing)} item(s)")

        # ── 3. Unified LLM build ────────────────────────────────────────────────
        _log("  [KB] Running kb_builder_skill merge via LLM…")
        final_items = _kb_build_with_llm(raw_blocks, existing, course_label, api_key, model)
        _log(f"  [KB] Merged result: {len(final_items)} total item(s)")

        # ── 4. Render & save ────────────────────────────────────────────────────
        kb_content = _kb_render(final_items, course_label)
        kb_path.write_text(kb_content, encoding="utf-8")
        _log(f"  [KB] Saved → {kb_path}")

    except Exception as exc:
        _log(f"  [KB] ⚠ Pipeline error ({type(exc).__name__}: {exc}) — simple append fallback")
        _kb_simple_append(kb_path, course_label, file_name, raw_blocks)

    return str(kb_path)


def _kb_simple_append(
    kb_path:      pathlib.Path,
    course_label: str,
    file_name:    str,
    blocks:       list[dict],
) -> None:
    """Fallback: append raw blocks under a timestamped lecture heading."""
    if not kb_path.exists():
        kb_path.write_text(
            f"# 📚 ספר הידע — {course_label}\n\n"
            "> קובץ זה נוצר אוטומטית ומתעדכן עם כל הרצאה.\n",
            encoding="utf-8",
        )
    ts           = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lecture_name = re.sub(r'[^\w\-. ]', '_', os.path.splitext(file_name)[0])[:60]
    lines        = [f"\n\n---\n\n## 📅 הרצאה: {lecture_name} — {ts}\n\n"]
    for block in blocks:
        item_id = str(uuid.uuid4())
        label   = _KB_TAG_LABEL.get(block["type"], block["type"])
        title   = block["subtitle"] or label
        lines.append(f"<!--kb:{item_id}-->\n#### {label}: {title}\n[{block['type']}]\n{block['content']}\n\n")
    with open(kb_path, "a", encoding="utf-8") as f:
        f.write("".join(lines))


def save_outputs(course_dir: str, base_name: str, master: str, transcript: str) -> tuple[str, str]:
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = re.sub(r'[^\w\-.]', '_', os.path.splitext(base_name)[0])[:40]
    s_path = str(SUMMARIES_DIR      / course_dir / f"{ts}_{name}.txt")
    t_path = str(TRANSCRIPTIONS_DIR / course_dir / f"{ts}_{name}.txt")
    with open(s_path, "w", encoding="utf-8") as f:
        f.write(master)
    with open(t_path, "w", encoding="utf-8") as f:
        f.write(transcript)
    return s_path, t_path


# ══════════════════════════════════════════════════════════════════════════════
# PER-SEGMENT CHECKPOINT SYSTEM
# Checkpoints are written after each segment so that a resume run can skip
# already-completed segments without re-uploading to Gemini.
#
# Layout on disk:
#   transcriptions/<course>/_wip_<sanitized_basename>/
#       meta.json               ← job parameters + total segment count
#       seg_000_transcript.txt  ← segment 0 transcript
#       seg_000_summary.txt     ← segment 0 summary
#       seg_001_transcript.txt
#       …
#
# The WIP directory is deleted automatically on job success.
# ══════════════════════════════════════════════════════════════════════════════

def _wip_dir(course_dir: str, base_name: str) -> pathlib.Path:
    """Return the deterministic WIP directory path for a given file."""
    name = re.sub(r'[^\w\-.]', '_', os.path.splitext(base_name)[0])[:40]
    return TRANSCRIPTIONS_DIR / course_dir / f"_wip_{name}"


def _wip_write_meta(
    course_dir: str, base_name: str, total_segments: int,
    model: str, lang: str, mime_type: str, chunk_minutes: int,
) -> None:
    d = _wip_dir(course_dir, base_name)
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "file_name":      base_name,
        "mime_type":      mime_type,
        "total_segments": total_segments,
        "model":          model,
        "lang":           lang,
        "course_dir":     course_dir,
        "chunk_minutes":  chunk_minutes,
        "created_at":     datetime.datetime.now().isoformat(),
    }
    (d / "meta.json").write_text(_json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _wip_write_checkpoint(
    course_dir: str, base_name: str, seg_idx: int,
    transcript: str, summary: str,
) -> None:
    d = _wip_dir(course_dir, base_name)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"seg_{seg_idx:03d}_transcript.txt").write_text(transcript, encoding="utf-8")
    (d / f"seg_{seg_idx:03d}_summary.txt").write_text(summary,    encoding="utf-8")


def _wip_read_checkpoint(
    course_dir: str, base_name: str, seg_idx: int,
) -> tuple[str, str] | None:
    """Return (transcript, summary) if a checkpoint exists, else None."""
    d = _wip_dir(course_dir, base_name)
    t_file = d / f"seg_{seg_idx:03d}_transcript.txt"
    s_file = d / f"seg_{seg_idx:03d}_summary.txt"
    if t_file.exists() and s_file.exists():
        return t_file.read_text(encoding="utf-8"), s_file.read_text(encoding="utf-8")
    return None


def _wip_cleanup(course_dir: str, base_name: str) -> None:
    """Remove the WIP directory after a successful job."""
    d = _wip_dir(course_dir, base_name)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _wip_list_resumable(course_dir: str) -> list[dict]:
    """Scan the course transcriptions dir for WIP directories and return metadata."""
    base = TRANSCRIPTIONS_DIR / course_dir
    result: list[dict] = []
    if not base.exists():
        return result
    for entry in base.iterdir():
        if not (entry.is_dir() and entry.name.startswith("_wip_")):
            continue
        meta_file = entry / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = _json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        done = sum(1 for _ in entry.glob("seg_*_transcript.txt"))
        result.append({
            "file_name":      meta.get("file_name", entry.name[5:]),
            "wip_name":       entry.name[5:],   # strip "_wip_" prefix
            "done_segments":  done,
            "total_segments": meta.get("total_segments", 0),
            "model":          meta.get("model", "gemini-2.5-flash"),
            "lang":           meta.get("lang", "he"),
            "course_dir":     course_dir,
            "chunk_minutes":  meta.get("chunk_minutes", 20),
            "created_at":     meta.get("created_at", ""),
        })
    return result


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND JOB
# ══════════════════════════════════════════════════════════════════════════════

def _run_processing_job(
    job_id: str,
    tmp_path: str,
    file_name: str,
    mime_type: str,
    model: str,
    lang: str,
    api_key: str,
    test_mode: bool,
    chunk_minutes: int,
    course_dir: str,
) -> None:
    def update(progress: str) -> None:
        _jobs[job_id]["progress"] = progress
        _log(f"[job {job_id[:8]}] {progress}")

    _log(f"\n{'='*70}")
    _log(f"JOB START  id={job_id}")
    _log(f"  file={file_name}  model={model}  lang={lang}  test={test_mode}  course={course_dir}")
    _log(f"{'='*70}")

    _jobs[job_id]["status"] = "processing"
    chunk_dir: str | None = None
    failed_segments: list[int] = []

    _prevent_sleep()
    try:
        client    = _get_client(api_key)
        lang_hint = LANG_HINTS.get(lang, LANG_HINTS["he"])
        chunk_sec = chunk_minutes * 60

        # ── Split ─────────────────────────────────────────────────────────────
        update("✂️ Splitting into segments…")
        chunk_dir = tempfile.mkdtemp(prefix="bgu_segs_")
        chunks    = split_video(tmp_path, chunk_dir, chunk_sec)
        n         = len(chunks)

        if test_mode:
            chunks    = chunks[:1]
            n_display = n
        else:
            n_display = n

        _log(f"  Segments to process: {len(chunks)} (total detected: {n_display})")
        update(f"📊 {n_display} segment(s) detected — starting analysis…")

        _jobs[job_id]["chunks"]         = []
        _jobs[job_id]["total_segments"] = n_display

        # Write job metadata so a resume run can identify this WIP directory.
        if not test_mode:
            _wip_write_meta(course_dir, file_name, n_display, model, lang, mime_type, chunk_minutes)

        chunk_summaries: list[dict] = []
        full_transcript             = ""

        # ── Per-segment loop ──────────────────────────────────────────────────
        for i, chunk_path in enumerate(chunks):
            seg_num   = i + 1
            total_seg = len(chunks)
            base, ext = os.path.splitext(file_name)
            chunk_name = f"{base}_seg{seg_num:03d}{ext}"

            _log(f"\n{'-'*60}")
            _log(f"SEGMENT {seg_num}/{total_seg}")
            _log(f"  path : {chunk_path}")
            _log(f"  name : {chunk_name}")
            _log(f"  size : {os.path.getsize(chunk_path) / 1_048_576:.1f} MB")

            # ── Checkpoint check (resume path) ────────────────────────────────
            cached = _wip_read_checkpoint(course_dir, file_name, i) if not test_mode else None
            if cached is not None:
                transcript, summary = cached
                _log(f"  ✓ CHECKPOINT HIT seg {seg_num} — loaded from disk, skipping API calls")
                update(f"✅ Segment {seg_num}/{total_seg} (resumed from checkpoint)")
                chunk_entry = {"idx": i, "transcript": transcript, "summary": summary}
                chunk_summaries.append(chunk_entry)
                full_transcript += f"\n\n--- Segment {seg_num} ---\n\n{transcript}"
                _jobs[job_id]["chunks"] = list(chunk_summaries)
                continue

            # ── Transcribe ────────────────────────────────────────────────────
            update(f"🎙️ Transcribing segment {seg_num}/{total_seg}…")
            _log(f"  [transcription start]")
            try:
                transcript = transcribe_segment(
                    client, chunk_path, chunk_name, mime_type, lang_hint, model
                )
                _log(f"  [transcription end]  {len(transcript)} chars")
            except Exception as exc:
                _log(f"  ✗ TRANSCRIPTION FAILED for segment {seg_num}")
                _log(f"    {type(exc).__name__}: {exc}")
                _log(traceback.format_exc())
                update(f"⚠️ Segment {seg_num}/{total_seg} transcription failed — skipping")
                failed_segments.append(seg_num)
                continue   # ← skip to next segment, do NOT freeze

            # ── Summarise ─────────────────────────────────────────────────────
            update(f"🤖 Summarising segment {seg_num}/{total_seg}…")
            _log(f"  [summarisation start]")
            try:
                summary = summarize_segment(transcript, lang, api_key=api_key, model=model)
                _log(f"  [summarisation end]  {len(summary)} chars")
            except Exception as exc:
                _log(f"  ✗ SUMMARISATION FAILED for segment {seg_num}")
                _log(f"    {type(exc).__name__}: {exc}")
                _log(traceback.format_exc())
                # Fallback: use first 500 chars of transcript as summary
                summary = transcript[:500] + "\n\n[סיכום אוטומטי נכשל — נא לעיין בתמלול]"
                _log(f"  Using transcript excerpt as fallback summary")

            # ── Write checkpoint ──────────────────────────────────────────────
            if not test_mode:
                _wip_write_checkpoint(course_dir, file_name, i, transcript, summary)
                _log(f"  Checkpoint written for seg {seg_num}")

            # ── Publish chunk ─────────────────────────────────────────────────
            chunk_entry = {"idx": i, "transcript": transcript, "summary": summary}
            chunk_summaries.append(chunk_entry)
            full_transcript += f"\n\n--- Segment {seg_num} ---\n\n{transcript}"

            _jobs[job_id]["chunks"] = list(chunk_summaries)
            update(f"✅ Segment {seg_num}/{total_seg} ready")
            _log(f"  Chunk published. Total published: {len(chunk_summaries)}")

        # ── End of loop summary ───────────────────────────────────────────────
        _log(f"\n{'-'*60}")
        _log(f"LOOP COMPLETE: {len(chunk_summaries)} succeeded, {len(failed_segments)} failed")
        if failed_segments:
            _log(f"  Failed segments: {failed_segments}")

        if not chunk_summaries:
            raise RuntimeError(
                f"All {len(chunks)} segment(s) failed. Check the logs above for details."
            )

        # ── Master summary ────────────────────────────────────────────────────
        if test_mode or len(chunk_summaries) == 1:
            _log("  Master summary = single segment summary (no aggregation needed)")
            master = chunk_summaries[0]["summary"]
            full_transcript = f"--- Segment 1{' (Test Mode)' if test_mode else ''} ---\n\n{chunk_summaries[0]['transcript']}"
        else:
            update("🧠 Generating master summary…")
            _log("  [master summary start]")
            try:
                master = generate_master_summary(chunk_summaries, lang, api_key=api_key, model=model)
                _log(f"  [master summary end]  {len(master)} chars")
            except Exception as exc:
                _log(f"  ✗ MASTER SUMMARY FAILED: {type(exc).__name__}: {exc}")
                _log(traceback.format_exc())
                # Fallback: concatenate segment summaries
                master = "\n\n---\n\n".join(
                    f"**Segment {c['idx'] + 1}**\n{c['summary']}"
                    for c in chunk_summaries
                )
                _log("  Using concatenated segment summaries as fallback master")

        # ── Save outputs ──────────────────────────────────────────────────────
        update("💾 Saving outputs…")
        _log("  Saving to disk…")
        s_path, t_path = save_outputs(
            course_dir, file_name, master, full_transcript.strip()
        )
        _log(f"  Summary  → {s_path}")
        _log(f"  Transcript → {t_path}")

        # ── Clean up checkpoints — only when all segments succeeded ──────────
        if not test_mode and not failed_segments:
            _wip_cleanup(course_dir, file_name)
            _log("  WIP checkpoints cleaned up")
        elif not test_mode and failed_segments:
            _log(f"  WIP checkpoints preserved — {len(failed_segments)} segment(s) failed (resumable)")

        # ── Update knowledge base ──────────────────────────────────────────────
        update("📚 Updating knowledge base…")
        kb_path = update_knowledge_base(course_dir, file_name, master, api_key, model)
        _log(f"  Knowledge base → {kb_path}")

        # ── Chat seed ─────────────────────────────────────────────────────────
        chat_seed = [
            {"role": "user",  "text": f"סיכום מאסטר של ההרצאה:\n{master}"},
            {"role": "model", "text": "קראתי את סיכום ההרצאה. אני מוכן ללמד אותך נושא אחד בכל פעם!"},
        ]

        _jobs[job_id].update({
            "status":           "done",
            "progress":         "✅ Complete" + (f" ({len(failed_segments)} segment(s) skipped)" if failed_segments else ""),
            "transcript":       full_transcript.strip(),
            "summary":          master,
            "chunks":           chunk_summaries,
            "chat_seed":        chat_seed,
            "saved_summary":    s_path,
            "saved_transcript": t_path,
            "total_segments":   n_display,
            **({"error": f"{len(failed_segments)} segment(s) failed and were skipped: {failed_segments}. You can resume the job to process the missing segments."} if failed_segments else {}),
        })

        _log(f"\n{'='*70}")
        _log(f"JOB DONE  id={job_id}")
        _log(f"{'='*70}\n")

    except Exception as exc:
        _log(f"\n{'!'*70}")
        _log(f"JOB FATAL ERROR  id={job_id}")
        _log(f"  {type(exc).__name__}: {exc}")
        _log(traceback.format_exc())
        _log(f"{'!'*70}\n")
        _jobs[job_id].update({
            "status":   "error",
            "progress": f"❌ {type(exc).__name__}: {exc}",
            "error":    str(exc),
        })

    finally:
        _allow_sleep()
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# TRANSCRIPT-ONLY BACKGROUND JOB  (fast track — no upload/transcription)
# ══════════════════════════════════════════════════════════════════════════════

def _run_transcript_job(
    job_id:     str,
    text:       str,
    file_name:  str,
    model:      str,
    lang:       str,
    api_key:    str,
    course_dir: str,
) -> None:
    """
    Summarise a plain-text transcript without any video/audio processing.
    Segments the text into ~2500-word chunks, summarises each one, then
    generates a master summary — exactly like the video pipeline but with
    the transcription step replaced by a simple text split.
    """
    def update(progress: str) -> None:
        _jobs[job_id]["progress"] = progress
        _log(f"[txt-job {job_id[:8]}] {progress}")

    _log(f"\n{'='*70}")
    _log(f"TRANSCRIPT JOB START  id={job_id}  file={file_name}  model={model}")
    _log(f"{'='*70}")

    _jobs[job_id]["status"] = "processing"

    _prevent_sleep()
    try:
        # ── Segment the text ─────────────────────────────────────────────────
        update("✂️ Splitting transcript into segments…")
        segments = split_text_into_segments(text, words_per_chunk=2500)
        n = len(segments)
        _log(f"  Text split into {n} segment(s)")
        update(f"📊 {n} segment(s) detected — starting summarisation…")

        _jobs[job_id]["chunks"]         = []
        _jobs[job_id]["total_segments"] = n

        chunk_summaries: list[dict] = []

        # ── Per-segment summarisation ─────────────────────────────────────────
        for i, seg_text in enumerate(segments):
            seg_num = i + 1
            _log(f"\n{'-'*60}")
            _log(f"SEGMENT {seg_num}/{n}  ({len(seg_text.split())} words)")

            update(f"🤖 Summarising segment {seg_num}/{n}…")
            try:
                summary = summarize_segment(seg_text, lang, api_key=api_key, model=model)
            except Exception as exc:
                _log(f"  ✗ SUMMARISATION FAILED for segment {seg_num}: {exc}")
                summary = seg_text[:500] + "\n\n[סיכום אוטומטי נכשל — נא לעיין בתמלול]"

            chunk_entry = {"idx": i, "transcript": seg_text, "summary": summary}
            chunk_summaries.append(chunk_entry)
            _jobs[job_id]["chunks"] = list(chunk_summaries)
            update(f"✅ Segment {seg_num}/{n} ready")

        # ── Master summary ────────────────────────────────────────────────────
        if len(chunk_summaries) == 1:
            master = chunk_summaries[0]["summary"]
        else:
            update("🧠 Generating master summary…")
            try:
                master = generate_master_summary(chunk_summaries, lang, api_key=api_key, model=model)
            except Exception as exc:
                _log(f"  ✗ MASTER SUMMARY FAILED: {exc}")
                master = "\n\n---\n\n".join(
                    f"**Segment {c['idx'] + 1}**\n{c['summary']}"
                    for c in chunk_summaries
                )

        # ── Save outputs ──────────────────────────────────────────────────────
        update("💾 Saving outputs…")
        s_path, t_path = save_outputs(course_dir, file_name, master, text.strip())

        chat_seed = [
            {"role": "user",  "text": f"סיכום מאסטר של ההרצאה:\n{master}"},
            {"role": "model", "text": "קראתי את סיכום ההרצאה. אני מוכן ללמד אותך נושא אחד בכל פעם!"},
        ]

        _jobs[job_id].update({
            "status":           "done",
            "progress":         "✅ Complete",
            "transcript":       text.strip(),
            "summary":          master,
            "chunks":           chunk_summaries,
            "chat_seed":        chat_seed,
            "saved_summary":    s_path,
            "saved_transcript": t_path,
            "total_segments":   n,
        })

        _log(f"\n{'='*70}")
        _log(f"TRANSCRIPT JOB DONE  id={job_id}")
        _log(f"{'='*70}\n")

    except Exception as exc:
        _log(f"\n{'!'*70}")
        _log(f"TRANSCRIPT JOB FATAL ERROR  id={job_id}: {exc}")
        _log(traceback.format_exc())
        _log(f"{'!'*70}\n")
        _jobs[job_id].update({
            "status":   "error",
            "progress": f"❌ {type(exc).__name__}: {exc}",
            "error":    str(exc),
        })

    finally:
        _allow_sleep()


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class JobStatus(BaseModel):
    job_id:           str
    status:           Literal["queued", "processing", "done", "error"]
    progress:         str              = ""
    transcript:       str | None       = None
    summary:          str | None       = None
    chunks:           list[dict] | None = None
    chat_seed:        list[dict] | None = None
    saved_summary:    str | None       = None
    saved_transcript: str | None       = None
    total_segments:   int | None       = None
    error:            str | None       = None


class ChatRequest(BaseModel):
    user_message:    str
    summary_context: str
    chat_history:    list[dict] = []
    model:           str        = "gemini-2.5-flash"
    api_key:         str        = ""


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/knowledge-base/{course_id}")
def get_knowledge_base(course_id: str):
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")
    kb_path = SUMMARIES_DIR / course_id / "knowledge_base.md"
    if not kb_path.exists():
        raise HTTPException(status_code=404, detail="not_found")
    return {
        "course_id":    course_id,
        "course_label": COURSES[course_id],
        "content":      kb_path.read_text(encoding="utf-8"),
    }


@app.get("/models")
def list_models():
    return {"models": AVAILABLE_MODELS}


@app.post("/upload", response_model=JobStatus)
async def upload_video(
    background_tasks: BackgroundTasks,
    file:          UploadFile = File(...),
    model:         str        = Form("gemini-2.5-flash"),
    lang:          str        = Form("he"),
    chunk_minutes: int        = Form(12),
    test_mode:     bool       = Form(False),
    course_dir:    str        = Form("combinatorics"),
    api_key:       str        = Form(""),
):
    suffix    = pathlib.Path(file.filename or "upload.mp4").suffix.lower()
    mime_type = MIME_MAP.get(suffix, "video/mp4")
    file_name = file.filename or f"upload{suffix}"

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        content = await file.read()
        with os.fdopen(tmp_fd, "wb") as buf:
            buf.write(content)
    except Exception as exc:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {exc}")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "queued", "progress": "Queued…"}
    _log(f"UPLOAD ACCEPTED  job={job_id}  file={file_name}  size={len(content)/1_048_576:.1f}MB")

    background_tasks.add_task(
        _run_processing_job,
        job_id=job_id, tmp_path=tmp_path, file_name=file_name,
        mime_type=mime_type, model=model, lang=lang,
        api_key=api_key or os.getenv("GOOGLE_API_KEY", ""),
        test_mode=test_mode, chunk_minutes=chunk_minutes, course_dir=course_dir,
    )

    return JobStatus(job_id=job_id, status="queued", progress="Queued…")


@app.post("/upload-transcript", response_model=JobStatus)
async def upload_transcript(
    background_tasks: BackgroundTasks,
    file:       UploadFile = File(...),
    model:      str        = Form("gemini-2.5-flash"),
    lang:       str        = Form("he"),
    course_dir: str        = Form("combinatorics"),
    api_key:    str        = Form(""),
):
    """
    Fast-track endpoint: accepts a plain-text (.txt) transcript and skips
    the upload + transcription steps entirely.  The text is segmented and
    fed directly into the summarisation pipeline.
    """
    if not file.filename or not file.filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt files are accepted by this endpoint.")

    try:
        raw_bytes = await file.read()
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read transcript: {exc}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Uploaded transcript is empty.")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "queued", "progress": "Queued…"}
    file_name = file.filename or "transcript.txt"
    _log(f"TRANSCRIPT UPLOAD ACCEPTED  job={job_id}  file={file_name}  chars={len(text)}")

    background_tasks.add_task(
        _run_transcript_job,
        job_id=job_id,
        text=text,
        file_name=file_name,
        model=model,
        lang=lang,
        api_key=api_key or os.getenv("GOOGLE_API_KEY", ""),
        course_dir=course_dir,
    )

    return JobStatus(job_id=job_id, status="queued", progress="Queued…")


@app.get("/resumable-jobs/{course_id}")
def list_resumable_jobs(course_id: str):
    """Return all in-progress (checkpointed) jobs for a given course."""
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")
    return {"jobs": _wip_list_resumable(course_id)}


@app.post("/resume", response_model=JobStatus)
async def resume_job(
    background_tasks: BackgroundTasks,
    file:          UploadFile = File(...),
    model:         str        = Form("gemini-2.5-flash"),
    lang:          str        = Form("he"),
    chunk_minutes: int        = Form(12),
    course_dir:    str        = Form("combinatorics"),
    api_key:       str        = Form(""),
):
    """
    Resume a previously interrupted job.  The uploaded file is re-split by
    ffmpeg; segments that already have on-disk checkpoints are loaded from
    disk instead of being re-sent to Gemini.  Only missing segments consume
    API tokens.
    """
    suffix    = pathlib.Path(file.filename or "upload.mp4").suffix.lower()
    mime_type = MIME_MAP.get(suffix, "video/mp4")
    file_name = file.filename or f"upload{suffix}"

    # Verify a WIP directory exists for this file before accepting the upload.
    wip = _wip_dir(course_dir, file_name)
    if not wip.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No checkpoint found for '{file_name}' in course '{course_dir}'. "
                "Please start a fresh upload."
            ),
        )

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        content = await file.read()
        with os.fdopen(tmp_fd, "wb") as buf:
            buf.write(content)
    except Exception as exc:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {exc}")

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "queued", "progress": "Queued (resuming from checkpoint)…"}
    _log(f"RESUME ACCEPTED  job={job_id}  file={file_name}  wip={wip}")

    background_tasks.add_task(
        _run_processing_job,
        job_id=job_id, tmp_path=tmp_path, file_name=file_name,
        mime_type=mime_type, model=model, lang=lang,
        api_key=api_key or os.getenv("GOOGLE_API_KEY", ""),
        test_mode=False, chunk_minutes=chunk_minutes, course_dir=course_dir,
    )

    return JobStatus(job_id=job_id, status="queued", progress="Queued (resuming from checkpoint)…")


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(job_id=job_id, **{
        k: v for k, v in job.items() if k in JobStatus.model_fields
    })


@app.post("/chat")
def chat(req: ChatRequest):
    try:
        client              = _get_client(req.api_key)
        tutor_system_prompt = _load_tutor_prompt()

        seed_history: list[dict] = [
            {"role": "user",  "text": f"סיכום מאסטר של ההרצאה:\n{req.summary_context}"},
            {"role": "model", "text": "קראתי את סיכום ההרצאה. אני מוכן ללמד אותך נושא אחד בכל פעם!"},
        ]

        chat_obj = client.chats.create(
            model=req.model,
            config=types.GenerateContentConfig(system_instruction=tutor_system_prompt),
            history=[
                types.Content(role=h["role"], parts=[types.Part(text=h["text"])])
                for h in seed_history + req.chat_history
            ],
        )
        response = chat_obj.send_message(req.user_message)
        return {"reply": response.text}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY HISTORY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

def _summary_meta_path(course_dir: str) -> pathlib.Path:
    return SUMMARIES_DIR / course_dir / ".meta.json"


def _load_summary_meta(course_dir: str) -> dict:
    p = _summary_meta_path(course_dir)
    if p.exists():
        try:
            return _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_summary_meta(course_dir: str, meta: dict) -> None:
    p = _summary_meta_path(course_dir)
    p.write_text(_json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/summaries/{course_id}")
def list_summaries(course_id: str):
    """List saved summary files for a course, newest first."""
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")

    course_dir = SUMMARIES_DIR / course_id
    meta = _load_summary_meta(course_id)
    result: list[dict] = []

    for f in sorted(course_dir.glob("*.txt"), reverse=True):
        if f.name.startswith("."):
            continue
        # Parse filename: YYYYMMDD_HHMMSS_originalname.txt
        parts = f.stem.split("_", 2)
        date_str = time_str = ""
        raw_name = f.stem
        if len(parts) >= 2 and len(parts[0]) == 8 and len(parts[1]) == 6:
            try:
                date_str = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}"
                time_str = f"{parts[1][:2]}:{parts[1][2:4]}"
            except Exception:
                pass
            raw_name = parts[2] if len(parts) > 2 else f.stem

        custom = meta.get(f.name, {})
        display_name = custom.get("display_name") or raw_name.replace("_", " ")

        # Check for matching transcript
        t_path = TRANSCRIPTIONS_DIR / course_id / f.name
        has_transcript = t_path.exists()

        result.append({
            "filename":       f.name,
            "date":           date_str,
            "time":           time_str,
            "display_name":   display_name,
            "has_transcript": has_transcript,
            "size":           f.stat().st_size,
        })

    return {
        "course_id":    course_id,
        "course_label": COURSES[course_id],
        "summaries":    result,
    }


@app.get("/summaries/{course_id}/{filename}")
def get_summary_content(course_id: str, filename: str):
    """Return the full text of a saved summary (and transcript if available)."""
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")

    s_path = SUMMARIES_DIR / course_id / filename
    if not s_path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")

    summary = s_path.read_text(encoding="utf-8")
    summary = _ensure_semantic_tags(summary)

    transcript: str | None = None
    t_path = TRANSCRIPTIONS_DIR / course_id / filename
    if t_path.exists():
        transcript = t_path.read_text(encoding="utf-8")

    meta = _load_summary_meta(course_id)
    custom = meta.get(filename, {})
    raw_parts = s_path.stem.split("_", 2)
    raw_name = raw_parts[2] if len(raw_parts) > 2 else s_path.stem
    display_name = custom.get("display_name") or raw_name.replace("_", " ")

    return {
        "filename":     filename,
        "display_name": display_name,
        "summary":      summary,
        "transcript":   transcript,
    }


@app.delete("/summaries/{course_id}/{filename}")
def delete_summary(course_id: str, filename: str):
    """Delete a saved summary (and its transcript counterpart)."""
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")

    s_path = SUMMARIES_DIR / course_id / filename
    if not s_path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")

    s_path.unlink()
    _log(f"Deleted summary: {s_path}")

    t_path = TRANSCRIPTIONS_DIR / course_id / filename
    if t_path.exists():
        t_path.unlink()
        _log(f"Deleted transcript: {t_path}")

    # Remove from meta
    meta = _load_summary_meta(course_id)
    if filename in meta:
        del meta[filename]
        _save_summary_meta(course_id, meta)

    return {"deleted": filename}


class RenameSummaryRequest(BaseModel):
    display_name: str


@app.patch("/summaries/{course_id}/{filename}")
def rename_summary(course_id: str, filename: str, req: RenameSummaryRequest):
    """Set a custom display name for a saved summary."""
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")

    s_path = SUMMARIES_DIR / course_id / filename
    if not s_path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")

    meta = _load_summary_meta(course_id)
    meta.setdefault(filename, {})["display_name"] = req.display_name.strip()
    _save_summary_meta(course_id, meta)

    return {"filename": filename, "display_name": req.display_name.strip()}
