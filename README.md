# BGU Lecture Summarizer

An AI-powered tool that transcribes and summarizes university lecture recordings using Google Gemini. Supports Hebrew, English, and Arabic lectures.

## Features

- Automatically splits long lectures into segments and processes each one
- Generates a **Master Summary** aggregating all segments
- Interactive AI tutor chat grounded in the lecture content
- Supports MP4, MP3, WAV, M4A files
- Dark themed UI with BGU colors

## Quick Start

### 1. Set up your API key

Copy `.env.example` to `.env` and fill in your Gemini API key:

```
GOOGLE_API_KEY=your_gemini_api_key_here
```

Get a free key at [Google AI Studio](https://aistudio.google.com/).

### 2. Run the app

**Double-click** `run_BGU_AI.bat` on your desktop (or from the project folder).

This will activate the virtual environment and launch the app at `http://localhost:8501`.

### 3. First-time setup

If you haven't created the desktop shortcut yet, run once in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "scripts\create_shortcut.ps1"
```

## Manual run (terminal)

```bash
# Install dependencies (first time only)
pip install -r requirements.txt

# Run
streamlit run app.py
```

## Project Structure

```
lecture-summarizer/
├── app.py                  # Main Streamlit application
├── run_BGU_AI.bat          # One-click launcher
├── requirements.txt
├── .env                    # Your API key (not committed)
├── .env.example            # Template for .env
├── assets/
│   └── bgu_ai.ico          # App icon
├── scripts/
│   ├── create_shortcut.ps1 # Creates desktop shortcut
│   └── download_test.py    # Dev/testing utility
├── skills/
│   └── tutor_skill.txt     # AI tutor system prompt
└── recordings/             # Place lecture files here (not committed)
```
