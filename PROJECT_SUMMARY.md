# BGU Lecture Summarizer — Project Summary

> **Generated:** 2026-03-27
> **Codebase:** `C:\Users\royan\IdeaProjects\lecture-summarizer`
> **Primary file:** `app.py` (783 lines)

---

## 1. Project Overview

**What it does:**
A Streamlit web application that takes a university lecture recording (MP4, MP3, WAV, M4A) and turns it into an interactive learning session. Gemini 2.5 Flash handles both transcription and tutoring natively — no local Whisper model or GPU needed.

**Problem it solves:**
BGU (Ben-Gurion University) students receive long, dense lecture recordings. This tool:
1. Transcribes the audio/video (including whiteboard content) automatically
2. Produces a structured, pedagogically ordered summary
3. Provides an AI tutor chat that teaches material one topic at a time, in Hebrew, following a strict Socratic progression

**Target user:** A single BGU student running the app locally on Windows.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Streamlit UI (app.py)                   │
│                                                             │
│  Sidebar              Main Area                             │
│  ─────────            ────────────────────────────────────  │
│  API Key Input        File Upload / recordings/ picker      │
│  Language Select      [Analyze & Summarize] button          │
│  Auto-delete CB       ↓ Processing (progress bar)           │
│                       ↓                                     │
│                       Results Section                        │
│                         ├─ [Video Player]  [Summary HTML]   │
│                         ├─ Transcription expander           │
│                         └─ Download buttons (txt / PDF)     │
│                                                             │
│                       Chat Section                          │
│                         ├─ Message bubbles (user / AI)      │
│                         ├─ Text input + Send button         │
│                         └─ Clear Chat button                │
└─────────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
  Google Gemini Files API         Google Gemini Chat API
  (upload + transcribe)           (multi-turn tutor session)
         │
         ▼
   ffmpeg subprocess (only for files > 500 MB)
```

**Module / Folder Map:**

| Path | Role |
|------|------|
| `app.py` | Entire application — UI, processing logic, helpers, PDF export |
| `skills/tutor_skill.txt` | Hebrew system prompt injected into every Gemini chat call |
| `recordings/` | Drop folder for local lecture files; listed in UI dropdown |
| `transcriptions/` | Created at startup; **not used** (output is in-memory only) |
| `summaries/` | Created at startup; **not used** (output is in-memory only) |
| `.env` | Holds `GOOGLE_API_KEY`; loaded at startup via `python-dotenv` |
| `run_BGU_AI.bat` | Windows double-click launcher (activates `.venv`, runs Streamlit) |
| `create_shortcut.ps1` | Generates `bgu_ai.ico` + desktop `.lnk` shortcut |
| `download_test.py` | Standalone proof-of-concept: downloads MP4 from a CloudFront URL |
| `main.py` | Empty placeholder — unused |

---

## 3. Tech Stack

| Category | Choice | Notes |
|----------|--------|-------|
| Language | Python 3.14 | Latest stable |
| UI Framework | Streamlit | Single-file web app with session state |
| AI / LLM | `google-genai` (NEW SDK) | NOT `google.generativeai` (deprecated) |
| LLM Model | `gemini-2.5-flash` | Configured as `MODEL` constant at top of `app.py` |
| Video Splitting | `ffmpeg` (subprocess) | Only invoked for files > 500 MB |
| Math Rendering | `sympy` (optional) | LaTeX → Unicode pretty-print |
| PDF Export | `fpdf2` (optional) | Tag-aware, math-rendering PDF output |
| Env Config | `python-dotenv` (optional) | Reads `GOOGLE_API_KEY` from `.env` |
| Font / CSS | Google Fonts (Syne + Inter) | Loaded via CDN in page HTML |
| Launcher | `.bat` + `.ps1` | Windows-only; no Docker or CI |

**Optional dependencies** (app degrades gracefully if missing):

```
fpdf2   → PDF export button hidden if not installed
sympy   → LaTeX falls back to regex cleanup
dotenv  → API key must be entered manually
```

---

## 4. Entry Points

### Primary Entry Point
```
streamlit run app.py
```
Streamlit executes `app.py` top-to-bottom on every user interaction. There is no `main()` function — execution is driven by Streamlit's reactive model.

### Launcher Scripts
```
run_BGU_AI.bat          # Double-click on Windows → activates .venv → streamlit run app.py
BGU Lecture Summarizer.lnk  # Desktop shortcut → calls run_BGU_AI.bat
```

### Key Constants (top of `app.py`)
```python
MODEL = "gemini-2.5-flash"          # Change here to switch Gemini model
CHUNK_THRESHOLD = 500 * 1024 * 1024 # Change to adjust when splitting kicks in
```

### Processing Trigger
The main processing logic starts at line ~459 inside:
```python
if process_btn and has_file and has_key:
    ...
```
A secondary processing block for large-file chunks starts at ~576:
```python
if st.session_state.chunks_remaining and st.session_state.ready:
    ...
```

---

## 5. Current State

### ✅ Working
- Full upload-to-summary pipeline for files ≤ 200 MB (browser upload limit) or any size from `recordings/`
- Step-by-step chunk processing for files > 500 MB via ffmpeg + "Summarize Next Part" button
- Hebrew, English, and Arabic language hints for transcription
- Multi-turn AI tutor chat with persistent context across Streamlit reruns
- `.env` auto-loading of API key
- Two-column video player + timestamp jump buttons (when file not deleted)
- `[DEFINITION]` / `[THEOREM]` / `[PROOF]` tag rendering in HTML and PDF
- LaTeX math via SymPy or regex fallback
- PDF export (fpdf2) with Unicode font detection (Arial on Windows, DejaVuSans on Linux)
- Auto-delete local video after processing (configurable)
- Desktop shortcut with custom icon

### ⚠️ Incomplete / Stub
| Item | Status |
|------|--------|
| `main.py` | Empty — was intended as a CLI entry point, never implemented |
| `transcriptions/` folder | Created but never written to (all output is in-memory) |
| `summaries/` folder | Created but never written to (all output is in-memory) |
| `download_test.py` | Hardcoded URL proof-of-concept; not integrated into UI |
| BGU Panopto/Moodle auth | No production integration; `download_test.py` is the only prototype |

### 🔧 No Explicit TODOs in Code
No `# TODO` or `# FIXME` comments exist. Incomplete areas are inferred from structure.

---

## 6. Key Patterns

### Reactive Streamlit Model
All state lives in `st.session_state`. Every button click triggers a full Python re-execution. Side effects (API calls, file I/O) are guarded by button-press conditions:
```python
if process_btn and has_file and has_key:
    # ...
    st.rerun()
```

### Chat Rebuild Pattern (Stateless API Objects)
The Gemini chat object is **never stored in session state**. Instead, plain Python dicts are stored and the chat is rebuilt fresh from history on every send:
```python
history = [
    types.Content(role=h["role"], parts=[types.Part(text=h["text"])])
    for h in st.session_state.chat_history_raw
]
chat = client.chats.create(model=MODEL, config=..., history=history)
response = chat.send_message(question)
```
This avoids stale object errors across Streamlit reruns.

### Optional Dependency Pattern
All three optional libraries are wrapped identically:
```python
try:
    from library import something
    LIB_AVAILABLE = True
except ImportError:
    LIB_AVAILABLE = False
```
UI elements for that feature are conditionally hidden with `if LIB_AVAILABLE`.

### Transcript Accumulation Pattern (Chunked Flow)
Each processed chunk appends its transcript with a section header:
```python
st.session_state.transcription += f"\n\n--- {next_label} ---\n\n{new_transcript}"
```
And the chat seed is updated to reflect the growing transcript:
```python
st.session_state.chat_history_raw[0]["text"] = f"תמלול...:\n{st.session_state.transcription}"
```

### PDF Caching Pattern
PDF generation is expensive; the result is cached with Streamlit's `@st.cache_data` using a JSON key:
```python
@st.cache_data(show_spinner=False)
def _cached_pdf(summaries_json: str) -> bytes | None:
    return generate_pdf_bytes(json.loads(summaries_json))
```
Cache is invalidated automatically when summaries change (new JSON key).

### Inline CSS Theming
All styling is injected once via `st.markdown(..., unsafe_allow_html=True)` at startup. Semantic CSS classes (`tag-definition`, `tag-theorem`, `tag-proof`, `chat-user`, `chat-ai`) are applied programmatically in Python string formatting.

---

## 7. Pain Points / Code Smells

### 🔴 High Priority

**1. No error handling in chat section**
The `chat.send_message()` call has no `try/except`. A network timeout or Gemini API error will crash the entire page with a Python traceback instead of a user-friendly message.
_File: `app.py`, ~line 772_

**2. Video path race condition**
When `auto_delete=True` and the user uploads a file (not from `recordings/`), the video path is stored in `session_state.video_path` — but later in the same processing block, the temp file is deleted. Subsequent reruns will show the video player but the file won't exist.
_File: `app.py`, ~lines 486–542_

**3. No ffmpeg presence check**
`ffmpeg` is called via `subprocess.run(["ffmpeg", ...])`. If ffmpeg is not installed or not on PATH, the error message is a raw subprocess exception. Users should be warned at startup.
_File: `app.py`, ~line 226_

### 🟡 Medium Priority

**4. Markdown not rendered in chat bubbles**
AI responses are embedded in raw HTML `<div>` tags. Markdown syntax (`**bold**`, `$$math$$`, ` ```code``` `) from Gemini is shown as literal characters instead of being rendered.
_File: `app.py`, ~lines 741–748_

**5. No version pins in `requirements.txt`**
All five dependencies are unpinned. A `google-genai` or `fpdf2` breaking release could silently break the app.
_File: `requirements.txt`_

**6. `.env` file committed / not gitignored**
The `.env` file containing the live API key appears to be in the project root without a `.gitignore` entry (`.gitignore` not found in codebase). Any push to a public repo would expose the key.

**7. Chunk size threshold is fixed, not adaptive**
The 500 MB threshold for splitting is hardcoded. For high-bitrate video, 500 MB may still exceed Gemini's per-file processing capacity, causing an unhelpful server-side error.

### 🟢 Low Priority

**8. SymPy LaTeX failures are silent**
Complex LaTeX (matrices, `\begin{...}` environments) silently falls back to regex stripping. The user sees a degraded formula without any indication.

**9. `transcriptions/` and `summaries/` folders are dead artifacts**
These folders are created by `os.makedirs(..., exist_ok=True)` but never written to. They add confusion about intended persistence behavior.

**10. `download_test.py` hardcodes a Panopto CloudFront URL**
The URL and browser-spoofed headers are hardcoded. This will break when the CDN URL expires and is misleading as a code artifact left in the root.

**11. ffmpeg stderr truncated to 600 chars**
Error messages from ffmpeg are clipped, making debugging split failures harder.

---

## 8. Open Questions

| # | Question | Why It Matters |
|---|----------|----------------|
| 1 | **Will Gemini transcription include whiteboard content?** The prompt says `[לוח]` for whiteboard annotations — but does `gemini-2.5-flash` reliably detect and annotate whiteboard frames in lecture videos? | Core feature validity |
| 2 | **What is the intended BGU Panopto integration flow?** `download_test.py` shows a CloudFront URL that expires. Is the plan to scrape Panopto auth cookies from the browser? Use Panopto's API? | Roadmap direction |
| 3 | **Why do `transcriptions/` and `summaries/` folders exist?** Were they meant for file-based persistence that was never implemented? Or are they legacy from an earlier design? | Cleanup vs. future feature |
| 4 | **What format does Gemini actually return for `[DEFINITION]`/`[THEOREM]`/`[PROOF]` tags?** The tutor system prompt is in Hebrew and doesn't explicitly instruct Gemini to emit these XML-style tags — so tag rendering in HTML/PDF may rarely trigger. | Feature reliability |
| 5 | **Is the 200 MB Streamlit upload limit a real constraint?** For lecture videos (typically 500 MB–2 GB), the browser upload path is effectively unusable. Is the `recordings/` folder the primary intended input method? | UX design decision |
| 6 | **Should the app persist data between sessions?** Currently, refreshing the browser loses everything. Adding SQLite or file-based persistence would make it production-ready for regular student use. | Architecture decision |
| 7 | **What happens when Gemini's Files API quota is hit?** There's no handling for quota-exceeded errors from the Files API. At 5+ hours of lecture video per day, this could be a real issue. | Production reliability |
| 8 | **Why is `main.py` empty?** Was it intended as a non-Streamlit CLI entry point, a FastAPI backend, or just an IDE artifact? | Cleanup decision |
| 9 | **Is multi-student / multi-session use intended?** Streamlit's default behavior runs one Python process per browser session. If multiple users run simultaneously, Gemini quota sharing and local file paths would conflict. | Deployment model |
| 10 | **Should the tutor system prompt support English lectures taught in Hebrew?** Many BGU courses mix Hebrew instruction with English technical terms. The current language selector treats Hebrew/English as binary. | Accuracy / UX |

---

## Appendix: Function Reference

| Function | Lines | Purpose |
|----------|-------|---------|
| `get_mime(filename)` | 220 | Map file extension → MIME type |
| `split_video(input_path, output_dir)` | 223 | ffmpeg: split video into 20-min chunks |
| `transcribe_file(client, ...)` | 240 | Upload → poll → transcribe → delete from Gemini |
| `summarize_chunk(client, transcript, ...)` | 282 | Generate structured summary via tutor system prompt |
| `parse_timestamps(text)` | 295 | Extract `[MM:SS]` / `[HH:MM:SS]` timestamps from transcript |
| `render_math_str(latex_str)` | 310 | LaTeX → Unicode (SymPy or regex fallback) |
| `render_summary_html(text)` | 321 | Wrap `[TAG]...[/TAG]` blocks in styled HTML divs |
| `generate_pdf_bytes(chunk_summaries)` | 345 | Build PDF with tag rendering and math |
| `_cached_pdf(summaries_json)` | 451 | Streamlit-cached wrapper for PDF generation |

## Appendix: Session State Keys

| Key | Type | Purpose |
|-----|------|---------|
| `transcription` | `str\|None` | Full raw transcript (all chunks joined) |
| `summary` | `str\|None` | Most recent chunk summary |
| `chat_display` | `list` | UI-only chat history `{role, content}` |
| `chat_history_raw` | `list` | Gemini API chat history `{role, text}` |
| `api_key` | `str` | Gemini API key |
| `ready` | `bool` | True after first summarization |
| `chunk_summaries` | `list` | All processed chunks `{idx, transcript, summary}` |
| `chunks_remaining` | `list` | File paths of unprocessed chunks |
| `chunk_dir_path` | `str\|None` | Temp dir for ffmpeg chunks (kept until all done) |
| `current_chunk_idx` | `int` | Index of last processed chunk |
| `total_chunks_count` | `int` | Total chunks from this file |
| `processing_mime` | `str\|None` | MIME type for remaining chunks |
| `processing_fname` | `str\|None` | Original filename |
| `video_path` | `str\|None` | Path to local video for `st.video()` |
| `video_start_time` | `int` | Current video seek position (seconds) |
