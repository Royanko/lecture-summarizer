# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview
A web app that takes lecture recordings (MP4/MP3/WAV/M4A) or plain-text transcripts, transcribes them via the Gemini API, produces structured RTL Hebrew summaries per chunk with styled semantic cards, and provides an interactive tutoring chat grounded in the transcript. Each course also maintains an auto-updating **Knowledge Base** — a deduplicated, topic-organised exam study guide.

**Architecture**: Next.js 15 frontend + FastAPI backend (two separate processes).

## Stack

### Backend (`backend/`)
- **Python 3.14** with a `.venv` virtualenv
- **FastAPI** + **uvicorn** — REST API server on `http://localhost:8000`
- **`google-genai`** — Gemini API client (`from google import genai`). NEW package. Do NOT use `google-generativeai` (deprecated).
- **Gemini Developer API (API key)** — the client is initialised with `genai.Client(api_key=...)`, reading `GOOGLE_API_KEY` (or `GEMINI_API_KEY`). Vertex AI is no longer used (the Google Cloud project was closed in Nov 2026). Keys come from https://aistudio.google.com/apikey.
- **`python-dotenv`** — loads env vars from the root `.env`, then `backend/.env` (the key lives in `backend/.env`)
- **ffmpeg** (system binary) — splits files >500 MB into configurable chunks
- **Models**: `gemini-3.6-flash` (default), `gemini-4-hydrogen`, `gemini-3.5-flash-lite` (selectable). The old `gemini-2.5-*` ids (deprecated by Google, rerouted 2026-12-01) are mapped forward by `_resolve_model()` so old WIP checkpoints and browser sessions keep working.
- **Free tier**: roughly 5 requests/minute and ~20 requests/day per model (check https://aistudio.google.com/rate-limit). A full lecture uses about 2 requests per segment plus 3; the 429 backoff and WIP resume handle hitting the limit.

### Frontend (`frontend/`)
- **Next.js 15** (React 18, TypeScript)
- **Tailwind CSS**
- **`react-markdown`** + **`remark-math`** + **`rehype-katex`** — Markdown + LaTeX rendering
- **`remark-gfm`** — GitHub Flavored Markdown (tables, strikethrough)
- **`rehype-raw`** — raw HTML passthrough
- Dev server: `http://localhost:3000`

## File Structure
```
backend/
  main.py               # FastAPI app — all processing logic + KB pipeline
  build_kb.py           # One-shot script: rebuild KB from an existing master summary
  requirements.txt
  .env                  # GOOGLE_API_KEY=... (not committed)
frontend/
  app/
    page.tsx            # Dashboard — course cards, upload, KB banner, resume interrupted jobs
    summary/page.tsx    # Summary + tutor chat page
    knowledge-base/[course]/page.tsx  # Knowledge Base viewer page
  components/
    SummaryRenderer.tsx # Shared RTL renderer: semantic cards, LaTeX
    Sidebar.tsx
    CourseCard.tsx
  lib/
    api.ts              # API client helpers
    AppContext.tsx       # Global state (course, result, chat history) — persisted to sessionStorage
skills/
  summary_skill.txt     # System prompt for per-segment summarisation (Hebrew)
  tutor_chat_skill.txt  # System prompt for AI tutor chat. DO NOT MODIFY.
  kb_builder_skill.txt  # System prompt for KB merge/refinement LLM call
recordings/             # Drop large lecture files here
summaries/
  <course>/
    *.txt               # Saved master summaries (timestamped)
    knowledge_base.md   # Auto-updated course knowledge base
    .meta.json          # Custom display names for saved summaries
transcriptions/
  <course>/
    *.txt               # Saved full transcripts (same filename as summary)
    _wip_<name>/        # Checkpoint dir for interrupted jobs (see WIP section)
start_app.bat           # Launches both servers (Windows)
_legacy_streamlit_backup/  # Old Streamlit app — ignore completely
```

## Backend API Endpoints (`backend/main.py`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/models` | List available Gemini models |
| POST | `/upload` | Start fresh video/audio processing job; wipes any stale WIP dir first |
| POST | `/upload-transcript` | Fast-track: start job from `.txt` transcript, skips transcription |
| POST | `/resume` | Resume an interrupted job; reuses WIP checkpoints |
| GET | `/jobs/{job_id}` | Poll job status + progressive chunk results |
| POST | `/chat` | Send a chat message; returns assistant reply |
| GET | `/knowledge-base/{course_id}` | Return `{course_id, course_label, content}` for KB viewer |
| GET | `/resumable-jobs/{course_id}` | List interrupted (WIP) jobs for a course |
| DELETE | `/resumable-jobs/{course_id}/{wip_name}` | Terminate and delete a WIP job permanently |
| GET | `/summaries/{course_id}` | List saved summaries for a course (newest first) |
| GET | `/summaries/{course_id}/{filename}` | Fetch full text of a saved summary + transcript |
| DELETE | `/summaries/{course_id}/{filename}` | Delete a saved summary and its transcript |
| PATCH | `/summaries/{course_id}/{filename}` | Set a custom display name for a saved summary |

### Job flow
1. `POST /upload` (or `/upload-transcript`, `/resume`) → creates background job, returns `job_id`
2. Client polls `GET /jobs/{job_id}` until `status == "done"` or `"error"`
3. On `"done"`, response includes `chunks` (list of `{idx, transcript, summary}`), `summary` (master), `transcript` (full)
4. After saving outputs, the KB pipeline runs automatically

## WIP Checkpoint System
Interrupted jobs are saved to `transcriptions/<course>/_wip_<sanitized_name>/`:
- `meta.json` — job parameters (model, lang, segment count, etc.)
- `seg_000_transcript.txt`, `seg_000_summary.txt` — per-segment checkpoints

Key behaviours:
- **`/upload` always deletes a pre-existing WIP dir** for that filename before starting, guaranteeing a clean slate.
- **`/resume` requires the WIP dir to exist** (404 if not found).
- **`_wip_write_checkpoint`** silently skips writing if the WIP dir has been deleted (prevents terminated background tasks from recreating it).
- WIP dir is automatically removed on successful job completion.

## Processing Flow (`backend/main.py`)
1. **Receive file** via `/upload`; save to temp dir
2. **Large file**: `split_video()` uses ffmpeg to cut into configurable-length chunks (default 12 min)
3. **Per chunk** — `transcribe_segment()`: media ≤ 20 MB (`INLINE_MAX_BYTES`) is sent as inline bytes; larger media is uploaded through the Files API (poll until processed, deleted after the call) → `generate_content` with the media Part + frame images (video)
4. **Per chunk** — `summarize_segment()`: `generate_content` with transcript + `summary_skill.txt`
5. **Master summary** — `generate_master_summary()`: combines all chunk summaries
6. **Save outputs** — `save_outputs()`: writes to `summaries/<course>/` and `transcriptions/`
7. **Knowledge Base** — `update_knowledge_base()`: 5-step pipeline (see below)
8. **Chat** — `/chat` rebuilds `client.chats.create(...)` from `history` plain dicts on every request

## Key google.genai Patterns
```python
from google import genai
from google.genai import types

# Client uses the Gemini Developer API with an API key
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# Transcription — inline bytes for small media
part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
# ...or the Files API for media over INLINE_MAX_BYTES (free, 2 GB/file, auto-deleted after 48h)
f = client.files.upload(file=file_path, config=types.UploadFileConfig(mime_type=mime_type))
part = types.Part.from_uri(file_uri=f.uri, mime_type=mime_type)   # after f.state leaves PROCESSING

r = client.models.generate_content(model=MODEL, contents=[
    types.Content(role="user", parts=[part, types.Part(text="prompt")])
])
client.files.delete(name=f.name)

# Generate text only (with system instruction)
r = client.models.generate_content(model=MODEL, contents="text",
    config=types.GenerateContentConfig(system_instruction="..."))

# Chat (rebuild from history on every request — no persistent chat object)
chat = client.chats.create(
    model=MODEL,
    config=types.GenerateContentConfig(system_instruction="..."),
    history=[types.Content(role=h["role"], parts=[types.Part(text=h["text"])]) for h in history_dicts]
)
response = chat.send_message("question")
```

**`build_kb.py`** uses the same `GOOGLE_API_KEY`.

## Knowledge Base Pipeline (`backend/main.py`)
Triggered automatically at the end of every job. Source: master summary only.

1. **Extract** — `_extract_blocks(master)`: regex-parses all `[DEFINITION]`, `[THEOREM]`, `[CLAIM]`, `[PROOF]`, `[EXERCISE]`, `[NOTE]` blocks
2. **Load existing** — `_kb_load_existing(kb_path)`: parses `knowledge_base.md` via `<!--kb:{uuid}-->` anchors into a flat item list
3. **Unified LLM build** — `_kb_build_with_llm()`: single call using `kb_builder_skill.txt` as `system_instruction`; sends new blocks + existing items; receives merged complete item list as JSON
4. **Render** — `_kb_render()`: rebuilds full file with auto-generated TOC, sorted by `main_topic → sub_topic → tag_type order → title`; each item anchored with `<!--kb:{uuid}-->`
5. **Fallback** — `_kb_simple_append()`: if LLM call fails, raw blocks are appended chronologically

## Semantic Card System
The summary renderer parses special tag markers in the LLM output and wraps them in styled cards.

| Tag | Card color | Label |
|-----|-----------|-------|
| `[DEFINITION]` | Blue | הגדרה |
| `[THEOREM]` | Amber/Yellow | משפט |
| `[CLAIM]` | Teal | טענה |
| `[PROOF]` | Green | הוכחה |
| `[EXERCISE]` | Red | תרגיל |
| `[NOTE]` | Purple | הערה |

**Format in LLM output:**
```
#### 🔵 הגדרה: Concept Name
[DEFINITION]
body text…
```

## Frontend State (`frontend/lib/AppContext.tsx`)
Global state stored in `sessionStorage` (survives page navigation within the session):
- `selectedCourse` — course key (`combinatorics`, `data_structures`, `calculus`, `linear_algebra_2`)
- `result` — `{ chunks: [{idx, transcript, summary}], summary, transcript, totalSegments }`
- `activeJob` — `{ jobId, status, progress, totalSegments }` — used to resume polling on page refresh
- `settings` — `{ model, lang, chunkMinutes }`

Each page (`page.tsx`, `summary/page.tsx`) wraps its inner component in its own `<AppProvider>`. Navigation between pages causes AppProvider to unmount/remount and re-read from sessionStorage.

## UI Pages & Features

### Dashboard (`app/page.tsx`)
- Course selector + Knowledge Base link
- Drag-and-drop / click-to-upload for video/audio
- Test Mode (trims to first 10 min via ffmpeg)
- Fast Track: upload `.txt` transcript directly
- **Interrupted Jobs** section: lists WIP jobs with Resume / Terminate buttons

### Summary Page (`app/summary/page.tsx`)
- Segment navigator with dot strip (jump to any segment)
- Toggle between segment summary and transcript view
- **Master Summary** toggle (full-page view above segment navigator)
- Download buttons (master summary, all summaries, transcript)
- AI Tutor chat panel (right side, 35% width)
- Resumes polling from `activeJob` in sessionStorage on page refresh

### Knowledge Base Viewer (`app/knowledge-base/[course]/page.tsx`)
- Fetches and renders `knowledge_base.md` using `SummaryRenderer`

## Shared Components (`frontend/components/`)

### `SummaryRenderer.tsx`
Single source of truth for rendering summary markdown. Used on both the summary page and the knowledge base viewer.
- `parseSegments(text)` — splits text into plain and typed card segments
- `TAG_CONFIG` — maps tag types to CSS class, emoji, label
- `summaryComponents` — ReactMarkdown custom components (RTL tables)

## Skills Files
| File | Purpose | Editable? |
|------|---------|-----------|
| `summary_skill.txt` | Per-segment summarisation prompt. Defines semantic tag format. Hebrew-first language rule — use English only when the lecturer said a term in English. | Yes |
| `tutor_chat_skill.txt` | AI tutor chat system prompt (Hebrew). Pedagogical logic. | **NO** |
| `kb_builder_skill.txt` | KB merge/refinement prompt. | Yes |

## Common Commands

### Run the app
```bash
# Start both servers (Windows one-click)
start_app.bat

# Manual — backend (port 8000)
cd backend
../.venv/Scripts/uvicorn main:app --reload --port 8000

# Manual — frontend (port 3000)
cd frontend
npm run dev
```

### Frontend
```bash
cd frontend
npm run build      # production build
npm run lint       # next lint
```

### Backend utilities
```bash
# Rebuild knowledge_base.md from an existing master summary (no new lecture needed).
# Edit COURSE_DIR / SUMMARY_FILE at the top of backend/build_kb.py first.
cd backend
../.venv/Scripts/python build_kb.py
```

There is no automated test suite — verify changes by running the app end-to-end. FastAPI Swagger UI is at `http://localhost:8000/docs`.

## Important Constraints
- **Do not switch to `google-generativeai`** — use `google-genai` only
- **Do not switch back to Vertex AI** — the backend uses the Gemini Developer API with an API key; large media goes through the Files API
- **Chat must be rebuilt from history plain dicts** on every `/chat` request (no persistent chat object)
- **`skills/tutor_chat_skill.txt` must not be modified**
- The `.env` file is not committed to git
- `_legacy_streamlit_backup/` is the old Streamlit app — do not touch it
- All uploads >10 MB go directly to FastAPI (not through Next.js proxy) — see `next.config.mjs` rewrites
- CORS is configured for `http://localhost:3000` only
