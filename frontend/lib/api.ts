/**
 * API client — routing strategy:
 *
 *  UPLOAD  →  direct browser → FastAPI (http://localhost:8000)
 *             Reason: Next.js hard-caps proxied request bodies at 10 MB.
 *             Video files are hundreds of MB, so the proxy must be bypassed
 *             entirely. The browser sends FormData straight to FastAPI;
 *             FastAPI's CORSMiddleware handles the preflight.
 *
 *  POLL / CHAT  →  through Next.js proxy (/api/*)
 *             These are small JSON payloads; the proxy is fine and keeps
 *             the backend URL out of client code for those calls.
 *
 *  NEXT_PUBLIC_BACKEND_URL  (set in frontend/.env.local)
 *             Defaults to http://localhost:8000.
 *             Override for staging/production without touching source code.
 */

// Proxy base — used for all non-upload calls (poll, chat)
const PROXY_BASE = "/api";

// Direct backend URL — used for upload only (bypasses Next.js body limit)
const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL?.replace(/\/$/, "") ??
  "http://localhost:8000";

// ── Types ──────────────────────────────────────────────────────────────────────

export type JobStatus = "queued" | "processing" | "done" | "error";

export interface QuizQuestion {
  num:        number;
  text:       string;
  choices:    string[];
  answer_idx: number;
}

export interface QuizBlock {
  position:  "mid" | "end";
  questions: QuizQuestion[];
}

export interface QuizData {
  blocks: QuizBlock[];
}

export interface Job {
  job_id:           string;
  status:           JobStatus;
  progress:         string;
  transcript:       string | null;
  summary:          string | null;
  chunks:           { idx: number; transcript: string; summary: string }[] | null;
  chat_seed:        { role: "user" | "model"; text: string }[] | null;
  saved_summary:    string | null;
  saved_transcript: string | null;
  total_segments:   number | null;
  quiz:             QuizData | null;
  error:            string | null;
}

export interface UploadParams {
  file: File;
  model: string;
  lang: string;
  chunkMinutes: number;
  testMode: boolean;
  courseDir: string;
}

export interface ChatParams {
  userMessage: string;
  summaryContext: string;
  chatHistory: { role: "user" | "model"; text: string }[];
  model: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Small JSON calls — routed through Next.js proxy */
function proxyUrl(path: string) {
  return `${PROXY_BASE}${path}`;
}

/** Large binary calls — routed directly to FastAPI, bypassing Next.js */
function directUrl(path: string) {
  return `${BACKEND_URL}${path}`;
}

// ── Upload ─────────────────────────────────────────────────────────────────────

/**
 * Send the video/audio file DIRECTLY to FastAPI, bypassing the Next.js proxy.
 *
 * Why direct?  Next.js limits proxied request bodies to 10 MB (hardcoded in
 * the framework).  Video files are 100s of MB — the proxy drops the connection
 * with ECONNRESET before FastAPI ever sees the request.  Sending directly from
 * the browser avoids this limit entirely.  FastAPI's CORSMiddleware allows
 * requests from http://localhost:3000, so there are no CORS issues.
 *
 * Returns the job_id immediately; use pollJob() to track completion.
 */
export async function startUploadJob(params: UploadParams): Promise<Job> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("model", params.model);
  form.append("lang", params.lang);
  form.append("chunk_minutes", String(params.chunkMinutes));
  form.append("test_mode", String(params.testMode));
  form.append("course_dir", params.courseDir);

  // directUrl — NOT proxyUrl — so the request never touches Next.js
  const res = await fetch(directUrl("/upload"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Upload failed");
  }
  return res.json();
}

// ── Transcript upload (Fast Track) ────────────────────────────────────────────

export interface TranscriptUploadParams {
  file:      File;
  model:     string;
  lang:      string;
  courseDir: string;
}

/**
 * Send a plain-text transcript directly to FastAPI's /upload-transcript
 * endpoint.  Bypasses transcription entirely — the backend segments the
 * text and summarises each chunk directly.
 */
export async function startTranscriptUploadJob(params: TranscriptUploadParams): Promise<Job> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("model", params.model);
  form.append("lang", params.lang);
  form.append("course_dir", params.courseDir);

  const res = await fetch(directUrl("/upload-transcript"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Transcript upload failed");
  }
  return res.json();
}

// ── Streaming poll (progressive loading) ──────────────────────────────────────

/**
 * Start polling /jobs/{job_id} and call onUpdate on EVERY response —
 * including while status is still "processing".  This enables the UI to
 * show each segment as soon as the backend publishes it.
 *
 * Uses setTimeout (not setInterval) so ticks never overlap: the next poll
 * only fires after the current HTTP response has been received and processed.
 *
 * Returns a cleanup function.  Call it to stop polling (e.g. on unmount).
 * The caller is responsible for detecting status === "done" or "error" inside
 * the onUpdate callback and calling stop() when appropriate.
 *
 * Usage:
 *   const stop = startJobPolling(jobId, (job) => { ... });
 *   useEffect(() => stop, []);   // stop on unmount
 */
export function startJobPolling(
  jobId: string,
  onUpdate: (job: Job) => void,
  intervalMs = 2500
): () => void {
  let running = true;

  async function tick() {
    if (!running) return;
    try {
      const res = await fetch(proxyUrl(`/jobs/${jobId}`));
      if (res.ok) {
        const job: Job = await res.json();
        if (running) onUpdate(job);
        // Stop self-scheduling once terminal state is reached
        if (job.status === "done" || job.status === "error") {
          running = false;
          return;
        }
      }
    } catch {
      // Transient network error — will retry on next tick
    }
    if (running) setTimeout(tick, intervalMs);
  }

  tick(); // Immediate first check
  return () => { running = false; };
}

// ── Resumable jobs ────────────────────────────────────────────────────────────

export interface ResumableJob {
  file_name:      string;
  wip_name:       string;
  done_segments:  number;
  total_segments: number;
  model:          string;
  lang:           string;
  course_dir:     string;
  chunk_minutes:  number;
  created_at:     string;
}

export async function fetchResumableJobs(courseId: string): Promise<ResumableJob[]> {
  try {
    const res = await fetch(proxyUrl(`/resumable-jobs/${courseId}`));
    if (!res.ok) return [];
    const data = await res.json();
    return (data.jobs ?? []) as ResumableJob[];
  } catch {
    return [];
  }
}

export async function terminateResumableJob(courseId: string, wipName: string): Promise<void> {
  const res = await fetch(proxyUrl(`/resumable-jobs/${courseId}/${wipName}`), {
    method: "DELETE",
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Terminate failed");
  }
}

/**
 * Re-upload the original video file to POST /resume.
 * The backend re-splits it with ffmpeg but loads already-checkpointed
 * segments from disk — only missing segments consume API tokens.
 */
export async function startResumeJob(params: Omit<UploadParams, "testMode">): Promise<Job> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("model", params.model);
  form.append("lang", params.lang);
  form.append("chunk_minutes", String(params.chunkMinutes));
  form.append("course_dir", params.courseDir);

  const res = await fetch(directUrl("/resume"), { method: "POST", body: form });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Resume failed");
  }
  return res.json();
}

// ── Knowledge Base ────────────────────────────────────────────────────────────

export interface KnowledgeBaseResponse {
  course_id:    string;
  course_label: string;
  content:      string;
}

export async function fetchKnowledgeBase(courseId: string): Promise<KnowledgeBaseResponse> {
  const res = await fetch(proxyUrl(`/knowledge-base/${courseId}`));
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Failed to load knowledge base");
  }
  return res.json();
}

// ── Summary History ──────────────────────────────────────────────────────────

export interface SavedSummaryMeta {
  filename:       string;
  date:           string;
  time:           string;
  display_name:   string;
  has_transcript: boolean;
  size:           number;
}

export async function fetchSummaryList(courseId: string): Promise<SavedSummaryMeta[]> {
  try {
    const res = await fetch(proxyUrl(`/summaries/${courseId}`));
    if (!res.ok) return [];
    const data = await res.json();
    return (data.summaries ?? []) as SavedSummaryMeta[];
  } catch {
    return [];
  }
}

export interface SavedSummaryContent {
  filename:     string;
  display_name: string;
  summary:      string;
  transcript:   string | null;
}

export async function fetchSavedSummary(courseId: string, filename: string): Promise<SavedSummaryContent> {
  const res = await fetch(proxyUrl(`/summaries/${courseId}/${filename}`));
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Failed to load summary");
  }
  return res.json();
}

export async function deleteSummary(courseId: string, filename: string): Promise<void> {
  const res = await fetch(proxyUrl(`/summaries/${courseId}/${filename}`), { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Delete failed");
  }
}

export async function renameSummary(courseId: string, filename: string, displayName: string): Promise<void> {
  const res = await fetch(proxyUrl(`/summaries/${courseId}/${filename}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name: displayName }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Rename failed");
  }
}

// ── Chat ───────────────────────────────────────────────────────────────────────

/**
 * Send a chat message to the stateless backend.
 * The ENTIRE summary is bundled with each request — this is how we
 * provide context without server-side session state.
 */
export async function sendChatMessage(params: ChatParams): Promise<string> {
  const res = await fetch(proxyUrl("/chat"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_message: params.userMessage,
      summary_context: params.summaryContext,
      chat_history: params.chatHistory,
      model: params.model,
    }),
  });

  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(detail.detail ?? "Chat request failed");
  }

  const data = await res.json();
  return data.reply as string;
}
