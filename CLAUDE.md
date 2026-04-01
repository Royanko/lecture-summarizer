# Lecture Summarizer — Project Guide

## Overview
A Streamlit web app that takes lecture recordings (MP4/MP3/WAV/M4A), transcribes them via the Gemini Files API, produces structured summaries, and provides an interactive tutoring chat grounded in the transcript.

## Stack
- **Python 3.14** with a `.venv` virtualenv
- **Streamlit** — UI framework
- **`google-genai`** — Gemini API client (`from google import genai`). This is the NEW package. Do NOT use `google-generativeai` (deprecated).
- **`fpdf2`** — optional PDF export
- **`sympy`** — optional LaTeX → unicode rendering for PDFs
- **`python-dotenv`** — loads `GOOGLE_API_KEY` from `.env`
- **ffmpeg** (system binary) — splits files >500 MB into 20-min chunks
- **Model**: `gemini-2.5-flash` (constant `MODEL` at top of `app.py`)

## File Structure
```
app.py                  # Main Streamlit app (all logic lives here)
skills/tutor_skill.txt  # System prompt for the Gemini tutor (Hebrew). DO NOT MODIFY.
requirements.txt        # streamlit, google-genai, python-dotenv, fpdf2, sympy
recordings/             # Drop large lecture files here (MP4/MP3/WAV/M4A)
.env                    # GOOGLE_API_KEY=... (not committed)
```

## Processing Flow (`app.py`)
1. **File input**: browser upload (≤200 MB) OR pick from `recordings/` folder
2. **Large file (>500 MB)**: ffmpeg splits into 20-min chunks; user clicks "Summarize Next Part" per chunk
3. **Transcription** (`transcribe_file`): upload via `client.files.upload()` → poll until `state.name == "ACTIVE"` → `generate_content` with the file URI + prompt → delete file from Gemini
4. **Summarization** (`summarize_chunk`): `generate_content` with transcript text + `system_instruction=system_prompt`
5. **Chat seeding**: transcript is injected as first user message in `chat_history_raw`
6. **Chat**: on every message, rebuild `client.chats.create(...)` from `chat_history_raw` (plain dicts) to avoid stale object issues across Streamlit reruns

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

# Generate text only
r = client.models.generate_content(model=MODEL, contents="text",
    config=types.GenerateContentConfig(system_instruction="..."))

# Chat (rebuild from history each time)
chat = client.chats.create(
    model=MODEL,
    config=types.GenerateContentConfig(system_instruction="..."),
    history=[types.Content(role=h["role"], parts=[types.Part(text=h["text"])]) for h in history_dicts]
)
response = chat.send_message("question")
```

## Session State Keys
| Key | Type | Description |
|-----|------|-------------|
| `transcription` | str\|None | Full accumulated transcript text |
| `summary` | str\|None | Latest chunk summary |
| `chunk_summaries` | list | `[{idx, transcript, summary}]` per chunk |
| `chunks_remaining` | list | File paths of unprocessed chunks |
| `chunk_dir_path` | str\|None | Temp dir holding chunk files (cleaned up when done) |
| `current_chunk_idx` | int | Index of last processed chunk |
| `total_chunks_count` | int | Total number of chunks |
| `processing_mime` | str\|None | MIME type for chunked processing |
| `processing_fname` | str\|None | Original filename for chunked processing |
| `chat_display` | list | `[{role, content}]` for UI rendering |
| `chat_history_raw` | list | `[{role, text}]` plain dicts for rebuilding chat |
| `api_key` | str | Gemini API key |
| `ready` | bool | True after first successful processing |
| `video_path` | str\|None | Local path to video for `st.video` player |
| `video_start_time` | int | Seek position in seconds for video player |

## UI Features
- **Dark theme**: `#0a0a0f` background, purple-blue gradient accents, Syne + Inter fonts
- **Sidebar**: API key input (auto-loaded from `.env`), lecture language selector (Hebrew/English/Arabic), auto-delete local video checkbox
- **Summary display**: `[DEFINITION]`, `[THEOREM]`, `[PROOF]` tags rendered as styled HTML cards
- **Timestamp extraction**: `[MM:SS]` / `[HH:MM:SS]` in transcript → clickable jump buttons for video player
- **Download**: summary `.txt`, transcription `.txt`, optional PDF export
- **Chat**: text input + "Send" button; "יאללה נמשיך" triggers next topic in tutor flow

## Tutor Skill (`skills/tutor_skill.txt`)
Hebrew-language system prompt. The tutor:
- Corrects STT errors in the transcript before teaching
- Presents one sub-topic at a time, waits for student confirmation
- Uses fixed structure per topic: concepts → explanation → code/math → exam tip
- Uses LaTeX for math, Markdown for formatting
- Ends with a 5–10 question multiple-choice quiz
- **DO NOT MODIFY this file** — it is the core pedagogical logic

## Running the App
```bash
# Install dependencies
pip install -r requirements.txt

# Set API key
echo GOOGLE_API_KEY=your_key_here > .env

# Run
streamlit run app.py
```

## Important Constraints
- Do not switch to `google-generativeai` — use `google-genai` only
- Chat must be rebuilt from `chat_history_raw` on every Streamlit rerun (not stored as an object in session state)
- `skills/tutor_skill.txt` must not be modified
- The `.env` file is not committed to git
