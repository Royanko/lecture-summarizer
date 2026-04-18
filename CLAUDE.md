# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview
A web app that takes lecture recordings (MP4/MP3/WAV/M4A) or plain-text transcripts, transcribes them via the Gemini Files API, produces structured RTL Hebrew summaries per chunk with styled semantic cards, and provides an interactive tutoring chat grounded in the transcript. Each course also maintains an auto-updating **Knowledge Base** — a deduplicated, topic-organised exam study guide.

**Architecture**: Next.js 15 frontend + FastAPI backend (two separate processes).

## Stack

### Backend (`backend/`)
- **Python 3.14** with a `.venv` virtualenv
- **FastAPI** + **uvicorn** — REST API server on `http://localhost:8000`
- **`google-genai`** — Gemini API client (`from google import genai`). NEW package. Do NOT use `google-generativeai` (deprecated).
- **`python-dotenv`** — loads `GOOGLE_API_KEY` from `.env` (root or `backend/`)
- **ffmpeg** (system binary) — splits files >500 MB into 20-min chunks
- **Models**: `gemini-2.5-flash` (default), `gemini-2.5-pro` (selectable)

### Frontend (`frontend/`)
- **Next.js 15** (React 18, TypeScript)
- **Tailwind CSS**
- **`react-markdown`** + **`remark-math`** + **`rehype-katex`** — Markdown + LaTeX rendering
- **`mermaid`** — SVG diagram rendering for ` ```mermaid ` blocks
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
    page.tsx            # Dashboard — course cards, upload, KB banner
    summary/page.tsx    # Summary + tutor chat page
    knowledge-base/[course]/page.tsx  # Knowledge Base viewer page
  components/
    SummaryRenderer.tsx # Shared RTL renderer: semantic cards, LaTeX, Mermaid
    Sidebar.tsx
    CourseCard.tsx
  lib/
    api.ts              # API client helpers
    AppContext.tsx       # Global state (course, result, chat history)
skills/
  summary_skill.txt     # System prompt for per-segment summarisation (Hebrew)
  tutor_chat_skill.txt  # System prompt for AI tutor chat. DO NOT MODIFY.
  kb_builder_skill.txt  # System prompt for KB merge/refinement LLM call
recordings/             # Drop large lecture files here
summaries/
  <course>/
    *.txt               # Saved master summaries (timestamped)
    knowledge_base.md   # Auto-updated course knowledge base
transcriptions/         # Saved transcription files
start_app.bat           # Launches both servers (Windows)
_legacy_streamlit_backup/  # Old Streamlit app — ignore completely
```

## Backend API Endpoints (`backend/main.py`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/models` | List available Gemini models |
| POST | `/upload` | Start a video/audio processing job; returns `{job_id, ...}` |
| POST | `/upload-transcript` | Fast-track: start job from `.txt` transcript, skips transcription |
| GET | `/jobs/{job_id}` | Poll job status + progressive chunk results |
| POST | `/chat` | Send a chat message; returns assistant reply |
| GET | `/knowledge-base/{course_id}` | Return `{course_id, course_label, content}` for KB viewer |

### Job flow
1. `POST /upload` or `POST /upload-transcript` → creates background job, returns `job_id`
2. Client polls `GET /jobs/{job_id}` until `status == "done"` or `"error"`
3. On `"done"`, response includes `chunks` (list of `{idx, transcript, summary}`), `summary` (master), `transcript` (full)
4. After saving outputs, the KB pipeline runs automatically

## Processing Flow (`backend/main.py`)
1. **Receive file** via `/upload`; save to temp dir
2. **Large file (>500 MB)**: `split_video()` uses ffmpeg to cut into 20-min chunks
3. **Per chunk** — `transcribe_segment()`:
   - `client.files.upload()` → poll until `state.name == "ACTIVE"` → `generate_content` with file URI → `client.files.delete()`
4. **Per chunk** — `summarize_segment()`: `generate_content` with transcript + `summary_skill.txt`
5. **Master summary** — `generate_master_summary()`: combines all chunk summaries
6. **Save outputs** — `save_outputs()`: writes to `summaries/<course>/` and `transcriptions/`
7. **Knowledge Base** — `update_knowledge_base()`: 5-step pipeline (see below)
8. **Chat** — `/chat` endpoint rebuilds `client.chats.create(...)` from `history` plain dicts on every request

## Knowledge Base Pipeline (`backend/main.py`)
Triggered automatically at the end of every job. Source: master summary only.

1. **Extract** — `_extract_blocks(master)`: regex-parses all `[DEFINITION]`, `[THEOREM]`, `[CLAIM]`, `[PROOF]`, `[EXERCISE]`, `[NOTE]` blocks
2. **Load existing** — `_kb_load_existing(kb_path)`: parses `knowledge_base.md` via `<!--kb:{uuid}-->` anchors into a flat item list
3. **Unified LLM build** — `_kb_build_with_llm()`: single call using `kb_builder_skill.txt` as `system_instruction`; sends new blocks + existing items; receives merged complete item list as JSON
4. **Render** — `_kb_render()`: rebuilds full file with auto-generated TOC, sorted by `main_topic → sub_topic → tag_type order → title`; each item anchored with `<!--kb:{uuid}-->`
5. **Fallback** — `_kb_simple_append()`: if LLM call fails, raw blocks are appended chronologically

KB file location: `summaries/<course>/knowledge_base.md`

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

The heading line is optional but recommended. The parser uses a regex that captures the heading line + tag as a single unit to avoid "bleeding header" bugs.

## Mermaid Diagrams
- Detected via ` ```mermaid ` code blocks in markdown
- Rendered client-side as SVG via the `mermaid` npm package (lazy-initialised)
- `MermaidDiagram` component in `SummaryRenderer.tsx`: async render, removes hardcoded SVG height for responsiveness, shows raw code fallback on parse error
- `summary_skill.txt` explicitly forbids ASCII art and instructs the LLM to use Mermaid for all visual diagrams (trees, graphs, flowcharts)

## Shared Components (`frontend/components/`)

### `SummaryRenderer.tsx`
The single source of truth for rendering summary markdown. Used on both the summary page and the knowledge base viewer.
- `parseSegments(text)` — splits text into plain segments and typed card segments
- `TAG_CONFIG` — maps tag types to CSS class, emoji, label
- `MermaidDiagram` — renders ` ```mermaid ` blocks as SVG
- `summaryComponents` — ReactMarkdown custom components (RTL tables, mermaid intercept)

## Key google.genai API Patterns
```python
from google import genai
from google.genai import types

client = genai.Client(api_key=KEY)

# Upload file
f = client.files.upload(file="path.mp4", config=types.UploadFileConfig(display_name="x", mime_type="video/mp4"))

# Poll until active
while f.state.name == "PROCESSING":
    time.sleep(4)
    f = client.files.get(name=f.name)

# Generate with file
r = client.models.generate_content(model=MODEL, contents=[
    types.Content(role="user", parts=[
        types.Part.from_uri(file_uri=f.uri, mime_type=f.mime_type),
        types.Part(text="prompt")
    ])
])

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

## Frontend State (`frontend/lib/AppContext.tsx`)
Global state stored in `sessionStorage` (survives page navigation within the session):
- `selectedCourse` — course key (`combinatorics`, `data_structures`, `calculus`, `linear_algebra_2`)
- `result` — `{ chunks: [{idx, transcript, summary}], summary, transcript, totalSegments }`
- `activeJob` — `{ jobId, status, progress, totalSegments }` — used to resume polling on refresh
- `settings` — `{ model, lang, apiKey, chunkMinutes }`
- Chat history — local component state on the summary page, seeded from `chat_seed`

## UI Pages & Features

### Dashboard (`app/page.tsx`)
- Course selector cards (4 courses)
- Drag-and-drop / click-to-upload for video/audio
- Test Mode button (trims to first 10 minutes via ffmpeg)
- Fast Track: upload `.txt` transcript directly
- **Knowledge Base banner** — prominent link to `/knowledge-base/{course}` for the selected course

### Summary Page (`app/summary/page.tsx`)
- Segment navigator with dot strip (jump to any segment)
- Toggle between segment summary and transcript view
- **Master Summary** button: toggles a full-page view of the master summary above the segment navigator
- Download buttons for master summary, all summaries, transcript
- AI Tutor chat panel (right side, 35% width)
- Live "processing" state with progressive segment loading

### Knowledge Base Viewer (`app/knowledge-base/[course]/page.tsx`)
- Fetches `knowledge_base.md` from `GET /api/knowledge-base/{course}`
- Renders using `SummaryRenderer` — same semantic card UI as summary page
- Download `.md` button
- Empty state with call-to-action when no KB exists yet

## Skills Files
| File | Purpose | Editable? |
|------|---------|-----------|
| `summary_skill.txt` | Per-segment summarisation prompt. Defines semantic tag format, forbids ASCII art, instructs Mermaid usage | Yes |
| `tutor_chat_skill.txt` | AI tutor chat system prompt (Hebrew). Pedagogical logic. | **NO** |
| `kb_builder_skill.txt` | KB merge/refinement prompt. Instructs academic editing style, no full proofs, Mermaid/table visuals | Yes |

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
npm run start      # serve the production build
npm run lint       # next lint
```

### Backend utilities
```bash
# Rebuild knowledge_base.md from an existing master summary (no new lecture needed).
# Edit COURSE_DIR / SUMMARY_FILE at the top of backend/build_kb.py first.
cd backend
../.venv/Scripts/python build_kb.py
```

There is no automated test suite in this repo — verify changes by running the app end-to-end.

### Interactive API docs
FastAPI serves Swagger UI at http://localhost:8000/docs once the backend is running.

## Important Constraints
- Do not switch to `google-generativeai` — use `google-genai` only
- Chat must be rebuilt from history plain dicts on every `/chat` request (no persistent chat object)
- `skills/tutor_chat_skill.txt` (or `tutor_skill.txt`) must not be modified
- The `.env` file is not committed to git
- `_legacy_streamlit_backup/` is the old Streamlit app — do not touch it
- All uploads >10 MB go directly to FastAPI (not through Next.js proxy) — see `next.config.mjs` rewrites
