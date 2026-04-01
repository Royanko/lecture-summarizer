import streamlit as st
import os
import re
import json
import time
import tempfile
import subprocess
import shutil
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
MIME_MAP = {".mp4": "video/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}
LANG_MAP = {
    "Hebrew (עברית)": ("he", "ההרצאה בעברית. תמלל בעברית בלבד."),
    "English":         ("en", "The lecture is in English. Transcribe in English only."),
    "Arabic (عربية)":  ("ar", "المحاضرة بالعربية. فرغها بالعربية فقط."),
}

with open("skills/tutor_skill.txt", "r", encoding="utf-8") as f:
    TUTOR_SYSTEM_PROMPT = f.read()

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="BGU Lecture Summarizer",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── BGU Theme CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Syne:wght@700;800&display=swap');

    * { font-family: 'Inter', sans-serif; box-sizing: border-box; }
    h1, h2, h3, h4 { font-family: 'Syne', sans-serif !important; }

    /* ── App background (BGU dark navy) ── */
    .stApp, .main { background: #08111e !important; color: #e2e8f0 !important; }

    /* ── Sidebar ── */
    section[data-testid="stSidebar"] {
        background: #060e19 !important;
        border-right: 1px solid #152840 !important;
    }
    section[data-testid="stSidebar"] * { color: #cbd5e1 !important; }

    /* ── Hero header ── */
    .bgu-header {
        background: linear-gradient(135deg, #0d1f38 0%, #112845 100%);
        border: 1px solid #1e3a5f;
        border-radius: 16px;
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
        font-size: 2.4rem;
        font-weight: 800;
        color: #f97316;
        margin: 0 0 0.3rem 0;
        line-height: 1.2;
    }
    .bgu-subtitle { color: #94a3b8; font-size: 1rem; margin: 0; }
    .bgu-badge {
        display: inline-block;
        background: rgba(249,115,22,0.12);
        border: 1px solid rgba(249,115,22,0.3);
        color: #f97316;
        border-radius: 6px;
        padding: 0.18rem 0.7rem;
        font-size: 0.72rem;
        font-weight: 600;
        margin-top: 0.8rem;
        letter-spacing: 0.07em;
    }

    /* ── Cards ── */
    .card {
        background: #0f1f38;
        border: 1px solid #1a3050;
        border-radius: 12px;
        padding: 1.4rem 1.6rem;
        margin: 0.7rem 0;
        color: #e2e8f0;
    }

    /* ── Segment label ── */
    .seg-label {
        font-family: 'Syne', sans-serif;
        font-size: 0.82rem;
        font-weight: 700;
        color: #f97316;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        border-bottom: 1px solid #1a3050;
        padding-bottom: 0.4rem;
        margin-bottom: 0.8rem;
    }

    /* ── Tag styled blocks ── */
    .tag-definition {
        background: #091f3a; border-left: 4px solid #3b82f6;
        border-radius: 4px 10px 10px 4px; padding: 0.65rem 1rem;
        margin: 0.4rem 0; color: #bfdbfe;
    }
    .tag-theorem {
        background: #082818; border-left: 4px solid #22c55e;
        border-radius: 4px 10px 10px 4px; padding: 0.65rem 1rem;
        margin: 0.4rem 0; color: #a7f3d0;
    }
    .tag-proof {
        background: #221600; border-left: 4px solid #f59e0b;
        border-radius: 4px 10px 10px 4px; padding: 0.65rem 1rem;
        margin: 0.4rem 0; color: #fde68a;
    }

    /* ── Buttons ── */
    .stButton > button {
        background: linear-gradient(135deg, #f97316, #ea580c) !important;
        color: white !important; border: none !important;
        border-radius: 8px !important; padding: 0.55rem 1.6rem !important;
        font-family: 'Syne', sans-serif !important; font-weight: 700 !important;
        font-size: 0.95rem !important; transition: opacity 0.2s, transform 0.1s !important;
    }
    .stButton > button:hover { opacity: 0.87 !important; transform: translateY(-1px) !important; }
    .stButton > button:disabled { opacity: 0.35 !important; transform: none !important; background: #374151 !important; }

    /* ── Inputs ── */
    .stTextInput > div > div > input,
    .stTextArea textarea {
        background: #08111e !important; border: 1px solid #1e3a5f !important;
        border-radius: 8px !important; color: #e2e8f0 !important;
    }
    .stTextInput > div > div > input:focus,
    .stTextArea textarea:focus {
        border-color: #f97316 !important;
        box-shadow: 0 0 0 2px rgba(249,115,22,0.2) !important;
    }
    .stSelectbox > div > div, .stSlider {
        color: #e2e8f0 !important;
    }

    /* ── File uploader ── */
    .stFileUploader {
        background: #0f1f38 !important; border: 2px dashed #1e3a5f !important;
        border-radius: 12px !important;
    }

    /* ── Expander ── */
    div[data-testid="stExpander"] {
        background: #0f1f38 !important; border: 1px solid #1a3050 !important;
        border-radius: 10px !important;
    }

    /* ── Progress bar ── */
    .stProgress > div > div > div {
        background: linear-gradient(90deg, #f97316, #fb923c) !important;
        border-radius: 4px !important;
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        background: #060e19 !important; border-radius: 10px;
        padding: 0.25rem; gap: 0.25rem;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent !important; color: #94a3b8 !important;
        border-radius: 7px !important; font-family: 'Syne', sans-serif !important;
        font-weight: 700 !important; font-size: 0.9rem !important;
    }
    .stTabs [aria-selected="true"] {
        background: #f97316 !important; color: white !important;
    }

    /* ── Chat bubbles ── */
    .chat-user {
        background: #1a3050; border: 1px solid #2563eb;
        border-radius: 14px 14px 4px 14px; padding: 0.75rem 1.1rem;
        margin: 0.5rem 0 0.5rem auto; max-width: 78%;
        color: #bfdbfe; text-align: right;
    }
    .chat-ai {
        background: #0f1f38; border: 1px solid #1a3050;
        border-radius: 14px 14px 14px 4px; padding: 0.75rem 1.1rem;
        margin: 0.5rem auto 0.5rem 0; max-width: 78%; color: #e2e8f0;
    }

    /* ── Metrics ── */
    div[data-testid="metric-container"] {
        background: #0f1f38 !important; border: 1px solid #1a3050 !important;
        border-radius: 10px !important; padding: 0.9rem 1rem !important;
    }
    div[data-testid="metric-container"] label { color: #94a3b8 !important; font-size: 0.8rem !important; }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] { color: #f97316 !important; font-family: 'Syne', sans-serif !important; }

    /* ── Alerts ── */
    div[data-baseweb="notification"] { border-radius: 8px !important; }
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
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Configuration")

    api_key_input = st.text_input(
        "🔑 Gemini API Key",
        type="password",
        placeholder="Auto-loaded from .env ✓" if _env_key else "Paste your API key...",
        value=st.session_state.api_key,
    )
    if api_key_input:
        st.session_state.api_key = api_key_input
    if _env_key:
        st.success("Key loaded from .env ✓")

    st.markdown("---")

    model_label = st.selectbox("🤖 Model", list(AVAILABLE_MODELS.keys()), index=0)
    selected_model = AVAILABLE_MODELS[model_label]

    lang_label = st.selectbox("🌐 Lecture Language", list(LANG_MAP.keys()), index=0)
    lang_code, lang_hint = LANG_MAP[lang_label]

    chunk_minutes = st.slider("✂️ Segment Length (minutes)", min_value=5, max_value=20, value=12, step=1)

    auto_delete = st.checkbox("🗑️ Delete local video after processing", value=True)

    st.markdown("---")
    st.markdown("### 📋 How to use")
    st.markdown("""
1. Paste your Gemini API key (or set in `.env`)
2. Upload a file **or** pick from `recordings/`
3. Click **Process Lecture**
4. Browse results in the tabs
5. Chat with the AI tutor below!
    """)
    st.markdown("---")
    st.markdown(
        '<div style="color:#475569;font-size:0.78rem;line-height:1.5;">'
        'BGU Lecture Summarizer<br>Powered by Google Gemini'
        '</div>',
        unsafe_allow_html=True,
    )

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="bgu-header">
    <div class="bgu-title">🎓 BGU Lecture Summarizer</div>
    <div class="bgu-subtitle">
        Upload a lecture recording → Gemini segments, transcribes &amp; summarizes → Interactive AI tutor
    </div>
    <div class="bgu-badge">POWERED BY GEMINI</div>
</div>
""", unsafe_allow_html=True)

# ── File selection ─────────────────────────────────────────────────────────────
col_up, col_rec = st.columns([1, 1])
with col_up:
    uploaded_file = st.file_uploader(
        "📁 Upload lecture (MP4, MP3, WAV, M4A)",
        type=["mp4", "mp3", "wav", "m4a"],
    )
with col_rec:
    os.makedirs("recordings", exist_ok=True)
    rec_files = sorted(
        f for f in os.listdir("recordings")
        if f.lower().endswith((".mp4", ".mp3", ".wav", ".m4a"))
    )
    if rec_files:
        selected_file = st.selectbox("📂 Or pick from recordings/", ["-- Select --"] + rec_files)
    else:
        selected_file = None
        st.info("No files found in recordings/")

use_local = bool(selected_file and selected_file != "-- Select --")
has_file  = bool(uploaded_file or use_local)
has_key   = bool(st.session_state.api_key)

# ── Metrics row (shown after processing) ──────────────────────────────────────
if st.session_state.chunk_summaries:
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Segments", len(st.session_state.chunk_summaries))
    with m2:
        words = len((st.session_state.transcription or "").split())
        st.metric("Words", f"{words:,}")
    with m3:
        st.metric("Model", selected_model.split("-")[-1].upper())
    with m4:
        st.metric("Language", lang_label.split("(")[0].strip())

# ── Action buttons ─────────────────────────────────────────────────────────────
btn_col, test_col, reset_col, _ = st.columns([1, 1, 1, 3])
with btn_col:
    process_btn = st.button(
        "✨ Process Lecture",
        disabled=(not has_file or not has_key),
    )
with test_col:
    test_btn = st.button(
        "🧪 Quick Test",
        disabled=(not has_file or not has_key),
        help="Transcribe & summarize only the first segment so you can test Gemini Q&A quickly.",
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
    """Split video into fixed-length segments. Falls back to [input_path] on error."""
    ext     = os.path.splitext(input_path)[1]
    pattern = os.path.join(output_dir, f"seg_%03d{ext}")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", input_path, "-c", "copy", "-map", "0",
                "-segment_time", str(chunk_sec), "-f", "segment",
                "-reset_timestamps", "1", pattern,
            ],
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
    return [input_path]  # fallback: treat whole file as one segment


def transcribe_segment(
    client, file_path: str, file_name: str, mime_type: str,
    lang_hint: str, model: str,
) -> str:
    """Upload → poll ACTIVE → transcribe → delete. Returns transcript text."""
    prompt = (
        f"{lang_hint} "
        "Transcribe this lecture segment completely and accurately. "
        "Include all spoken words, technical terms, and formulas. "
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
            raise TimeoutError(f"Timeout waiting for Gemini to process: {file_name}")
    if gf.state.name != "ACTIVE":
        raise RuntimeError(f"File processing failed (state={gf.state.name}): {file_name}")
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
    """Concise per-segment summary — keeps prompt short to save tokens."""
    # Cap transcript to ~6 000 chars; Gemini will still capture key content
    excerpt = transcript[:6000]
    prompt  = (
        f"Lecture segment {seg_num}/{total}. "
        "Summarize concisely in Hebrew. "
        "Structure: key concepts → main points → formulas/code/algorithms. "
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
    """Aggregate all segment summaries into one cohesive master summary."""
    joined = "\n\n---\n\n".join(
        f"Segment {e['idx'] + 1}:\n{e['summary']}" for e in chunk_summaries
    )
    prompt = (
        "You have summaries of every segment of an academic lecture. "
        "Write a unified master summary in Hebrew that:\n"
        "• Covers all topics in logical order\n"
        "• Lists all key definitions, theorems, and algorithms\n"
        "• Highlights exam-critical points\n"
        "• Uses [DEFINITION: ...] [THEOREM: ...] [PROOF: ...] tags\n\n"
        f"Segment summaries:\n{joined}"
    )
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=TUTOR_SYSTEM_PROMPT),
    )
    return resp.text


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
        ctx = text[max(0, m.start() - 40): min(len(text), m.end() + 50)].replace("\n", " ").strip()
        results.append({"label": lbl, "seconds": secs, "context": ctx})
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
    TAG_CLASS = {"DEFINITION": "tag-definition", "THEOREM": "tag-theorem", "PROOF": "tag-proof"}
    TAG_LABEL = {"DEFINITION": "📘 הגדרה", "THEOREM": "📗 משפט", "PROOF": "📙 הוכחה"}

    def make_div(tag: str, content: str) -> str:
        css = TAG_CLASS.get(tag, "card")
        lbl = TAG_LABEL.get(tag, f"[{tag}]")
        return f'<div class="{css}"><strong>{lbl}:</strong> {content.strip()}</div>'

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
            self.set_text_color(249, 115, 22)   # BGU orange
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
                "DEFINITION": ((59, 130, 246),  "DEFINITION"),
                "THEOREM":    ((34, 197, 94),   "THEOREM"),
                "PROOF":      ((245, 158, 11),  "PROOF"),
            }
            pos = 0
            pat = re.compile(
                r'\[(DEFINITION|THEOREM|PROOF)(?::\s*(.*?))?\](.*?)(?:\[/\1\]|(?=\[(DEFINITION|THEOREM|PROOF)|\Z))',
                re.DOTALL | re.IGNORECASE,
            )
            for m in pat.finditer(text):
                before = text[pos:m.start()].strip()
                if before:
                    self._write_plain(before)
                tag   = m.group(1).upper()
                body  = ((m.group(2) or "") + " " + (m.group(3) or "")).strip()
                color, lbl = TAG_INFO.get(tag, ((150, 150, 150), tag))
                self.set_font(self.main_font, "B", 9)
                self.set_text_color(*color)
                self.cell(0, 6, f"[{lbl}]", ln=True)
                self.set_text_color(220, 220, 230)
                self.set_font(self.main_font, "", 9)
                if body:
                    self._write_plain(body)
                self.ln(2)
                pos = m.end()
            rest = text[pos:].strip()
            if rest:
                self._write_plain(rest)

        def _write_plain(self, text: str):
            self.set_font(self.main_font, "", 9)
            self.set_text_color(220, 220, 230)
            text = re.sub(
                r'\$\$(.*?)\$\$',
                lambda m: "\n" + render_math_str(m.group(1)) + "\n",
                text, flags=re.DOTALL,
            )
            text = re.sub(
                r'\$((?:[^$\\]|\\.)+)\$',
                lambda m: render_math_str(m.group(1)),
                text,
            )
            self.multi_cell(0, 5, text)

    try:
        pdf = LecturePDF()
        pdf.set_auto_page_break(auto=True, margin=20)

        # Master summary page
        pdf.add_page()
        pdf.set_font(pdf.main_font, "B", 13)
        pdf.set_text_color(249, 115, 22)
        pdf.cell(0, 9, "Master Summary", ln=True)
        pdf.ln(3)
        pdf.write_section(master_summary)

        # Per-segment pages
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
        # Resolve file
        if use_local:
            file_path = os.path.join("recordings", selected_file)
            file_name = selected_file
        else:
            status.info("💾 Saving uploaded file…")
            suffix = os.path.splitext(uploaded_file.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name
            file_path, file_name = tmp_path, uploaded_file.name

        mime_type = get_mime(file_name)

        # Split and take only first segment
        progress_bar.progress(0.1, text="✂️ Extracting first segment…")
        status.info(f"✂️ Extracting first ~{chunk_minutes}-minute segment…")
        chunk_dir = tempfile.mkdtemp(prefix="bgu_test_")
        chunks    = split_video(file_path, chunk_dir, chunk_sec)
        first_seg = chunks[0]

        base, ext  = os.path.splitext(file_name)
        chunk_name = f"{base}_test_seg001{ext}"

        progress_bar.progress(0.25, text="🎙️ Transcribing segment 1…")
        status.info("🎙️ Transcribing first segment…")
        transcript = transcribe_segment(
            client, first_seg, chunk_name, mime_type, lang_hint, selected_model,
        )

        progress_bar.progress(0.75, text="🤖 Summarizing…")
        status.info("🤖 Summarizing segment…")
        summary = summarize_segment(client, transcript, 1, len(chunks), selected_model)

        # Cleanup
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
            chunk_dir = None
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass

        # Seed chat with this single segment (token-efficient)
        st.session_state.chat_history_raw = [
            {"role": "user",  "text": f"סיכום של קטע בדיקה מההרצאה:\n{summary}"},
            {"role": "model", "text": "קראתי את הקטע. אני מוכן לענות על שאלות!"},
        ]
        st.session_state.chat_display    = []
        st.session_state.transcription   = f"--- Segment 1 (Quick Test) ---\n\n{transcript}"
        st.session_state.master_summary  = summary
        st.session_state.chunk_summaries = [{"idx": 0, "transcript": transcript, "summary": summary}]
        st.session_state.video_path      = None
        st.session_state.ready           = True

        n_total = len(chunks)
        progress_bar.progress(1.0, text="✅ Quick Test ready!")
        status.success(
            f"✅ First segment processed! "
            f"(Full lecture has ~{n_total} segment(s) total.) "
            "Scroll down to chat with the tutor."
        )
        time.sleep(1.5)
        progress_bar.empty()
        status.empty()
        st.rerun()

    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
        progress_bar.empty()
        status.empty()
        st.error(f"❌ Quick Test error: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# PROCESSING
# ════════════════════════════════════════════════════════════════════════════════
if process_btn and has_file and has_key:
    client    = genai.Client(api_key=st.session_state.api_key)
    chunk_sec = chunk_minutes * 60
    tmp_path  = None
    chunk_dir = None

    progress_bar = st.progress(0, text="Starting…")
    status       = st.empty()

    try:
        # ── Resolve file path ─────────────────────────────────────────────
        if use_local:
            file_path = os.path.join("recordings", selected_file)
            file_name = selected_file
            local_to_delete = file_path if auto_delete else None
        else:
            status.info("💾 Saving uploaded file…")
            suffix = os.path.splitext(uploaded_file.name)[1]
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded_file.read())
                tmp_path = tmp.name
            file_path, file_name = tmp_path, uploaded_file.name
            local_to_delete = None

        mime_type = get_mime(file_name)

        # Keep temp file for video player (unless auto-delete local)
        st.session_state.video_path = (
            None if (use_local and auto_delete) else file_path
        )
        st.session_state.video_start_time = 0

        # ── Split into segments ───────────────────────────────────────────
        progress_bar.progress(0.04, text="✂️ Splitting into segments…")
        status.info(f"✂️ Splitting lecture into ~{chunk_minutes}-minute segments…")
        chunk_dir = tempfile.mkdtemp(prefix="bgu_segs_")
        chunks    = split_video(file_path, chunk_dir, chunk_sec)
        n         = len(chunks)
        status.info(f"📊 {n} segment(s) detected — starting analysis…")

        # ── Process each segment ──────────────────────────────────────────
        chunk_summaries = []
        full_transcript = ""

        for i, chunk_path in enumerate(chunks):
            seg_label  = f"Segment {i + 1}/{n}"
            base, ext  = os.path.splitext(file_name)
            chunk_name = f"{base}_seg{i + 1:03d}{ext}"

            # Transcribe
            prog = 0.08 + (i / n) * 0.72
            progress_bar.progress(prog, text=f"🎙️ Transcribing {seg_label}…")
            status.info(f"🎙️ Transcribing {seg_label}…")
            transcript = transcribe_segment(
                client, chunk_path, chunk_name, mime_type, lang_hint, selected_model,
            )

            # Summarize
            prog2 = 0.08 + (i / n) * 0.72 + (0.72 / n) * 0.5
            progress_bar.progress(prog2, text=f"🤖 Summarizing {seg_label}…")
            status.info(f"🤖 Summarizing {seg_label}…")
            summary = summarize_segment(client, transcript, i + 1, n, selected_model)

            chunk_summaries.append({"idx": i, "transcript": transcript, "summary": summary})
            full_transcript += f"\n\n--- {seg_label} ---\n\n{transcript}"

        # ── Master summary ────────────────────────────────────────────────
        progress_bar.progress(0.85, text="🧠 Generating master summary…")
        status.info("🧠 Aggregating all segments into a master summary…")
        master = generate_master_summary(client, chunk_summaries, selected_model)

        # ── Cleanup ───────────────────────────────────────────────────────
        if local_to_delete and os.path.exists(local_to_delete):
            os.remove(local_to_delete)
            st.session_state.video_path = None
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
            chunk_dir = None

        # ── Seed chat with master summary (token-efficient) ───────────────
        st.session_state.chat_history_raw = [
            {"role": "user",  "text": f"סיכום מאסטר של ההרצאה:\n{master}"},
            {"role": "model", "text": "קראתי את סיכום ההרצאה המלא. אני מוכן ללמד אותך נושא אחד בכל פעם!"},
        ]
        st.session_state.chat_display    = []
        st.session_state.transcription   = full_transcript.strip()
        st.session_state.master_summary  = master
        st.session_state.chunk_summaries = chunk_summaries
        st.session_state.ready           = True

        progress_bar.progress(1.0, text="✅ Done!")
        status.success(f"✅ Processed {n} segment(s) successfully! Scroll down to explore the results.")
        if auto_delete and use_local:
            st.toast("🗑️ Local video deleted")
        time.sleep(1.5)
        progress_bar.empty()
        status.empty()
        st.rerun()

    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try: os.unlink(tmp_path)
            except Exception: pass
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
        progress_bar.empty()
        status.empty()
        st.error(f"❌ Error: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# RESULTS
# ════════════════════════════════════════════════════════════════════════════════
if st.session_state.chunk_summaries:
    st.markdown("---")

    has_video  = bool(
        st.session_state.video_path and os.path.exists(st.session_state.video_path)
    )
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
                st.markdown(f'<div class="card">{rendered}</div>', unsafe_allow_html=True)

        with tab_segs:
            for entry in st.session_state.chunk_summaries:
                total = len(st.session_state.chunk_summaries)
                st.markdown(
                    f'<div class="seg-label">📦 Segment {entry["idx"] + 1} / {total}</div>',
                    unsafe_allow_html=True,
                )
                rendered = render_summary_html(entry["summary"])
                st.markdown(f'<div class="card">{rendered}</div>', unsafe_allow_html=True)

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
        n_cols = 3 if FPDF_AVAILABLE else 2
        dl_cols = st.columns(n_cols)

        with dl_cols[0]:
            st.download_button("⬇️ Summary (.txt)", full_export, "summary.txt", "text/plain")
        with dl_cols[1]:
            st.download_button(
                "⬇️ Transcription (.txt)",
                st.session_state.transcription or "",
                "transcription.txt",
                "text/plain",
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
            st.markdown(f'<div class="chat-ai">🤖 {msg["content"]}</div>', unsafe_allow_html=True)

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
