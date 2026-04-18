"""
One-shot script: build knowledge_base.md from an existing master summary file.
Run from the backend/ directory:
    ../.venv/Scripts/python build_kb.py
"""
import os
import pathlib
import sys

# Force UTF-8 output so emoji in _log() don't crash on Windows cp1255 terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Load .env ──────────────────────────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).parent.parent
_env  = _ROOT / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_env, override=True)
    except ImportError:
        for line in _env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# ── Config — edit these if needed ─────────────────────────────────────────────
COURSE_DIR   = "data_structures"
SUMMARY_FILE = _ROOT / "summaries" / COURSE_DIR / "20260409_201529_de52e190c78340c5dc55d485c745f3243523d68c.txt"
MODEL        = os.getenv("KB_MODEL", "gemini-2.5-flash")
API_KEY      = os.getenv("GOOGLE_API_KEY", "")

# ── Run ────────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from main import update_knowledge_base, _log

if not API_KEY:
    print("ERROR: GOOGLE_API_KEY not set in .env", file=sys.stderr)
    sys.exit(1)

if not SUMMARY_FILE.exists():
    print(f"ERROR: summary file not found: {SUMMARY_FILE}", file=sys.stderr)
    sys.exit(1)

master = SUMMARY_FILE.read_text(encoding="utf-8")
_log(f"Loaded summary: {len(master)} chars from {SUMMARY_FILE.name}")
_log(f"Course: {COURSE_DIR}  |  Model: {MODEL}")
_log("Starting KB pipeline…\n")

kb_path = update_knowledge_base(
    course_dir = COURSE_DIR,
    file_name  = SUMMARY_FILE.name,
    master     = master,
    api_key    = API_KEY,
    model      = MODEL,
)

_log(f"\nDone → {kb_path}")
