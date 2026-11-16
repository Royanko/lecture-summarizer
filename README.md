# Lecture Summarizer v2.0

An AI-powered academic tool that transcribes university lecture recordings, generates structured Hebrew summaries with styled semantic cards, and builds a continuously-updating **Knowledge Base** for each course — all powered by Google Gemini.

## Features

- **Transcription** — uploads MP4/MP3/WAV/M4A directly to Gemini Files API; auto-splits files >500 MB with ffmpeg
- **Structured Summaries** — per-segment summaries with colour-coded semantic cards: Definitions (blue), Theorems (amber), Claims (teal), Proofs (green), Exercises (red), Notes (purple)
- **LaTeX rendering** — full KaTeX support for inline and block math
- **Mermaid diagrams** — ` ```mermaid ` blocks render as interactive SVG (trees, graphs, flowcharts); ASCII art is forbidden in prompts
- **Master Summary** — aggregated summary of the full lecture, toggled via a dedicated button
- **AI Tutor Chat** — Hebrew-language Socratic tutor, grounded in the lecture content
- **Fast Track** — skip transcription by uploading a `.txt` transcript directly
- **Test Mode** — trims to first 10 minutes for quick API key and quality checks
- **Course Knowledge Base** — per-course `knowledge_base.md` that auto-updates after every lecture, deduplicated and topic-organised by Gemini using `kb_builder_skill.txt`
- **KB Viewer** — browse and download the knowledge base from inside the app
- RTL Hebrew UI, dark BGU colour theme

## Quick Start

### 1. Get a Gemini API key
Get a free key at [Google AI Studio](https://aistudio.google.com/) and create `backend/.env`:
```
GOOGLE_API_KEY=your_key_here
```

### 2. Install dependencies (first time only)

**Backend:**
```bash
cd backend
python -m venv ../.venv
../.venv/Scripts/activate   # Windows
pip install -r requirements.txt
```

**Frontend:**
```bash
cd frontend
npm install
```

**ffmpeg** — install from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.

### 3. Run the app

**Windows — double-click:**
```
start_app.bat
```

**Manual:**
```bash
# Terminal 1 — backend
cd backend
../.venv/Scripts/uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev
```

Open **http://localhost:3000**.

## How It Works

```
Upload lecture file
       │
       ▼
 ffmpeg split (if >500MB)
       │
       ▼
 Gemini Files API transcription (per chunk)
       │
       ▼
 Gemini summarisation (per chunk) ──► per-chunk summary streamed to UI
       │
       ▼
 Master summary (all chunks combined)
       │
       ▼
 Knowledge Base pipeline
   ├─ extract semantic blocks
   ├─ load existing KB
   ├─ Gemini merge/dedup (kb_builder_skill)
   └─ render full KB with TOC → knowledge_base.md
```

## Knowledge Base

After every lecture, the system automatically:
1. Extracts all `[DEFINITION]`, `[THEOREM]`, `[CLAIM]`, `[PROOF]`, `[EXERCISE]`, `[NOTE]` blocks from the master summary
2. Sends them to Gemini alongside the existing KB
3. Gemini merges, deduplicates, categorises by logical topic (not chronologically), adds Mermaid visuals and truth tables where useful
4. Rebuilds `summaries/<course>/knowledge_base.md` with a clickable Table of Contents

Access it from the dashboard via the **"ספר הקורס"** banner, or download it as a `.md` file.

## Project Structure

```
lecture-summarizer/
├── backend/
│   ├── main.py               # FastAPI app — all processing + KB pipeline
│   ├── requirements.txt
│   └── .env                  # GOOGLE_API_KEY (not committed)
├── frontend/
│   ├── app/
│   │   ├── page.tsx          # Dashboard
│   │   ├── summary/          # Summary + tutor chat
│   │   └── knowledge-base/   # KB viewer (/knowledge-base/[course])
│   ├── components/
│   │   └── SummaryRenderer.tsx  # Shared renderer (cards, LaTeX, Mermaid)
│   └── lib/
│       ├── api.ts            # API client
│       └── AppContext.tsx    # Global state
├── skills/
│   ├── summary_skill.txt     # Summarisation system prompt
│   ├── tutor_chat_skill.txt  # Tutor chat system prompt (DO NOT MODIFY)
│   └── kb_builder_skill.txt  # Knowledge Base builder prompt
├── summaries/
│   └── <course>/
│       ├── *.txt             # Per-lecture master summaries
│       └── knowledge_base.md # Auto-updated course KB
├── transcriptions/           # Saved transcripts
├── recordings/               # Drop lecture files here
└── start_app.bat             # One-click launcher (Windows)
```

## Supported Courses

| Key | Name |
|-----|------|
| `combinatorics` | קומבינטוריקה — Combinatorics |
| `data_structures` | מבני נתונים — Data Structures |
| `calculus` | חדו"א — Calculus |
| `linear_algebra_2` | אלגברה לינארית 2 — Linear Algebra 2 |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/models` | Available Gemini models |
| POST | `/upload` | Start video/audio job |
| POST | `/upload-transcript` | Start job from `.txt` transcript |
| GET | `/jobs/{job_id}` | Poll job status + progressive results |
| POST | `/chat` | Tutor chat message |
| GET | `/knowledge-base/{course_id}` | Fetch KB markdown content |

Interactive docs: **http://localhost:8000/docs**

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15, React 18, TypeScript, Tailwind CSS |
| Rendering | react-markdown, remark-math, rehype-katex, mermaid |
| Backend | Python 3.14, FastAPI, uvicorn |
| AI | Google Gemini 2.5 Flash / Pro via `google-genai` |
| Video | ffmpeg |
