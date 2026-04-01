import streamlit as st
import os
import re
import json
import time
import tempfile
import subprocess
import shutil
import datetime
from google import genai
from google.genai import types

# ── Optional dependencies ──────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False

try:
    from sympy.parsing.latex import parse_latex
    from sympy.printing.pretty import pretty as sympy_pretty
    SYMPY_AVAILABLE = True
except ImportError:
    SYMPY_AVAILABLE = False

# ── Constants ──────────────────────────────────────────────────────────────────
AVAILABLE_MODELS = {
    "Gemini 2.5 Flash ⚡ (Recommended)": "gemini-2.5-flash",
    "Gemini 2.5 Pro 🧠 (Most capable)":  "gemini-2.5-pro",
}

COURSES = {
    "קומבינטוריקה — Combinatorics":          "combinatorics",
    "מבני נתונים — Data Structures":          "data_structures",
    'חדו"א — Calculus':                        "calculus",
    "אלגברה לינארית 2 — Linear Algebra 2":    "linear_algebra_2",
}

MIME_MAP = {".mp4": "video/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}
LANG_MAP = {
    "Hebrew (עברית)": ("he", "The lecture is in Hebrew. Transcribe in Hebrew only."),
    "English":         ("en", "The lecture is in English. Transcribe in English only."),
    "Arabic (عربية)":  ("ar", "The lecture is in Arabic. Transcribe in Arabic only."),
}

# Create course directories on startup
for _d in COURSES.values():
    os.makedirs(f"recordings/{_d}", exist_ok=True)
    os.makedirs(f"summaries/{_d}", exist_ok=True)
    os.makedirs(f"transcriptions/{_d}", exist_ok=True)

with open("skills/tutor_skill.txt", "r", encoding="utf-8") as f:
    TUTOR_SYSTEM_PROMPT = f.read()

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BGU Lecture Summarizer",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS + KaTeX + RTL ──────────────────────────────────────────────────────────
st.markdown("""
<!-- KaTeX for LaTeX rendering -->
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css"
      crossorigin="anonymous">
<script defer
        src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"
        crossorigin="anonymous"></script>
<script defer
        src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
        crossorigin="anonymous"></script>
<script>
(function() {
    var OPTS = {
        delimiters: [
            {left: "$$", right: "$$", display: true},
            {left: "$",  right: "$",  display: false}
        ],
        throwOnError: false
    };
    function render() {
        if (window.renderMathInElement) {
            renderMathInElement(document.body, OPTS);
        }
    }
    // First render after scripts load
    window.addEventListener("load", function() { setTimeout(render, 200); });
    // Re-render on every Streamlit rerun (DOM mutations)
    var observer = new MutationObserver(function() { setTimeout(render, 150); });
    document.addEventListener("DOMContentLoaded", function() {
        observer.observe(document.body, {childList: true, subtree: true});
    });
})();
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Syne:wght@700;800&display=swap');

/* ── Reset & base ── */
* { box-sizing: border-box; }
body, * { font-family: 'Inter', sans-serif !important; }
h1, h2, h3, h4 { font-family: 'Syne', sans-serif !important; }

/* ── White academic background ── */
.stApp, .main, [data-testid="stAppViewContainer"] {
    background: #ffffff !important;
    color: #0f172a !important;
}
[data-testid="stHeader"] { background: #ffffff !important; }

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: #f8fafc !important;
    border-right: 1px solid #e2e8f0 !important;
}
section[data-testid="stSidebar"] * { color: #1e293b !important; }
section[data-testid="stSidebar"] .stSuccess {
    background: #f0fdf4 !important;
    border: 1px solid #86efac !important;
    border-radius: 8px !important;
    color: #166534 !important;
    font-size: 0.85rem;
}

/* ── Hero header ── */
.bgu-header {
    background: #0f172a;
    border-radius: 14px;
    padding: 2rem 2.5rem 1.8rem;
    margin-bottom: 1.5rem;
    position: relative;
    overflow: hidden;
}
.bgu-header::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 4px;
    background: linear-gradient(90deg, #f97316, #fb923c, #f97316);
}
.bgu-title {
    font-family: 'Syne', sans-serif !important;
    font-size: 2.2rem; font-weight: 800;
    color: #f97316; margin: 0 0 0.3rem 0; line-height: 1.2;
}
.bgu-subtitle { color: #94a3b8; font-size: 0.95rem; margin: 0; }
.bgu-badge {
    display: inline-block;
    background: rgba(249,115,22,0.15);
    border: 1px solid rgba(249,115,22,0.4);
    color: #f97316; border-radius: 6px;
    padding: 0.18rem 0.7rem; font-size: 0.7rem;
    font-weight: 700; margin-top: 0.8rem; letter-spacing: 0.08em;
}

/* ── Content cards (white theme) ── */
.card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 1.4rem 1.6rem;
    margin: 0.7rem 0;
    color: #0f172a;
    direction: rtl;
    text-align: right;
}
/* Code/math inside cards stays LTR */
.card pre, .card code, .card .katex-display { direction: ltr; text-align: left; }

/* ── Segment label ── */
.seg-label {
    font-family: 'Syne', sans-serif;
    font-size: 0.82rem; font-weight: 700;
    color: #f97316; text-transform: uppercase;
    letter-spacing: 0.08em;
    border-bottom: 2px solid #e2e8f0;
    padding-bottom: 0.4rem; margin-bottom: 0.8rem;
}

/* ── Tag blocks ── */
.tag-definition {
    background: #eff6ff; border-left: 4px solid #3b82f6;
    border-radius: 4px 10px 10px 4px;
    padding: 0.65rem 1rem; margin: 0.4rem 0;
    color: #1e40af; direction: rtl; text-align: right;
}
.tag-theorem {
    background: #f0fdf4; border-left: 4px solid #22c55e;
    border-radius: 4px 10px 10px 4px;
    padding: 0.65rem 1rem; margin: 0.4rem 0;
    color: #166534; direction: rtl; text-align: right;
}
.tag-proof {
    background: #fffbeb; border-left: 4px solid #f59e0b;
    border-radius: 4px 10px 10px 4px;
    padding: 0.65rem 1rem; margin: 0.4rem 0;
    color: #92400e; direction: rtl; text-align: right;
}
/* LTR for math inside tags */
.tag-definition .katex-display,
.tag-theorem .katex-display,
.tag-proof .katex-display { direction: ltr; text-align: center; }

/* ── Buttons ── */
.stButton > button {
    background: linear-gradient(135deg, #f97316, #ea580c) !important;
    color: white !important; border: none !important;
    border-radius: 8px !important; padding: 0.55rem 1.5rem !important;
    font-family: 'Syne', sans-serif !important; font-weight: 700 !important;
    font-size: 0.92rem !important;
}
.stButton > button:hover { opacity: 0.88 !important; }
.stButton > button:disabled { background: #cbd5e1 !important; opacity: 1 !important; }

/* ── Inputs ── */
.stTextInput > div > div > input {
    background: #f8fafc !important; border: 1px solid #cbd5e1 !important;
    border-radius: 8px !important; color: #0f172a !important;
}
.stTextInput > div > div > input:focus {
    border-color: #f97316 !important;
    box-shadow: 0 0 0 2px rgba(249,115,22,0.15) !important;
}
.stTextArea textarea {
    background: #f8fafc !important; border: 1px solid #cbd5e1 !important;
    border-radius: 8px !important; color: #0f172a !important;
    direction: rtl; text-align: right;
    font-size: 0.9rem !important; line-height: 1.7 !important;
}

/* ── Selectbox ── */
.stSelectbox > div > div {
    background: #f8fafc !important; border: 1px solid #cbd5e1 !important;
    border-radius: 8px !important; color: #0f172a !important;
}

/* ── File uploader ── */
.stFileUploader {
    background: #f8fafc !important; border: 2px dashed #cbd5e1 !important;
    border-radius: 12px !important;
}

/* ── Expander ── */
div[data-testid="stExpander"] {
    background: #f8fafc !important; border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
}

/* ── Progress bar ── */
.stProgress > div > div > div {
    background: linear-gradient(90deg, #f97316, #fb923c) !important;
    border-radius: 4px !important;
}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"] {
    background: #f1f5f9 !important; border-radius: 10px; padding: 0.25rem; gap: 0.25rem;
}
.stTabs [data-baseweb="tab"] {
    background: transparent !important; color: #64748b !important;
    border-radius: 7px !important; font-family: 'Syne', sans-serif !important;
    font-weight: 700 !important; font-size: 0.88rem !important;
}
.stTabs [aria-selected="true"] {
    background: #f97316 !important; color: white !important;
}

/* ── Chat bubbles (RTL) ── */
.chat-user {
    background: #eff6ff; border: 1px solid #bfdbfe;
    border-radius: 14px 14px 4px 14px;
    padding: 0.75rem 1.1rem; margin: 0.5rem 0 0.5rem auto;
    max-width: 78%; color: #1e40af;
    direction: rtl; text-align: right;
}
.chat-ai {
    background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 14px 14px 14px 4px;
    padding: 0.75rem 1.1rem; margin: 0.5rem auto 0.5rem 0;
    max-width: 78%; color: #0f172a;
    direction: rtl; text-align: right;
}
/* LTR for math in chat */
.chat-ai .katex-display, .chat-user .katex-display { direction: ltr; text-align: center; }

/* ── Metrics ── */
div[data-testid="metric-container"] {
    background: #f8fafc !important; border: 1px solid #e2e8f0 !important;
    border-radius: 10px !important;
}
div[data-testid="metric-container"] label { color: #64748b !important; font-size: 0.78rem !important; }
div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
    color: #f97316 !important; font-family: 'Syne', sans-serif !important;
}

/* ── Course badge ── */
.course-badge {
    display: inline-block;
    background: #0f172a; color: white;
    border-radius: 8px; padding: 0.25rem 0.85rem;
    font-size: 0.82rem; font-weight: 600;
    font-family: 'Syne', sans-serif;
    margin-bottom: 0.5rem;
}

/* ── Save path info ── */
.save-info {
    background: #f0fdf4; border: 1px solid #86efac;
    border-radius: 8px; padding: 0.7rem 1rem;
    color: #166534; font-size: 0.85rem;
    direction: ltr; text-align: left;
    margin-top: 0.5rem;
}

/* ── Divider ── */
hr { border-color: #e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
_env_key = os.getenv("GOOGLE_API_KEY", "")

_defaults = {
    "api_key":          _env_key,
    "transcription":    None,
    "master_summary":   None,
    "chunk_summaries":  [],
    "chat_display":     [],
    "chat_history_raw": [],
    "ready":            False,
    "video_path":       None,
    "video_start_time": 0,
    "saved_summary_path":     None,
    "saved_transcript_path":  None,
    "current_course":   None,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configuration")

    # API key — hidden if already loaded from .env
    if _env_key:
        st.success("🔑 API Key loaded from .env")
    else:
        api_key_input = st.text_input(
            "🔑 Gemini API Key",
            type="password",
            placeholder="Paste your Gemini API key...",
            value=st.session_state.api_key,
        )
        if api_key_input:
            st.session_state.api_key = api_key_input

    st.markdown("---")

    model_label    = st.selectbox("🤖 Model", list(AVAILABLE_MODELS.keys()), index=0)
    selected_model = AVAILABLE_MODELS[model_label]

    lang_label      = st.selectbox("🌐 Lecture Language", list(LANG_MAP.keys()), index=0)
    lang_code, lang_hint = LANG_MAP[lang_label]

    chunk_minutes = st.slider("✂️ Segment Length (min)", min_value=5, max_value=20, value=12, step=1)

    auto_delete = st.checkbox("🗑️ Delete local video after processing", value=True)

    st.markdown("---")
    st.markdown("### 📋 How to use")
    st.markdown("""
1. Select a **course** from the dropdown
2. Upload a file or pick from `recordings/`
3. Click **Process** or **Quick Test**
4. Browse tabs: Summary · Segments · Transcript
5. Chat with the AI tutor!
    """)
    st.markdown("---")
    st.markdown(
        '<div style="color:#94a3b8;font-size:0.78rem;">BGU Lecture Summarizer · Powered by Gemini</div>',
        unsafe_allow_html=True,
    )

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="bgu-header">
    <div class="bgu-title">🎓 BGU Lecture Summarizer</div>
    <div class="bgu-subtitle">
        Upload a lecture → AI segments, transcribes &amp; summarizes → Interactive tutor with LaTeX rendering
    </div>
    <div class="bgu-badge">POWERED BY GEMINI</div>
</div>
""", unsafe_allow_html=True)

# ── Course selector ────────────────────────────────────────────────────────────
course_label    = st.selectbox("📚 Course", list(COURSES.keys()), index=0)
course_dir_name = COURSES[course_label]

# ── File selection ─────────────────────────────────────────────────────────────
col_up, col_rec = st.columns([1, 1])
with col_up:
    uploaded_file = st.file_uploader(
        "📁 Upload lecture (MP4, MP3, WAV, M4A)",
        type=["mp4", "mp3", "wav", "m4a"],
    )
with col_rec:
    course_rec_dir = os.path.join("recordings", course_dir_name)
    rec_files = sorted(
        f for f in os.listdir(course_rec_dir)
        if f.lower().endswith((".mp4", ".mp3", ".wav", ".m4a"))
    )
    if rec_files:
        selected_file = st.selectbox(
            f"📂 Or pick from recordings/{course_dir_name}/",
            ["-- Select --"] + rec_files,
        )
    else:
        selected_file = None
        st.info(f"No files in recordings/{course_dir_name}/")

use_local = bool(selected_file and selected_file != "-- Select --")
has_file  = bool(uploaded_file or use_local)
has_key   = bool(st.session_state.api_key)

# ── Metrics row ────────────────────────────────────────────────────────────────
if st.session_state.chunk_summaries:
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Segments", len(st.session_state.chunk_summaries))
    with m2:
        words = len((st.session_state.transcription or "").split())
        st.metric("Words", f"{words:,}")
    with m3:
        st.metric("Course", course_dir_name.replace("_", " ").title()[:14])
    with m4:
        st.metric("Model", selected_model.split("-")[-1].upper())

# ── Action buttons ─────────────────────────────────────────────────────────────
btn_col, test_col, reset_col, _ = st.columns([1, 1, 1, 3])
with btn_col:
    process_btn = st.button("✨ Process Lecture", disabled=(not has_file or not has_key))
with test_col:
    test_btn = st.button(
        "🧪 Quick Test",
        disabled=(not has_file or not has_key),
        help="Process only the first segment for a quick preview.",
    )
with reset_col:
    if st.session_state.ready:
        if st.button("🔄 Reset"):
            for k, v in _defaults.items():
                st.session_state[k] = v
            st.rerun()

# ── Helpers ────────────────────────────────────────────────────────────────────
def get_mime(filename: str) -> str:
    return MIME_MAP.get(os.path.splitext(filename)[1].lower(), "video/mp4")


def split_video(input_path: str, output_dir: str, chunk_sec: int) -> list:
    ext     = os.path.splitext(input_path)[1]
    pattern = os.path.join(output_dir, f"seg_%03d{ext}")
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", input_path, "-c", "copy", "-map", "0",
             "-segment_time", str(chunk_sec), "-f", "segment",
             "-reset_timestamps", "1", pattern],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode == 0:
            chunks = sorted(
                os.path.join(output_dir, f)
                for f in os.listdir(output_dir)
                if f.startswith("seg_") and f.endswith(ext)
            )
            if chunks:
                return chunks
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return [input_path]


def transcribe_segment(
    client, file_path: str, file_name: str, mime_type: str,
    lang_hint: str, model: str,
) -> str:
    prompt = (
        f"{lang_hint} "
        "Transcribe this lecture segment completely and accurately. "
        "Include all spoken content and technical terms. "
        "Wrap ALL mathematical expressions in LaTeX: inline with $...$ and display with $$...$$. "
        "Mark whiteboard content with [BOARD: ...]."
    )
    gf = client.files.upload(
        file=file_path,
        config=types.UploadFileConfig(display_name=file_name, mime_type=mime_type),
    )
    poll = 0
    while gf.state.name == "PROCESSING":
        time.sleep(4)
        gf = client.files.get(name=gf.name)
        poll += 1
        if poll > 75:
            try: client.files.delete(name=gf.name)
            except Exception: pass
            raise TimeoutError(f"Timeout: {file_name}")
    if gf.state.name != "ACTIVE":
        raise RuntimeError(f"File failed (state={gf.state.name}): {file_name}")
    resp = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=[
            types.Part.from_uri(file_uri=gf.uri, mime_type=gf.mime_type),
            types.Part(text=prompt),
        ])],
    )
    try: client.files.delete(name=gf.name)
    except Exception: pass
    return resp.text


def summarize_segment(
    client, transcript: str, seg_num: int, total: int, model: str,
) -> str:
    excerpt = transcript[:6000]
    prompt  = (
        f"Lecture segment {seg_num}/{total}. "
        "Summarize concisely in Hebrew. "
        "ALL mathematical expressions MUST be in LaTeX: inline $...$ or display $$...$$. "
        "Structure: key concepts → main points → formulas/algorithms. "
        "Use [DEFINITION: ...] [THEOREM: ...] [PROOF: ...] tags where relevant.\n\n"
        f"Transcript:\n{excerpt}"
    )
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=TUTOR_SYSTEM_PROMPT),
    )
    return resp.text


def generate_master_summary(client, chunk_summaries: list, model: str) -> str:
    joined = "\n\n---\n\n".join(
        f"Segment {e['idx'] + 1}:\n{e['summary']}" for e in chunk_summaries
    )
    prompt = (
        "Create a unified master summary in Hebrew from these lecture segment summaries. "
        "ALL mathematical expressions MUST use LaTeX: inline $...$ or display $$...$$. "
        "Include: all topics in order, key definitions/theorems/algorithms, exam-critical points. "
        "Use [DEFINITION: ...] [THEOREM: ...] [PROOF: ...] tags.\n\n"
        f"Segment summaries:\n{joined}"
    )
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=TUTOR_SYSTEM_PROMPT),
    )
    return resp.text


def save_outputs(course_dir: str, base_name: str, master: str, transcript: str) -> tuple:
    """Save summary and transcript to course folders. Returns (summary_path, transcript_path)."""
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = re.sub(r'[^\w\-.]', '_', os.path.splitext(base_name)[0])[:40]
    s_path = os.path.join("summaries",      course_dir, f"{ts}_{name}.txt")
    t_path = os.path.join("transcriptions", course_dir, f"{ts}_{name}.txt")
    with open(s_path, "w", encoding="utf-8") as f:
        f.write(master)
    with open(t_path, "w", encoding="utf-8") as f:
        f.write(transcript)
    return s_path, t_path


def parse_timestamps(text: str) -> list:
    seen, results = set(), []
    for m in re.finditer(r'\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]', text):
        lbl = m.group(0)
        if lbl in seen:
            continue
        seen.add(lbl)
        secs = (
            int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if m.group(3)
            else int(m.group(1)) * 60 + int(m.group(2))
        )
        results.append({"label": lbl, "seconds": secs})
    return results


def render_math_str(latex_str: str) -> str:
    if SYMPY_AVAILABLE:
        try:
            return sympy_pretty(parse_latex(latex_str.strip()), use_unicode=True)
        except Exception:
            pass
    s = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'(\1)/(\2)', latex_str)
    s = re.sub(r'\\[a-zA-Z]+', lambda m: m.group(0)[1:], s)
    return s.replace("{", "").replace("}", "")


def render_summary_html(text: str) -> str:
    """Convert [DEFINITION/THEOREM/PROOF] tags to styled HTML divs.
    LaTeX ($...$) is left intact for KaTeX auto-render in the browser."""
    TAG_CLASS = {"DEFINITION": "tag-definition", "THEOREM": "tag-theorem", "PROOF": "tag-proof"}
    TAG_LABEL = {"DEFINITION": "📘 הגדרה", "THEOREM": "📗 משפט", "PROOF": "📙 הוכחה"}

    def make_div(tag: str, content: str) -> str:
        css = TAG_CLASS.get(tag, "card")
        lbl = TAG_LABEL.get(tag, f"[{tag}]")
        return f'<div class="{css}" dir="rtl"><strong>{lbl}:</strong> {content.strip()}</div>'

    text = re.sub(
        r'\[(DEFINITION|THEOREM|PROOF)\](.*?)\[/\1\]',
        lambda m: make_div(m.group(1).upper(), m.group(2)),
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r'\[(DEFINITION|THEOREM|PROOF):\s*(.*?)\]',
        lambda m: make_div(m.group(1).upper(), m.group(2)),
        text, flags=re.IGNORECASE,
    )
    return text


def generate_pdf_bytes(chunk_summaries: list, master_summary: str) -> bytes | None:
    if not FPDF_AVAILABLE:
        return None

    class LecturePDF(FPDF):
        def __init__(self):
            super().__init__()
            self.main_font = "Helvetica"
            for reg, bold in [
                ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"),
                ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ]:
                if os.path.exists(reg):
                    try:
                        self.add_font("UniF", fname=reg)
                        if os.path.exists(bold):
                            self.add_font("UniF", style="B", fname=bold)
                        self.main_font = "UniF"
                        break
                    except Exception:
                        pass

        def header(self):
            self.set_font(self.main_font, "B", 16)
            self.set_text_color(249, 115, 22)
            self.cell(0, 10, "BGU Lecture Summary", ln=True, align="C")
            self.set_draw_color(249, 115, 22)
            self.set_line_width(0.4)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(6)

        def footer(self):
            self.set_y(-15)
            self.set_font(self.main_font, "", 8)
            self.set_text_color(128, 128, 128)
            self.cell(0, 10, f"Page {self.page_no()}", align="C")

        def write_section(self, text: str):
            TAG_INFO = {
                "DEFINITION": ((59, 130, 246), "DEFINITION"),
                "THEOREM":    ((34, 197, 94),  "THEOREM"),
                "PROOF":      ((245, 158, 11), "PROOF"),
            }
            pos, pat = 0, re.compile(
                r'\[(DEFINITION|THEOREM|PROOF)(?::\s*(.*?))?\](.*?)(?:\[/\1\]|(?=\[(DEFINITION|THEOREM|PROOF)|\Z))',
                re.DOTALL | re.IGNORECASE,
            )
            for m in pat.finditer(text):
                before = text[pos:m.start()].strip()
                if before: self._write_plain(before)
                tag   = m.group(1).upper()
                body  = ((m.group(2) or "") + " " + (m.group(3) or "")).strip()
                color, lbl = TAG_INFO.get(tag, ((100, 100, 100), tag))
                self.set_font(self.main_font, "B", 9)
                self.set_text_color(*color)
                self.cell(0, 6, f"[{lbl}]", ln=True)
                self.set_text_color(30, 30, 30)
                self.set_font(self.main_font, "", 9)
                if body: self._write_plain(body)
                self.ln(2)
                pos = m.end()
            rest = text[pos:].strip()
            if rest: self._write_plain(rest)

        def _write_plain(self, text: str):
            self.set_font(self.main_font, "", 9)
            self.set_text_color(30, 30, 30)
            text = re.sub(r'\$\$(.*?)\$\$', lambda m: "\n" + render_math_str(m.group(1)) + "\n",
                          text, flags=re.DOTALL)
            text = re.sub(r'\$((?:[^$\\]|\\.)+)\$', lambda m: render_math_str(m.group(1)), text)
            self.multi_cell(0, 5, text)

    try:
        pdf = LecturePDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_page()
        pdf.set_font(pdf.main_font, "B", 13)
        pdf.set_text_color(249, 115, 22)
        pdf.cell(0, 9, "Master Summary", ln=True)
        pdf.ln(3)
        pdf.write_section(master_summary)
        for entry in chunk_summaries:
            pdf.add_page()
            pdf.set_font(pdf.main_font, "B", 12)
            pdf.set_text_color(249, 115, 22)
            pdf.cell(0, 9, f"Segment {entry['idx'] + 1}", ln=True)
            pdf.ln(3)
            pdf.write_section(entry["summary"])
        return bytes(pdf.output())
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def _cached_pdf(summaries_json: str, master: str) -> bytes | None:
    return generate_pdf_bytes(json.loads(summaries_json), master)


# ════════════════════════════════════════════════════════════════════════════════
# SHARED PROCESSING LOGIC
# ════════════════════════════════════════════════════════════════════════════════
def _resolve_file():
    """Returns (file_path, file_name, local_to_delete, tmp_path)."""
    if use_local:
        fp = os.path.join("recordings", course_dir_name, selected_file)
        return fp, selected_file, (fp if auto_delete else None), None
    else:
        suffix = os.path.splitext(uploaded_file.name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name
        return tmp_path, uploaded_file.name, None, tmp_path


def _finalize(chunk_summaries, full_transcript, master, file_name, course_dir, video_src):
    """Store results in session state, save to disk, seed chat."""
    s_path, t_path = save_outputs(course_dir, file_name, master, full_transcript.strip())

    st.session_state.chat_history_raw = [
        {"role": "user",  "text": f"סיכום מאסטר של ההרצאה:\n{master}"},
        {"role": "model", "text": "קראתי את סיכום ההרצאה. אני מוכן ללמד אותך נושא אחד בכל פעם!"},
    ]
    st.session_state.chat_display           = []
    st.session_state.transcription          = full_transcript.strip()
    st.session_state.master_summary         = master
    st.session_state.chunk_summaries        = chunk_summaries
    st.session_state.ready                  = True
    st.session_state.current_course         = course_label
    st.session_state.saved_summary_path     = s_path
    st.session_state.saved_transcript_path  = t_path
    st.session_state.video_path             = video_src
    st.session_state.video_start_time       = 0


# ════════════════════════════════════════════════════════════════════════════════
# QUICK TEST — first segment only
# ════════════════════════════════════════════════════════════════════════════════
if test_btn and has_file and has_key:
    client    = genai.Client(api_key=st.session_state.api_key)
    chunk_sec = chunk_minutes * 60
    tmp_path  = None
    chunk_dir = None

    progress_bar = st.progress(0, text="Starting Quick Test…")
    status       = st.empty()

    try:
        file_path, file_name, local_to_delete, tmp_path = _resolve_file()
        mime_type = get_mime(file_name)

        progress_bar.progress(0.1, text="✂️ Extracting first segment…")
        status.info(f"✂️ Extracting first ~{chunk_minutes}-minute segment…")
        chunk_dir  = tempfile.mkdtemp(prefix="bgu_test_")
        chunks     = split_video(file_path, chunk_dir, chunk_sec)
        first_seg  = chunks[0]
        base, ext  = os.path.splitext(file_name)
        chunk_name = f"{base}_test_seg001{ext}"

        progress_bar.progress(0.25, text="🎙️ Transcribing…")
        status.info("🎙️ Transcribing first segment…")
        transcript = transcribe_segment(client, first_seg, chunk_name, mime_type, lang_hint, selected_model)

        progress_bar.progress(0.70, text="🤖 Summarizing…")
        status.info("🤖 Summarizing segment…")
        summary = summarize_segment(client, transcript, 1, len(chunks), selected_model)

        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True); chunk_dir = None
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass

        video_src = None if (use_local and auto_delete) else file_path
        _finalize(
            [{"idx": 0, "transcript": transcript, "summary": summary}],
            f"--- Segment 1 (Quick Test) ---\n\n{transcript}",
            summary, file_name, course_dir_name, video_src,
        )

        progress_bar.progress(1.0, text="✅ Quick Test ready!")
        status.success(
            f"✅ First segment processed! Full lecture has ~{len(chunks)} segment(s). "
            "Scroll down to explore."
        )
        time.sleep(1.5)
        progress_bar.empty(); status.empty()
        st.rerun()

    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
        progress_bar.empty(); status.empty()
        st.error(f"❌ Quick Test error: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# FULL PROCESSING
# ════════════════════════════════════════════════════════════════════════════════
if process_btn and has_file and has_key:
    client    = genai.Client(api_key=st.session_state.api_key)
    chunk_sec = chunk_minutes * 60
    tmp_path  = None
    chunk_dir = None

    progress_bar = st.progress(0, text="Starting…")
    status       = st.empty()

    try:
        file_path, file_name, local_to_delete, tmp_path = _resolve_file()
        mime_type = get_mime(file_name)

        video_src = None if (use_local and auto_delete) else file_path
        st.session_state.video_path = video_src
        st.session_state.video_start_time = 0

        progress_bar.progress(0.04, text="✂️ Splitting into segments…")
        status.info(f"✂️ Splitting into ~{chunk_minutes}-minute segments…")
        chunk_dir = tempfile.mkdtemp(prefix="bgu_segs_")
        chunks    = split_video(file_path, chunk_dir, chunk_sec)
        n         = len(chunks)
        status.info(f"📊 {n} segment(s) detected — starting analysis…")

        chunk_summaries = []
        full_transcript = ""

        for i, chunk_path in enumerate(chunks):
            seg_label  = f"Segment {i + 1}/{n}"
            base, ext  = os.path.splitext(file_name)
            chunk_name = f"{base}_seg{i + 1:03d}{ext}"

            progress_bar.progress(0.08 + (i / n) * 0.72, text=f"🎙️ Transcribing {seg_label}…")
            status.info(f"🎙️ Transcribing {seg_label}…")
            transcript = transcribe_segment(client, chunk_path, chunk_name, mime_type, lang_hint, selected_model)

            progress_bar.progress(0.08 + (i / n) * 0.72 + (0.72 / n) * 0.5, text=f"🤖 Summarizing {seg_label}…")
            status.info(f"🤖 Summarizing {seg_label}…")
            summary = summarize_segment(client, transcript, i + 1, n, selected_model)

            chunk_summaries.append({"idx": i, "transcript": transcript, "summary": summary})
            full_transcript += f"\n\n--- {seg_label} ---\n\n{transcript}"

        progress_bar.progress(0.86, text="🧠 Generating master summary…")
        status.info("🧠 Aggregating all segments into master summary…")
        master = generate_master_summary(client, chunk_summaries, selected_model)

        if local_to_delete and os.path.exists(local_to_delete):
            os.remove(local_to_delete); video_src = None
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True); chunk_dir = None

        _finalize(chunk_summaries, full_transcript, master, file_name, course_dir_name, video_src)

        progress_bar.progress(1.0, text="✅ Done!")
        status.success(f"✅ {n} segment(s) processed and saved to {course_dir_name}/")
        if auto_delete and use_local:
            st.toast("🗑️ Local video deleted")
        time.sleep(1.5)
        progress_bar.empty(); status.empty()
        st.rerun()

    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
        progress_bar.empty(); status.empty()
        st.error(f"❌ Error: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# RESULTS
# ════════════════════════════════════════════════════════════════════════════════
if st.session_state.chunk_summaries:
    st.markdown("---")

    # Course badge
    if st.session_state.current_course:
        st.markdown(
            f'<div class="course-badge">📚 {st.session_state.current_course}</div>',
            unsafe_allow_html=True,
        )

    # Saved paths info
    if st.session_state.saved_summary_path:
        st.markdown(
            f'<div class="save-info">'
            f'💾 Summary → <code>{st.session_state.saved_summary_path}</code><br>'
            f'💾 Transcript → <code>{st.session_state.saved_transcript_path}</code>'
            f'</div>',
            unsafe_allow_html=True,
        )

    has_video  = bool(st.session_state.video_path and os.path.exists(st.session_state.video_path))
    timestamps = parse_timestamps(st.session_state.transcription or "")

    if has_video:
        content_col, vid_col = st.columns([3, 2])
    else:
        content_col = st.container()

    # ── Video player ──────────────────────────────────────────────────────
    if has_video:
        with vid_col:
            st.markdown("### 🎬 Lecture Video")
            try:
                st.video(st.session_state.video_path, start_time=st.session_state.video_start_time)
            except Exception:
                st.video(st.session_state.video_path)
            if timestamps:
                st.markdown("#### 🕒 Jump to Timestamp")
                for ts in timestamps[:20]:
                    if st.button(ts["label"], key=f"ts_{ts['seconds']}"):
                        st.session_state.video_start_time = ts["seconds"]
                        st.rerun()

    # ── Tabs ──────────────────────────────────────────────────────────────
    with content_col:
        tab_master, tab_segs, tab_trans = st.tabs([
            "🎯 Master Summary",
            "📦 Segment Summaries",
            "📝 Raw Transcription",
        ])

        with tab_master:
            if st.session_state.master_summary:
                rendered = render_summary_html(st.session_state.master_summary)
                st.markdown(f'<div class="card" dir="rtl">{rendered}</div>', unsafe_allow_html=True)

        with tab_segs:
            total = len(st.session_state.chunk_summaries)
            for entry in st.session_state.chunk_summaries:
                st.markdown(
                    f'<div class="seg-label">📦 Segment {entry["idx"] + 1} / {total}</div>',
                    unsafe_allow_html=True,
                )
                rendered = render_summary_html(entry["summary"])
                st.markdown(f'<div class="card" dir="rtl">{rendered}</div>', unsafe_allow_html=True)

        with tab_trans:
            st.text_area(
                "Full Transcription",
                value=st.session_state.transcription or "",
                height=420,
                label_visibility="collapsed",
            )

        # ── Downloads ─────────────────────────────────────────────────────
        st.markdown("---")
        full_export = (
            "=== MASTER SUMMARY ===\n\n"
            + (st.session_state.master_summary or "")
            + "\n\n=== SEGMENT SUMMARIES ===\n\n"
            + "\n\n---\n\n".join(
                f"Segment {e['idx'] + 1}:\n{e['summary']}"
                for e in st.session_state.chunk_summaries
            )
        )
        n_cols  = 3 if FPDF_AVAILABLE else 2
        dl_cols = st.columns(n_cols)
        with dl_cols[0]:
            st.download_button("⬇️ Summary (.txt)", full_export, "summary.txt", "text/plain")
        with dl_cols[1]:
            st.download_button(
                "⬇️ Transcription (.txt)",
                st.session_state.transcription or "",
                "transcription.txt", "text/plain",
            )
        if FPDF_AVAILABLE:
            with dl_cols[2]:
                _pdf_key  = json.dumps(
                    [{"idx": e["idx"], "summary": e["summary"]} for e in st.session_state.chunk_summaries]
                )
                pdf_bytes = _cached_pdf(_pdf_key, st.session_state.master_summary or "")
                if pdf_bytes:
                    st.download_button("📄 Export PDF", pdf_bytes, "lecture_summary.pdf", "application/pdf")
                else:
                    st.caption("PDF unavailable — run `pip install fpdf2`")


# ════════════════════════════════════════════════════════════════════════════════
# CHAT
# ════════════════════════════════════════════════════════════════════════════════
if st.session_state.ready:
    st.markdown("---")
    st.markdown("## 💬 AI Tutor — Ask Questions or Continue Learning")

    for msg in st.session_state.chat_display:
        if msg["role"] == "user":
            st.markdown(f'<div class="chat-user">🙋 {msg["content"]}</div>', unsafe_allow_html=True)
        else:
            rendered_ans = render_summary_html(msg["content"])
            st.markdown(f'<div class="chat-ai">🤖 {rendered_ans}</div>', unsafe_allow_html=True)

    question = st.text_input(
        "Ask the tutor…",
        placeholder="שאל שאלה על ההרצאה, או כתוב 'יאללה נמשיך' להמשך...",
    )

    ask_col, clear_col, _ = st.columns([1, 1, 4])
    with ask_col:
        ask_btn = st.button("Send →", disabled=not question)
    with clear_col:
        if st.button("🗑️ Clear Chat"):
            st.session_state.chat_display    = []
            st.session_state.chat_history_raw = st.session_state.chat_history_raw[:2]
            st.rerun()

    if ask_btn and question and st.session_state.api_key:
        client  = genai.Client(api_key=st.session_state.api_key)
        history = [
            types.Content(role=h["role"], parts=[types.Part(text=h["text"])])
            for h in st.session_state.chat_history_raw
        ]
        chat = client.chats.create(
            model=selected_model,
            config=types.GenerateContentConfig(system_instruction=TUTOR_SYSTEM_PROMPT),
            history=history,
        )
        with st.spinner("🤔 Thinking…"):
            response = chat.send_message(question)
            answer   = response.text

        st.session_state.chat_display.append({"role": "user",  "content": question})
        st.session_state.chat_display.append({"role": "ai",    "content": answer})
        st.session_state.chat_history_raw.append({"role": "user",  "text": question})
        st.session_state.chat_history_raw.append({"role": "model", "text": answer})
        st.rerun()
