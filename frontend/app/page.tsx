"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import CourseCard from "@/components/CourseCard";
import { AppProvider, useAppState } from "@/lib/AppContext";
import type { CourseKey } from "@/lib/AppContext";
import {
  startUploadJob, startTranscriptUploadJob, startJobPolling,
  fetchResumableJobs, startResumeJob,
  fetchSummaryList, fetchSavedSummary, deleteSummary, renameSummary,
} from "@/lib/api";
import type { Job, ResumableJob, SavedSummaryMeta } from "@/lib/api";

// ── Course metadata ────────────────────────────────────────────────────────────

const COURSES: {
  id: CourseKey;
  emoji: string;
  hebrewName: string;
  englishName: string;
  color: string;
}[] = [
  { id: "combinatorics",    emoji: "🔢", hebrewName: "קומבינטוריקה",    englishName: "Combinatorics",    color: "#3b82f6" },
  { id: "data_structures",  emoji: "🗂️", hebrewName: "מבני נתונים",      englishName: "Data Structures",  color: "#8b5cf6" },
  { id: "calculus",         emoji: "📐", hebrewName: 'חדו"א',             englishName: "Calculus",         color: "#22c55e" },
  { id: "linear_algebra_2", emoji: "🔷", hebrewName: "אלגברה לינארית 2", englishName: "Linear Algebra 2", color: "#FF7A00" },
];

// ── Dashboard ──────────────────────────────────────────────────────────────────

function DashboardInner() {
  const router       = useRouter();
  const { state, dispatch, hydrated } = useAppState();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const txtInputRef  = useRef<HTMLInputElement>(null);
  const stopPollingRef = useRef<(() => void) | null>(null);

  const resumeFileInputRef = useRef<HTMLInputElement>(null);

  const [isDragging,      setIsDragging]      = useState(false);
  const [processing,      setProcessing]      = useState(false);
  const [progress,        setProgress]        = useState("");
  const [error,           setError]           = useState<string | null>(null);
  const [resumableJobs,   setResumableJobs]   = useState<ResumableJob[]>([]);
  const [resumeTarget,    setResumeTarget]    = useState<ResumableJob | null>(null);
  const [summaryList,     setSummaryList]     = useState<SavedSummaryMeta[]>([]);
  const [loadingSummary,  setLoadingSummary]  = useState<string | null>(null);
  const [renamingFile,    setRenamingFile]    = useState<string | null>(null);
  const [renameValue,     setRenameValue]     = useState("");

  // Stop any in-flight polling when the dashboard unmounts (user navigated away).
  useEffect(() => () => { stopPollingRef.current?.(); }, []);

  // After hydration: if a result with chunks already exists in sessionStorage
  // (e.g. user pressed Back from the summary page but the job was done),
  // redirect back to the summary page automatically.
  useEffect(() => {
    if (hydrated && state.result && state.result.chunks.length > 0) {
      router.replace("/summary");
    }
  }, [hydrated, state.result, router]);

  // Fetch resumable (interrupted) jobs and summary list whenever course changes.
  useEffect(() => {
    fetchResumableJobs(state.selectedCourse).then(setResumableJobs);
    fetchSummaryList(state.selectedCourse).then(setSummaryList);
  }, [state.selectedCourse]);

  // Per-course summary counts (fetched from backend)
  const [allCourseCounts, setAllCourseCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    COURSES.forEach((c) => {
      fetchSummaryList(c.id).then((list) => {
        setAllCourseCounts((prev) => ({ ...prev, [c.id]: list.length }));
      });
    });
  }, []);

  // ── Upload + progressive poll ──────────────────────────────────────────────

  const handleFile = useCallback(
    async (file: File, testMode: boolean) => {
      setError(null);
      setProcessing(true);
      setProgress("Uploading file…");

      try {
        const job = await startUploadJob({
          file,
          model:        state.settings.model,
          lang:         state.settings.lang,
          chunkMinutes: state.settings.chunkMinutes,
          testMode,
          courseDir:    state.selectedCourse,
          apiKey:       state.settings.apiKey || undefined,
        });

        // Store the active job so the summary page can resume polling if
        // the user refreshes mid-processing.
        dispatch({
          type: "SET_ACTIVE_JOB",
          payload: {
            jobId:         job.job_id,
            status:        "queued",
            progress:      "Queued…",
            totalSegments: null,
            error:         null,
          },
        });

        let navigatedToSummary = false;

        const stop = startJobPolling(job.job_id, (updatedJob: Job) => {
          setProgress(updatedJob.progress);

          // Merge job data into context (chunks, status, etc.)
          dispatch({ type: "UPDATE_FROM_JOB", payload: updatedJob });

          if (updatedJob.status === "error") {
            stop();
            stopPollingRef.current = null;
            setError(updatedJob.error ?? "Processing failed");
            setProcessing(false);
            dispatch({ type: "CLEAR_ACTIVE_JOB" });
            return;
          }

          // Navigate to the summary page as soon as the first segment is ready.
          // The summary page will continue polling for the remaining segments.
          if (!navigatedToSummary && updatedJob.chunks && updatedJob.chunks.length > 0) {
            navigatedToSummary = true;
            stop();                        // dashboard stops; summary page takes over
            stopPollingRef.current = null;
            router.push("/summary");
          }
        });

        stopPollingRef.current = stop;
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
        setProcessing(false);
      }
    },
    [state.settings, state.selectedCourse, dispatch, router]
  );

  // ── Transcript (Fast Track) upload + poll ─────────────────────────────────

  const handleTranscriptFile = useCallback(
    async (file: File) => {
      setError(null);
      setProcessing(true);
      setProgress("Uploading transcript…");

      try {
        const job = await startTranscriptUploadJob({
          file,
          model:     state.settings.model,
          lang:      state.settings.lang,
          courseDir: state.selectedCourse,
          apiKey:    state.settings.apiKey || undefined,
        });

        dispatch({
          type: "SET_ACTIVE_JOB",
          payload: {
            jobId:         job.job_id,
            status:        "queued",
            progress:      "Queued…",
            totalSegments: null,
            error:         null,
          },
        });

        let navigated = false;

        const stop = startJobPolling(job.job_id, (updatedJob: Job) => {
          setProgress(updatedJob.progress);
          dispatch({ type: "UPDATE_FROM_JOB", payload: updatedJob });

          if (updatedJob.status === "error") {
            stop();
            stopPollingRef.current = null;
            setError(updatedJob.error ?? "Processing failed");
            setProcessing(false);
            dispatch({ type: "CLEAR_ACTIVE_JOB" });
            return;
          }

          if (!navigated && updatedJob.chunks && updatedJob.chunks.length > 0) {
            navigated = true;
            stop();
            stopPollingRef.current = null;
            router.push("/summary");
          }
        });

        stopPollingRef.current = stop;
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
        setProcessing(false);
      }
    },
    [state.settings, state.selectedCourse, dispatch, router]
  );

  // ── Resume interrupted job ────────────────────────────────────────────────

  const handleResume = useCallback(
    async (file: File, target: ResumableJob) => {
      setError(null);
      setProcessing(true);
      setProgress("Re-uploading file for resume…");

      try {
        const job = await startResumeJob({
          file,
          model:        target.model,
          lang:         target.lang,
          chunkMinutes: target.chunk_minutes,
          courseDir:    target.course_dir,
          apiKey:       state.settings.apiKey || undefined,
        });

        dispatch({
          type: "SET_ACTIVE_JOB",
          payload: {
            jobId:         job.job_id,
            status:        "queued",
            progress:      "Queued (resuming)…",
            totalSegments: null,
            error:         null,
          },
        });

        let navigated = false;

        const stop = startJobPolling(job.job_id, (updatedJob: Job) => {
          setProgress(updatedJob.progress);
          dispatch({ type: "UPDATE_FROM_JOB", payload: updatedJob });

          if (updatedJob.status === "error") {
            stop();
            stopPollingRef.current = null;
            setError(updatedJob.error ?? "Resume failed");
            setProcessing(false);
            dispatch({ type: "CLEAR_ACTIVE_JOB" });
            return;
          }

          if (!navigated && updatedJob.chunks && updatedJob.chunks.length > 0) {
            navigated = true;
            stop();
            stopPollingRef.current = null;
            router.push("/summary");
          }
        });

        stopPollingRef.current = stop;
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
        setProcessing(false);
      }
    },
    [state.settings.apiKey, dispatch, router]
  );

  // ── Open a saved summary ─────────────────────────────────────────────────
  const handleOpenSummary = useCallback(
    async (meta: SavedSummaryMeta) => {
      setLoadingSummary(meta.filename);
      try {
        const data = await fetchSavedSummary(state.selectedCourse, meta.filename);
        const transcript = data.transcript ?? "";
        dispatch({
          type: "SET_RESULT",
          payload: {
            summary:         data.summary,
            transcript,
            course:          state.selectedCourse,
            chunks:          [{ idx: 0, transcript, summary: data.summary }],
            chatSeed:        [
              { role: "user",  text: `סיכום מאסטר של ההרצאה:\n${data.summary}` },
              { role: "model", text: "קראתי את סיכום ההרצאה. אני מוכן ללמד אותך נושא אחד בכל פעם!" },
            ],
            savedSummary:    null,
            savedTranscript: null,
            totalSegments:   1,
          },
        });
        dispatch({ type: "CLEAR_ACTIVE_JOB" });
        router.push("/summary");
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoadingSummary(null);
      }
    },
    [state.selectedCourse, dispatch, router]
  );

  const handleDeleteSummary = useCallback(
    async (filename: string) => {
      try {
        await deleteSummary(state.selectedCourse, filename);
        setSummaryList((prev) => prev.filter((s) => s.filename !== filename));
        setAllCourseCounts((prev) => ({
          ...prev,
          [state.selectedCourse]: Math.max(0, (prev[state.selectedCourse] ?? 1) - 1),
        }));
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [state.selectedCourse]
  );

  const handleRenameSummary = useCallback(
    async (filename: string, newName: string) => {
      if (!newName.trim()) return;
      try {
        await renameSummary(state.selectedCourse, filename, newName.trim());
        setSummaryList((prev) =>
          prev.map((s) =>
            s.filename === filename ? { ...s, display_name: newName.trim() } : s
          )
        );
        setRenamingFile(null);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [state.selectedCourse]
  );

  function openFilePicker(testMode: boolean) {
    if (fileInputRef.current) {
      fileInputRef.current.dataset.testMode = testMode ? "1" : "0";
      fileInputRef.current.click();
    }
  }

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file     = e.target.files?.[0];
    const testMode = e.target.dataset.testMode === "1";
    if (file) handleFile(file, testMode);
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file, false);
  }

  const currentCourse = COURSES.find((c) => c.id === state.selectedCourse)!;

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />

      <main className="flex-1 flex flex-col overflow-y-auto bg-white">

        {/* ── Header ──────────────────────────────────────────────────────── */}
        <header className="bg-bgu-blue px-10 py-8 flex flex-col items-center text-center shrink-0">
          <h1
            className="font-display font-extrabold text-5xl leading-tight"
            style={{ color: "#FF7A00" }}
          >
            BGU Lecture Summarizer
          </h1>
          <p className="text-slate-300 text-sm mt-2">
            Upload a lecture → AI transcribes &amp; summarizes → Chat with your tutor
          </p>
          <span className="mt-4 bg-bgu-orange/20 border border-bgu-orange/40 text-bgu-orange
                           text-xs font-display font-bold rounded-lg px-3 py-1 tracking-widest uppercase">
            Powered by Gemini
          </span>
        </header>

        <div className="flex-1 px-10 py-8 flex flex-col gap-8">

          {/* ── Upload Area ──────────────────────────────────────────────── */}
          <section>
            <h2 className="font-display font-bold text-lg text-black mb-4">
              Upload Lecture
            </h2>

            {/* Drag-drop zone */}
            <div
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={onDrop}
              className={`border-2 border-dashed rounded-xl2 p-8 mb-5 text-center
                transition-all duration-200
                ${isDragging
                  ? "border-bgu-orange bg-orange-50"
                  : "border-slate-300 bg-slate-50 hover:border-bgu-orange hover:bg-orange-50/50"
                }`}
            >
              <div className="text-4xl mb-3">🎬</div>
              <p className="text-black font-semibold mb-1">
                Drag &amp; drop your lecture file here
              </p>
              <p className="text-slate-500 text-sm">
                Supports MP4, MP3, WAV, M4A
              </p>
            </div>

            {/* CTA buttons */}
            <div className="flex gap-4 flex-wrap">
              <button
                onClick={() => openFilePicker(false)}
                disabled={processing}
                className="btn-orange text-base px-8 py-4 flex-1 min-w-[220px]"
              >
                <span className="text-xl">📂</span>
                Upload Full Video
              </button>

              <button
                onClick={() => openFilePicker(true)}
                disabled={processing}
                className="btn-outline text-base px-8 py-4 flex-1 min-w-[220px]"
                title="Trims the file to the first 10 minutes using ffmpeg, then runs the full AI pipeline."
              >
                <span className="text-xl">⚡</span>
                Run Test Mode
                <span className="ml-1 text-xs font-normal opacity-80">(first 10 min)</span>
              </button>
            </div>

            <p className="text-xs text-slate-400 mt-2">
              <strong>Test Mode:</strong> Trims to the first 10 minutes with ffmpeg and runs
              the complete AI pipeline — use it to verify your API key and output quality
              before committing to a full-length lecture.
            </p>

            {/* Fast Track divider */}
            <div className="flex items-center gap-3 mt-5">
              <div className="flex-1 h-px bg-slate-200" />
              <span className="text-xs text-slate-400 font-semibold tracking-widest uppercase">
                Fast Track
              </span>
              <div className="flex-1 h-px bg-slate-200" />
            </div>

            <button
              onClick={() => txtInputRef.current?.click()}
              disabled={processing}
              className="mt-3 w-full inline-flex items-center justify-center gap-2
                         border-2 border-emerald-500 text-emerald-700
                         hover:bg-emerald-50 active:bg-emerald-100
                         font-display font-bold rounded-xl px-6 py-3
                         transition-all duration-200
                         disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <span className="text-xl">📄</span>
              Upload Transcript (.txt)
              <span className="ml-1 text-xs font-normal opacity-70">
                — skips transcription, summarises directly
              </span>
            </button>
            <p className="text-xs text-slate-400 mt-1.5">
              <strong>Fast Track:</strong> Upload a plain-text transcript to skip the
              video upload + transcription steps entirely. Ideal for development, testing,
              and re-summarising existing transcripts.
            </p>
          </section>

          {/* ── Resume Banner ────────────────────────────────────────────── */}
          {resumableJobs.length > 0 && !processing && (
            <section>
              <h2 className="font-display font-bold text-lg text-black mb-3">
                Interrupted Jobs
              </h2>
              <div className="flex flex-col gap-3">
                {resumableJobs.map((job) => (
                  <div
                    key={job.wip_name}
                    className="flex items-center gap-4 rounded-xl border-2 border-amber-400/60
                               bg-amber-50 px-5 py-4"
                  >
                    <span className="text-2xl shrink-0">⚡</span>
                    <div className="flex-1 min-w-0">
                      <p className="font-display font-bold text-sm text-black truncate">
                        {job.file_name}
                      </p>
                      <p className="text-xs text-slate-600 mt-0.5">
                        {job.done_segments} / {job.total_segments} segments completed
                        {job.created_at ? ` · started ${new Date(job.created_at).toLocaleDateString()}` : ""}
                      </p>
                    </div>
                    <button
                      onClick={() => {
                        setResumeTarget(job);
                        resumeFileInputRef.current?.click();
                      }}
                      className="shrink-0 bg-amber-500 hover:bg-amber-600 active:bg-amber-700
                                 text-white font-display font-bold text-sm rounded-lg px-4 py-2
                                 transition-colors"
                    >
                      Resume →
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* ── Processing Banner ────────────────────────────────────────── */}
          {processing && (
            <div className="card border-bgu-orange/30 bg-orange-50 flex items-center gap-4">
              <div className="w-8 h-8 border-4 border-bgu-orange border-t-transparent
                              rounded-full animate-spin shrink-0" />
              <div>
                <p className="font-semibold text-black">Processing…</p>
                <p className="text-sm text-slate-600">{progress}</p>
                <p className="text-xs text-slate-400 mt-1">
                  You will be redirected to the summary page as soon as the first
                  segment is ready — no need to wait for the full video.
                </p>
              </div>
            </div>
          )}

          {/* ── Error ──────────────────────────────────────────────────────── */}
          {error && (
            <div className="card border-red-300 bg-red-50">
              <p className="font-semibold text-red-700">Error</p>
              <p className="text-sm text-red-600 mt-1">{error}</p>
              <button
                className="mt-3 text-xs font-semibold text-red-500 underline"
                onClick={() => setError(null)}
              >
                Dismiss
              </button>
            </div>
          )}

          {/* ── Course Selection ─────────────────────────────────────────── */}
          <section>
            <div className="flex items-center gap-3 mb-4">
              <h2 className="font-display font-bold text-lg text-black">
                Select Course
              </h2>
              <span
                className="text-xs font-bold px-2 py-0.5 rounded-lg text-white"
                style={{ backgroundColor: currentCourse.color }}
              >
                {currentCourse.emoji} {currentCourse.englishName}
              </span>
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              {COURSES.map((course) => (
                <CourseCard
                  key={course.id}
                  {...course}
                  recordings={0}
                  summaries={allCourseCounts[course.id] ?? 0}
                  isSelected={state.selectedCourse === course.id}
                  onSelect={(id) => dispatch({ type: "SET_COURSE", payload: id })}
                />
              ))}
            </div>

            {/* ── Knowledge Base banner ── */}
            <Link
              href={`/knowledge-base/${state.selectedCourse}`}
              className="mt-4 flex items-center gap-4 rounded-xl border-2 px-6 py-4
                         transition-all duration-200 hover:shadow-md group"
              style={{
                borderColor: `${currentCourse.color}60`,
                backgroundColor: `${currentCourse.color}0d`,
              }}
            >
              <span className="text-3xl">📚</span>
              <div className="flex-1">
                <p className="font-display font-bold text-base text-black group-hover:text-bgu-orange transition-colors">
                  ספר הקורס — {currentCourse.hebrewName}
                </p>
                <p className="text-xs text-slate-500 mt-0.5">
                  כל ההגדרות, המשפטים, ההוכחות והתרגילים · מתעדכן אוטומטית עם כל הרצאה
                </p>
              </div>
              <span
                className="text-sm font-bold px-3 py-1.5 rounded-lg text-white shrink-0"
                style={{ backgroundColor: currentCourse.color }}
              >
                פתח ←
              </span>
            </Link>
          </section>

          {/* ── Summary History ────────────────────────────────────────── */}
          {summaryList.length > 0 && (
            <section>
              <h2 className="font-display font-bold text-lg text-black mb-3">
                Past Summaries — {currentCourse.englishName}
                <span className="ml-2 text-sm font-normal text-slate-400">
                  ({summaryList.length})
                </span>
              </h2>
              <div className="flex flex-col gap-2">
                {summaryList.map((s) => (
                  <div
                    key={s.filename}
                    className="flex items-center gap-3 rounded-xl border border-slate-200
                               bg-white px-5 py-3 hover:border-slate-300 transition-colors group"
                  >
                    <span className="text-xl shrink-0">📄</span>
                    <div className="flex-1 min-w-0">
                      {renamingFile === s.filename ? (
                        <form
                          className="flex gap-2"
                          onSubmit={(e) => {
                            e.preventDefault();
                            handleRenameSummary(s.filename, renameValue);
                          }}
                        >
                          <input
                            autoFocus
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            className="flex-1 border border-slate-300 rounded-lg px-2 py-1 text-sm
                                       focus:outline-none focus:border-bgu-orange"
                          />
                          <button type="submit"
                            className="text-xs text-white bg-bgu-orange rounded-lg px-3 py-1 font-bold">
                            Save
                          </button>
                          <button type="button" onClick={() => setRenamingFile(null)}
                            className="text-xs text-slate-500 hover:text-black">
                            Cancel
                          </button>
                        </form>
                      ) : (
                        <>
                          <p className="font-display font-bold text-sm text-black truncate">
                            {s.display_name}
                          </p>
                          <p className="text-xs text-slate-500 mt-0.5">
                            {s.date && s.time ? `${s.date} ${s.time}` : s.filename}
                            {s.has_transcript && " · has transcript"}
                          </p>
                        </>
                      )}
                    </div>
                    {renamingFile !== s.filename && (
                      <div className="flex gap-1.5 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={() => handleOpenSummary(s)}
                          disabled={loadingSummary === s.filename}
                          className="text-xs bg-bgu-orange hover:bg-bgu-orange-dark text-white
                                     font-bold rounded-lg px-3 py-1.5 transition-colors
                                     disabled:opacity-50"
                        >
                          {loadingSummary === s.filename ? "Loading…" : "Open"}
                        </button>
                        <button
                          onClick={() => { setRenamingFile(s.filename); setRenameValue(s.display_name); }}
                          className="text-xs border border-slate-300 text-slate-600 hover:text-black
                                     hover:border-slate-400 rounded-lg px-2.5 py-1.5 transition-colors"
                          title="Rename"
                        >
                          ✏️
                        </button>
                        <button
                          onClick={() => {
                            if (confirm(`Delete "${s.display_name}"?`))
                              handleDeleteSummary(s.filename);
                          }}
                          className="text-xs border border-red-200 text-red-500 hover:bg-red-50
                                     hover:border-red-300 rounded-lg px-2.5 py-1.5 transition-colors"
                          title="Delete"
                        >
                          🗑️
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

        </div>
      </main>

      <input
        ref={fileInputRef}
        type="file"
        accept=".mp4,.mp3,.wav,.m4a"
        className="hidden"
        onChange={onFileChange}
      />
      <input
        ref={txtInputRef}
        type="file"
        accept=".txt"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleTranscriptFile(file);
          e.target.value = "";
        }}
      />
      <input
        ref={resumeFileInputRef}
        type="file"
        accept=".mp4,.mp3,.wav,.m4a"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && resumeTarget) handleResume(file, resumeTarget);
          setResumeTarget(null);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export default function DashboardPage() {
  return (
    <AppProvider>
      <DashboardInner />
    </AppProvider>
  );
}
