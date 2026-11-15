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
import random
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
    load_dotenv(dotenv_path=pathlib.Path(__file__).parent / ".env", override=True)
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

DEFAULT_MODEL = "gemini-3.6-flash"
AVAILABLE_MODELS: list[str] = [DEFAULT_MODEL, "gemini-4-hydrogen", "gemini-3.5-flash-lite"]

# Gemini 2.5 models are deprecated (rerouted by Google on 2026-12-01). Old WIP
# checkpoints and browser sessions may still send them, so map them forward.
_LEGACY_MODELS: dict[str, str] = {
    "gemini-2.5-flash": DEFAULT_MODEL,
    "gemini-2.5-pro":   "gemini-4-hydrogen",
}

def _resolve_model(model: str) -> str:
    return _LEGACY_MODELS.get(model, model)

# Media up to this size is sent inline; anything larger goes through the
# Files API (Gemini Developer API: 100 MB max request, 2 GB max per file).
INLINE_MAX_BYTES = 20 * 1_048_576

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

UPLOAD_TIMEOUT_S      = 600    # 10 min  — large files can take time to upload
TRANSCRIBE_TIMEOUT_S  = 1800   # 30 min  — large media segments need it
SUMMARIZE_TIMEOUT_S   = 1800   # 30 min  — larger models + long Hebrew output can be slow
SUMMARIZE_MAX_RETRIES = 3      #  retry on timeout up to this many times before giving up
MASTER_TIMEOUT_S      = 1800   # 30 min  — aggregates many segment summaries

# Quota (429) retry — exponential backoff.
# With Google's 5 RPM free-tier limit (reduced Dec 2025), fixed short waits are
# not enough: the quota needs time to actually reset.
# Strategy: 2 min → 4 → 8 → 16 → 30 min cap, with ±10 % jitter.
_QUOTA_BACKOFF_BASE_S = 120   # first wait: 2 min
_QUOTA_BACKOFF_MAX_S  = 1800  # cap: 30 min

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


# ── Quota-error detection & smart backoff ─────────────────────────────────────

def _is_quota_error(exc: Exception) -> bool:
    """Return True when exc is a Gemini API 429 RESOURCE_EXHAUSTED response."""
    s = str(exc)
    return "429" in s or "RESOURCE_EXHAUSTED" in s.upper()


def _extract_retry_after(exc: Exception) -> float | None:
    """
    Try to read the exact wait time the API embedded in the 429 error body.
    Gemini API errors often contain {"retryDelay": "NNs"} in their JSON
    payload, and HTTP 429 responses may include a Retry-After header value.
    Returns seconds as a float, or None if nothing parseable was found.
    """
    s = str(exc)
    # gRPC body: {"retryDelay": "120s"}
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', s)
    if m:
        return float(m.group(1))
    # HTTP header: Retry-After: 120
    m = re.search(r'[Rr]etry-[Aa]fter[:\s]+(\d+)', s)
    if m:
        return float(m.group(1))
    # x-ratelimit-reset-requests: <seconds or ms>
    m = re.search(r'x-ratelimit-reset[^:]*[:\s]+(\d+(?:\.\d+)?)', s, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return val / 1000.0 if val > 10_000 else val  # convert ms → s if needed
    return None


def _quota_backoff_seconds(attempt: int, exc: Exception | None = None) -> float:
    """
    Return how many seconds to sleep before the next quota-retry.

    Priority:
    1. Retry-After / retryDelay value embedded in the error (if present).
    2. Exponential backoff: base * 2^(attempt-1), capped at _QUOTA_BACKOFF_MAX_S.
    A ±10 % jitter is applied in both cases.
    """
    if exc is not None:
        server_wait = _extract_retry_after(exc)
        if server_wait is not None:
            jitter = server_wait * random.uniform(-0.05, 0.05)
            wait = max(60.0, server_wait + jitter)
            _log(f"    ℹ️  Using server-supplied Retry-After: {server_wait:.0f}s (with jitter → {wait:.0f}s)")
            return wait

    raw = _QUOTA_BACKOFF_BASE_S * (2 ** (attempt - 1))
    capped = min(raw, _QUOTA_BACKOFF_MAX_S)
    jitter = capped * random.uniform(-0.1, 0.1)
    return capped + jitter


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


# ── LaTeX normalization ────────────────────────────────────────────────────────
# remark-math v6 rules that Gemini frequently violates:
#   • Inline math must start with $ immediately followed by a non-space character.
#     "$ x $" is NOT parsed — the dollar signs are emitted as plain text.
#   • Block math ($$...$$) must occupy its own paragraph (blank lines before & after).
#   • \[...\], \(...\), \begin{equation}...\end{equation} are unknown to remark-math.

_DISPLAY_ENV_RE = re.compile(
    r'\\begin\{(equation\*?|align\*?|aligned|gather\*?|multline\*?|eqnarray\*?)\}'
    r'([\s\S]*?)'
    r'\\end\{(?:equation\*?|align\*?|aligned|gather\*?|multline\*?|eqnarray\*?)\}',
    re.IGNORECASE,
)

def _normalize_latex(text: str) -> str:
    """
    Convert non-standard LaTeX delimiters to $...$ / $$...$$ and fix spacing
    issues that prevent remark-math from parsing the expressions.
    Safe to call multiple times (idempotent on already-normalized output).
    """
    if not text:
        return text

    # 1. \[...\]  →  block math with surrounding blank lines
    text = re.sub(
        r'\\\[([\s\S]*?)\\\]',
        lambda m: f'\n\n$$\n{m.group(1).strip()}\n$$\n\n',
        text,
    )

    # 2. \(...\)  →  inline math
    text = re.sub(
        r'\\\(([\s\S]*?)\\\)',
        lambda m: f'${m.group(1).strip()}$',
        text,
    )

    # 3. \begin{equation} / align / gather / multline  →  $$...$$
    text = _DISPLAY_ENV_RE.sub(
        lambda m: f'\n\n$$\n{m.group(2).strip()}\n$$\n\n',
        text,
    )

    # 4. Remove space immediately after opening $ or before closing $:
    #    "$ expr $" and "$ expr$" both break remark-math v6.
    text = re.sub(
        r'\$\s+([^$\n]{1,400}?)\s*\$',
        lambda m: f'${m.group(1).strip()}$',
        text,
    )

    # 5. Collapse runs of 4+ blank lines produced by replacements above
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    return text


# ── Unprotected-LaTeX detection ────────────────────────────────────────────────
# After normalization, any surviving \command outside $...$ / $$...$$ is a sign
# that Gemini forgot to wrap a formula — trigger a targeted repair call.

_RAW_MATH_CMD_RE = re.compile(
    r'(?<![`$\\])'   # not already inside math/code
    r'\\(?:'
    r'frac|binom|sqrt|sum|prod|int|oint|lim'
    r'|bigcup|bigcap|cup|cap|setminus'
    r'|forall|exists|nexists'
    r'|alpha|beta|gamma|Gamma|delta|Delta|epsilon|varepsilon'
    r'|zeta|eta|theta|Theta|iota|kappa|lambda|Lambda|mu|nu|xi|Xi'
    r'|pi|Pi|rho|sigma|Sigma|tau|upsilon|phi|Phi|chi|psi|Psi|omega|Omega'
    r'|mathbb|mathbf|mathcal|mathrm|mathit'
    r'|overline|underline|hat|widehat|tilde|vec|bar|dot|ddot'
    r'|Rightarrow|Leftarrow|Leftrightarrow|rightarrow|leftarrow'
    r'|leq|geq|neq|equiv|approx|sim|cong|propto'
    r'|subset|subseteq|supset|supseteq|in|notin'
    r'|cdot|cdots|ldots|vdots|times|div|oplus|otimes'
    r'|left|right|bigl|bigr'
    r')\b'
)

def _has_unprotected_latex(text: str) -> bool:
    """
    Return True if LaTeX math commands appear outside $...$ / $$...$$ delimiters.
    Used as a cheap gate before paying for a repair API call.
    """
    # Strip math regions so commands inside them don't fire
    stripped = re.sub(r'\$\$[\s\S]*?\$\$', '', text)
    stripped = re.sub(r'\$[^$\n]{1,500}\$', '', stripped)
    # Strip fenced code and inline code
    stripped = re.sub(r'```[\s\S]*?```', '', stripped)
    stripped = re.sub(r'`[^`\n]+`', '', stripped)
    return bool(_RAW_MATH_CMD_RE.search(stripped))


# ── LaTeX repair via Gemini ────────────────────────────────────────────────────

_LATEX_REPAIR_SYSTEM = """\
You receive a Hebrew academic summary that contains LaTeX math.
Some math expressions are incorrectly delimited: bare \\frac, \\binom, \\sum etc. \
appear outside dollar signs, or older \\[...\\] / \\(...\\) syntax is used.

Fix ONLY the LaTeX delimiter issues. Do not change any content, Hebrew text, \
structure, heading levels, or semantic tag markers such as [DEFINITION] or [THEOREM].

Rules:
- Inline math:  $expression$   (no space after opening $ or before closing $)
- Block math:   blank line → $$ → formula on its own line(s) → $$ → blank line
- Every LaTeX command (\\frac, \\binom, \\sum, Greek letters, etc.) must be inside $...$ or $$...$$
- Return the complete corrected text and nothing else — no commentary, no fences.
"""

def _repair_latex(text: str, model: str) -> str:
    """
    Ask Gemini to fix unprotected LaTeX delimiters in a summary.
    Returns the repaired text; falls back to the original on any failure.
    """
    _log("    [LaTeX repair] Bare LaTeX detected — calling Gemini to fix delimiters…")
    try:
        resp = _timed(
            "generate_content(latex_repair)", _do_generate,
            _GEMINI_CLIENT, model, text,
            types.GenerateContentConfig(
                system_instruction=_LATEX_REPAIR_SYSTEM,
                temperature=0.0,
            ),
            timeout=180,
        )
        fixed = (resp.text or "").strip()
        if not fixed:
            _log("    [LaTeX repair] Empty response — keeping original")
            return text
        _log(f"    [LaTeX repair] Done: {len(text)} → {len(fixed)} chars")
        return fixed
    except Exception as exc:
        _log(f"    [LaTeX repair] Failed ({type(exc).__name__}: {exc}) — keeping original")
        return text


def _apply_latex_fixes(text: str, model: str) -> str:
    """
    Regex-only LaTeX normalisation. The Gemini repair call has been removed
    because the summary prompt now instructs the model to emit correct
    delimiters from the start. Regex normalisation is kept as a free
    safety net for any minor delimiter drift.
    """
    return _normalize_latex(text)


# ── Gemini API client (module-level singleton) ─────────────────────────────────
# Uses the Gemini Developer API with an API key from backend/.env (or root .env).
_API_KEY = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
if not _API_KEY:
    _log(
        "[WARN] GOOGLE_API_KEY is not set. Gemini calls will fail until you add "
        "GOOGLE_API_KEY=<your key> to backend/.env (get one at https://aistudio.google.com/apikey)."
    )

_GEMINI_CLIENT = genai.Client(
    api_key=_API_KEY or "missing-api-key",
    http_options=types.HttpOptions(
        retry_options=types.HttpRetryOptions(
            initial_delay=1.0,
            attempts=3,
            # 429 is intentionally absent: quota errors need exponential backoff
            # that only our application-level code can provide.  Letting the SDK
            # fire short retries first would burn quota capacity and bypass our
            # Retry-After extraction.
            http_status_codes=[408, 500, 502, 503, 504],
        ),
        timeout=30 * 60 * 1000,   # 30 minutes
    ),
)


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


def _do_generate(client: genai.Client, model: str, contents, config=None):
    """The actual generate_content SDK call — run via _timed()."""
    kwargs = dict(model=_resolve_model(model), contents=contents)
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

    # ── 1. Prepare the media part: inline bytes, or Files API for large files ──
    uploaded = None
    if os.path.getsize(file_path) <= INLINE_MAX_BYTES:
        _log("    Step 1/3: Reading file into memory for inline content…")
        with open(file_path, "rb") as fh:
            media_part = types.Part.from_bytes(data=fh.read(), mime_type=mime_type)
    else:
        _log("    Step 1/3: Uploading file via the Files API…")
        uploaded = _timed(
            "files.upload", client.files.upload,
            file=file_path,
            config=types.UploadFileConfig(mime_type=mime_type),
            timeout=UPLOAD_TIMEOUT_S,
        )
        deadline = time.monotonic() + UPLOAD_TIMEOUT_S
        while uploaded.state and uploaded.state.name == "PROCESSING":
            if time.monotonic() > deadline:
                raise TimeoutError("Uploaded file was still PROCESSING after the upload timeout")
            time.sleep(5)
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state and uploaded.state.name == "FAILED":
            raise RuntimeError("Gemini could not process the uploaded file")
        media_part = types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type)

    # ── 2. Extract frames (video only) ────────────────────────────────────────
    frame_parts: list[types.Part] = []
    frames_dir: str | None = None
    is_video = mime_type.startswith("video/")
    if is_video:
        _log("    Step 2/3: Extracting frames every 1 minute…")
        try:
            frames_dir = tempfile.mkdtemp(prefix="bgu_frames_")
            frame_pattern = os.path.join(frames_dir, "frame_%04d.jpg")
            ffmpeg_result = subprocess.run(
                ["ffmpeg", "-i", file_path, "-vf", "fps=1/60", "-q:v", "2", frame_pattern],
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
                _log(f"    Extracted {len(frame_parts)} frame(s)")
            else:
                _log(f"    Frame extraction failed (exit {ffmpeg_result.returncode}) — continuing without frames")
        except Exception as frame_exc:
            _log(f"    Frame extraction error (non-fatal): {frame_exc} — continuing without frames")
    else:
        _log("    Step 2/3: Audio-only file — skipping frame extraction")

    # ── 3. Transcribe ─────────────────────────────────────────────────────────
    _log("    Step 3/3: Calling generate_content for transcription…")
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
        media_part,
        *frame_parts,
        types.Part(text=prompt),
    ])]
    try:
        resp = _timed(
            "generate_content(transcribe)", _do_generate,
            client, model, contents,
            timeout=TRANSCRIBE_TIMEOUT_S,
        )
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception as del_exc:
                _log(f"    Could not delete uploaded file (auto-expires in 48h): {del_exc}")
    transcript_len = len(resp.text) if resp.text else 0
    _log(f"    Transcription received: {transcript_len} chars")

    # ── 4. Clean up local frames ──────────────────────────────────────────────
    if frames_dir and os.path.exists(frames_dir):
        shutil.rmtree(frames_dir, ignore_errors=True)
        _log("    Local frames deleted OK")

    return resp.text


def summarize_segment(
    transcript: str,
    language: str = "Hebrew",
    model: str = DEFAULT_MODEL,
) -> str:
    """Summarize a single transcript segment using Gemini."""
    if not transcript or not transcript.strip():
        return ""
    _log(f"    Summarising {len(transcript)} chars via Gemini…")

    timeout_attempts = 0
    quota_attempt    = 0

    while True:
        try:
            label = f"generate_content(summarize) attempt t={timeout_attempts+1} q={quota_attempt+1}"
            resp = _timed(
                label, _do_generate,
                _GEMINI_CLIENT, model,
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
            text = _apply_latex_fixes(text, model)
            return text

        except TimeoutError as e:
            timeout_attempts += 1
            _log(f"    ✗ Summarization timed out (attempt {timeout_attempts}/{SUMMARIZE_MAX_RETRIES}): {e}")
            if timeout_attempts >= SUMMARIZE_MAX_RETRIES:
                _log(f"    ✗ All {SUMMARIZE_MAX_RETRIES} summarization timeout attempts exhausted")
                return (
                    f"[הסיכום נכשל לאחר {SUMMARIZE_MAX_RETRIES} ניסיונות — חריגת זמן ({SUMMARIZE_TIMEOUT_S}s). "
                    "נסה שנית עם מודל מהיר יותר.]"
                )
            wait = 10 * timeout_attempts
            _log(f"    Retrying after {wait}s…")
            time.sleep(wait)

        except Exception as e:
            if _is_quota_error(e):
                quota_attempt += 1
                wait = _quota_backoff_seconds(quota_attempt, e)
                _log(
                    f"    ⏳ 429 RESOURCE_EXHAUSTED on summarization "
                    f"(quota attempt {quota_attempt}) — waiting {wait:.0f}s before retry…"
                )
                time.sleep(wait)
            else:
                # Non-retriable error (auth failure, malformed request, etc.)
                _log(f"    ✗ Summarization failed (non-retriable): {e}")
                return f"[שגיאת סיכום: {e}]"


def generate_master_summary(
    chunk_summaries: list[dict],
    language: str = "Hebrew",
    model: str = DEFAULT_MODEL,
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
    prompt = (
        "להלן סיכומי הסגמנטים של הרצאה אקדמית שלמה. "
        "אחד אותם לסיכום מאסטר אחד שמכיל:\n"
        "1. סקירה כללית של כל ההרצאה (5-7 משפטים)\n"
        "2. רצף מלא של כל יחידות התוכן (הגדרות, משפטים, הוכחות, תרגילים) לפי סדרן\n"
        "3. גלוסרי מאוחד של כל המונחחים\n\n"
        f"{combined}"
    )
    quota_attempt = 0
    while True:
        try:
            resp = _timed(
                f"generate_content(master_summary) q={quota_attempt+1}", _do_generate,
                _GEMINI_CLIENT, model, prompt,
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
            text = _apply_latex_fixes(text, model)
            return text
        except Exception as e:
            if _is_quota_error(e):
                quota_attempt += 1
                wait = _quota_backoff_seconds(quota_attempt, e)
                _log(
                    f"  ⏳ 429 RESOURCE_EXHAUSTED on master summary "
                    f"(quota attempt {quota_attempt}) — waiting {wait:.0f}s before retry…"
                )
                time.sleep(wait)
            else:
                _log(f"  ✗ Master summary failed (non-retriable): {e}")
                return f"[Master summary error: {e}]"


_QUIZ_SYSTEM = (
    "אתה עוזר שיוצר שאלות חזרה על הרצאה אקדמית.\n"
    "צור שאלות בעברית בלבד.\n"
    "החזר JSON בלבד — ללא הסבר, ללא markdown, ללא ```.\n"
    "פורמט חובה:\n"
    '{"blocks":[{"position":"mid","questions":[{"num":1,"text":"שאלה?","choices":["א. ...","ב. ...","ג. ...","ד. ..."],"answer_idx":0}]},{"position":"end","questions":[{"num":3,"text":"שאלה?","choices":["א. ...","ב. ...","ג. ...","ד. ..."],"answer_idx":2}]}]}\n'
    "כללים:\n"
    "- בלוק mid: 2-3 שאלות על הנושאים הראשונים (כחצי ראשון של ההרצאה)\n"
    "- בלוק end: 2-3 שאלות על כלל ההרצאה\n"
    "- ממספור הכולל: שאלות mid מתחילות ב-1, שאלות end ממשיכות מאיפה שעצרנו\n"
    "- choices: תמיד 4 תשובות — א, ב, ג, ד\n"
    "- answer_idx: אינדקס התשובה הנכונה (0-3)\n"
    "- השאלות מבוססות על הגדרות, משפטים, הוכחות ותרגילים מהסיכום"
)

_QUIZ_TIMEOUT_S = 120


def generate_quiz(
    chunk_summaries: list[dict],
    model: str = DEFAULT_MODEL,
) -> dict | None:
    """
    Generate an interactive quiz for the master summary.
    Returns a dict with shape {blocks: [{position, questions: [{num, text, choices, answer_idx}]}]}
    or None on failure.
    """
    if not chunk_summaries:
        return None
    combined = "\n\n---\n\n".join([
        f"### חלק {i + 1}\n{c.get('summary', '')}"
        for i, c in enumerate(chunk_summaries)
        if c.get("summary")
    ])
    prompt = f"להלן סיכום הרצאה אקדמית. צור שאלות חזרה לפי הפורמט שהוגדר:\n\n{combined}"
    try:
        client = _GEMINI_CLIENT
        resp = _timed(
            "generate_content(quiz)", _do_generate,
            client, model, prompt,
            types.GenerateContentConfig(
                system_instruction=_QUIZ_SYSTEM,
                response_mime_type="application/json",
            ),
            timeout=_QUIZ_TIMEOUT_S,
        )
        text = (resp.text or "").strip()
        if not text:
            _log("  Quiz response was empty")
            return None
        # Strip markdown code fences if the model ignored our instruction
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        # Remove invalid backslash escapes (e.g. \p, \v) that json.loads() rejects.
        # Valid JSON escape chars after \: " \ / b f n r t u
        text = re.sub(r'\\(?!["\\/bfnrtu])', r'', text)
        data = _json.loads(text)
        _log(f"  Quiz generated: {sum(len(b.get('questions', [])) for b in data.get('blocks', []))} questions")
        return data
    except Exception as exc:
        _log(f"  ✗ Quiz generation failed: {exc}")
        return None


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

    client = _GEMINI_CLIENT
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
        final_items = _kb_build_with_llm(raw_blocks, existing, course_label, model)
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
    if not d.exists():
        return  # WIP dir was deleted (terminated) — silently skip, do not recreate
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


def _wip_terminate(course_dir: str, wip_name: str) -> bool:
    """
    Permanently delete a WIP directory identified by its wip_name
    (the directory name after stripping the '_wip_' prefix).
    Returns True if the directory existed and was removed, False if not found.
    """
    d = TRANSCRIPTIONS_DIR / course_dir / f"_wip_{wip_name}"
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


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
            "model":          meta.get("model", DEFAULT_MODEL),
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
        client    = _GEMINI_CLIENT
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

            # ── Transcribe (retry indefinitely on 429, fail fast on other errors) ──
            update(f"🎙️ Transcribing segment {seg_num}/{total_seg}…")
            _log(f"  [transcription start]")
            transcript: str | None = None
            quota_attempt = 0
            while True:
                try:
                    transcript = transcribe_segment(
                        client, chunk_path, chunk_name, mime_type, lang_hint, model
                    )
                    _log(f"  [transcription end]  {len(transcript)} chars")
                    break
                except Exception as exc:
                    if _is_quota_error(exc):
                        quota_attempt += 1
                        wait = _quota_backoff_seconds(quota_attempt, exc)
                        _log(
                            f"  ⏳ 429 RESOURCE_EXHAUSTED on seg {seg_num} "
                            f"(quota attempt {quota_attempt}) — waiting {wait:.0f}s before retry…"
                        )
                        update(
                            f"⏳ Segment {seg_num}/{total_seg}: quota exhausted, "
                            f"waiting {wait:.0f}s… (attempt {quota_attempt})"
                        )
                        time.sleep(wait)
                    else:
                        _log(f"  ✗ TRANSCRIPTION FAILED for segment {seg_num}")
                        _log(f"    {type(exc).__name__}: {exc}")
                        _log(traceback.format_exc())
                        update(f"⚠️ Segment {seg_num}/{total_seg} transcription failed — skipping")
                        failed_segments.append(seg_num)
                        break  # non-quota error: give up on this segment

            if transcript is None:
                continue   # segment failed for a non-quota reason; move to next

            # ── Summarise ─────────────────────────────────────────────────────
            update(f"🤖 Summarising segment {seg_num}/{total_seg}…")
            _log(f"  [summarisation start]")
            try:
                summary = summarize_segment(transcript, lang, model=model)
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

            # ── Inter-segment delay (paces to ~4 RPM, below the 5 RPM quota) ──
            if not test_mode and i < len(chunks) - 1:
                _log("  ⏸ Waiting 15s before next segment to stay under quota…")
                update(f"⏸ Segment {seg_num}/{total_seg} done — waiting 15s before next…")
                time.sleep(15)

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
                master = generate_master_summary(chunk_summaries, lang, model=model)
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
        kb_path = update_knowledge_base(course_dir, file_name, master, model)
        _log(f"  Knowledge base → {kb_path}")

        # ── Generate quiz ─────────────────────────────────────────────────────
        quiz: dict | None = None
        if not test_mode:
            update("❓ Generating quiz…")
            quiz = generate_quiz(chunk_summaries, model=model)

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
            "quiz":             quiz,
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
                summary = summarize_segment(seg_text, lang, model=model)
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
                master = generate_master_summary(chunk_summaries, lang, model=model)
            except Exception as exc:
                _log(f"  ✗ MASTER SUMMARY FAILED: {exc}")
                master = "\n\n---\n\n".join(
                    f"**Segment {c['idx'] + 1}**\n{c['summary']}"
                    for c in chunk_summaries
                )

        # ── Save outputs ──────────────────────────────────────────────────────
        update("💾 Saving outputs…")
        s_path, t_path = save_outputs(course_dir, file_name, master, text.strip())

        # ── Update knowledge base ─────────────────────────────────────────────
        update("📚 Updating knowledge base…")
        kb_path = update_knowledge_base(course_dir, file_name, master, model)
        _log(f"  Knowledge base → {kb_path}")

        # ── Generate quiz ─────────────────────────────────────────────────────
        update("❓ Generating quiz…")
        quiz = generate_quiz(chunk_summaries, model=model)

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
            "quiz":             quiz,
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
    quiz:             dict | None      = None
    error:            str | None       = None


class ChatRequest(BaseModel):
    user_message:    str
    summary_context: str
    chat_history:    list[dict] = []
    model:           str        = DEFAULT_MODEL


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
    content = kb_path.read_text(encoding="utf-8")
    content = _normalize_latex(content)
    return {
        "course_id":    course_id,
        "course_label": COURSES[course_id],
        "content":      content,
    }


@app.get("/models")
def list_models():
    return {"models": AVAILABLE_MODELS}


@app.post("/upload", response_model=JobStatus)
async def upload_video(
    background_tasks: BackgroundTasks,
    file:          UploadFile = File(...),
    model:         str        = Form(DEFAULT_MODEL),
    lang:          str        = Form("he"),
    chunk_minutes: int        = Form(12),
    test_mode:     bool       = Form(False),
    course_dir:    str        = Form("combinatorics"),
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

    # Ensure a clean slate: delete any stale WIP directory for this file.
    # This handles the case where a previous job was terminated or crashed mid-run.
    _wip_cleanup(course_dir, file_name)

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "queued", "progress": "Queued…"}
    _log(f"UPLOAD ACCEPTED  job={job_id}  file={file_name}  size={len(content)/1_048_576:.1f}MB")

    background_tasks.add_task(
        _run_processing_job,
        job_id=job_id, tmp_path=tmp_path, file_name=file_name,
        mime_type=mime_type, model=model, lang=lang,
        test_mode=test_mode, chunk_minutes=chunk_minutes, course_dir=course_dir,
    )

    return JobStatus(job_id=job_id, status="queued", progress="Queued…")


@app.post("/upload-transcript", response_model=JobStatus)
async def upload_transcript(
    background_tasks: BackgroundTasks,
    file:       UploadFile = File(...),
    model:      str        = Form(DEFAULT_MODEL),
    lang:       str        = Form("he"),
    course_dir: str        = Form("combinatorics"),
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
        course_dir=course_dir,
    )

    return JobStatus(job_id=job_id, status="queued", progress="Queued…")


@app.get("/resumable-jobs/{course_id}")
def list_resumable_jobs(course_id: str):
    """Return all in-progress (checkpointed) jobs for a given course."""
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")
    return {"jobs": _wip_list_resumable(course_id)}


@app.delete("/resumable-jobs/{course_id}/{wip_name}")
def terminate_resumable_job(course_id: str, wip_name: str):
    """
    Permanently delete a checkpointed WIP job so the file can be reprocessed
    from scratch.  Removes all partial transcripts and summaries for that job.
    """
    if course_id not in COURSES:
        raise HTTPException(status_code=404, detail=f"Unknown course: {course_id}")
    removed = _wip_terminate(course_id, wip_name)
    if not removed:
        raise HTTPException(status_code=404, detail="Interrupted job not found")
    _log(f"TERMINATED WIP job: course={course_id}  wip_name={wip_name}")
    return {"status": "terminated", "wip_name": wip_name}


@app.post("/resume", response_model=JobStatus)
async def resume_job(
    background_tasks: BackgroundTasks,
    file:          UploadFile = File(...),
    model:         str        = Form(DEFAULT_MODEL),
    lang:          str        = Form("he"),
    chunk_minutes: int        = Form(12),
    course_dir:    str        = Form("combinatorics"),
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
        client              = _GEMINI_CLIENT
        tutor_system_prompt = _load_tutor_prompt()

        seed_history: list[dict] = [
            {"role": "user",  "text": f"סיכום מאסטר של ההרצאה:\n{req.summary_context}"},
            {"role": "model", "text": "קראתי את סיכום ההרצאה. אני מוכן ללמד אותך נושא אחד בכל פעם!"},
        ]

        chat_obj = client.chats.create(
            model=_resolve_model(req.model),
            config=types.GenerateContentConfig(system_instruction=tutor_system_prompt),
            history=[
                types.Content(role=h["role"], parts=[types.Part(text=h.get("text") or "")])
                for h in seed_history + req.chat_history
            ],
        )
        response = chat_obj.send_message(req.user_message)
        reply = response.text
        if not reply:
            raise HTTPException(status_code=503, detail="התגובה חסומה או ריקה. נסה שנית.")
        return {"reply": reply}

    except HTTPException:
        raise
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
    summary = _normalize_latex(summary)   # fix delimiter issues in stored summaries without a new API call

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
