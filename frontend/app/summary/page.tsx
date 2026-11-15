"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import { AppProvider, useAppState } from "@/lib/AppContext";
import { sendChatMessage, startJobPolling } from "@/lib/api";
import type { Job, QuizBlock as QuizBlockData } from "@/lib/api";
import SummaryRenderer, { QuizBlock as QuizBlockUI } from "@/components/SummaryRenderer";

// ── Types ──────────────────────────────────────────────────────────────────────

interface ChatMessage {
  role: "user" | "model";
  text: string;
}

// ── Skeleton ───────────────────────────────────────────────────────────────────
// Rendered on the server AND during first client paint (before sessionStorage
// is read). Must be completely static — no props, no conditional classes.

function PageSkeleton() {
  return (
    <div className="flex h-screen overflow-hidden bg-slate-100">
      <div className="w-[65%] h-full flex flex-col bg-white border-r border-slate-200">
        <div className="bg-bgu-blue px-8 py-4 shrink-0 h-[56px]" />
        <div className="flex-1 px-10 py-8 flex flex-col gap-4">
          <div className="h-4 bg-slate-200 rounded w-3/4 animate-pulse" />
          <div className="h-4 bg-slate-200 rounded w-full animate-pulse" />
          <div className="h-4 bg-slate-200 rounded w-5/6 animate-pulse" />
        </div>
      </div>
      <div className="w-[35%] h-full flex flex-col bg-white">
        <div className="bg-bgu-blue px-6 py-4 shrink-0 h-[56px]" />
        <div className="flex-1" />
      </div>
    </div>
  );
}

// ── Transcript Renderer ────────────────────────────────────────────────────────

/**
 * Renders raw transcript text with:
 *   [MM:SS]     → purple timestamp badge
 *   [BOARD: …]  → orange highlighted span
 *   rest        → plain text (preserving newlines)
 */
function TranscriptRenderer({ text }: { text: string }) {
  // Split on [MM:SS] timestamps and [BOARD: ...] tags, keeping the delimiters
  const TOKEN_RE = /(\[\d{1,2}:\d{2}\]|\[BOARD:[^\]]*\])/g;
  const parts = text.split(TOKEN_RE);

  const nodes = parts.map((part, i) => {
    if (/^\[\d{1,2}:\d{2}\]$/.test(part)) {
      return (
        <span key={i}
          className="inline-block mx-1 px-1.5 py-0.5 text-[0.65rem] font-mono font-semibold
                     rounded bg-purple-900/60 text-purple-300 border border-purple-700/50
                     align-middle leading-none">
          {part}
        </span>
      );
    }
    if (/^\[BOARD:/i.test(part)) {
      const inner = part.replace(/^\[BOARD:\s*/i, "").replace(/\]$/, "");
      return (
        <span key={i}
          className="inline-block mx-1 px-2 py-0.5 text-[0.75rem] font-mono
                     rounded bg-amber-900/40 text-amber-300 border border-amber-700/40
                     align-middle">
          📋 {inner}
        </span>
      );
    }
    // Plain text — preserve newlines
    return (
      <span key={i}>
        {part.split("\n").map((line, j, arr) => (
          <span key={j}>{line}{j < arr.length - 1 && <br />}</span>
        ))}
      </span>
    );
  });

  return <>{nodes}</>;
}

// SummaryRenderer is imported from @/components/SummaryRenderer

// ── Chat Bubble ────────────────────────────────────────────────────────────────
// memo: prevents re-render when userInput changes (only re-renders when msg changes)

const ChatBubble = memo(function ChatBubble({ msg }: { msg: ChatMessage }) {
  return (
    <div className={msg.role === "user" ? "flex justify-end" : "flex justify-start"}>
      <div className={msg.role === "user" ? "bubble-user" : "bubble-ai"}>
        <ReactMarkdown remarkPlugins={[remarkMath, remarkGfm]} rehypePlugins={[rehypeKatex]}>
          {msg.text}
        </ReactMarkdown>
      </div>
    </div>
  );
});

// ── Segment Navigator ──────────────────────────────────────────────────────────

interface SegmentNavigatorProps {
  currentIdx:     number;
  totalLoaded:    number;   // chunks available right now
  totalExpected:  number | null; // total when job is done (null while splitting)
  isProcessing:   boolean;
  progress:       string;
  onPrev:         () => void;
  onNext:         () => void;
  onJump:         (idx: number) => void;
  activeCardIdx?: number;
  totalCards?:    number;
}

const SegmentNavigator = memo(function SegmentNavigator({
  currentIdx, totalLoaded, totalExpected, isProcessing, progress, onPrev, onNext, onJump,
  activeCardIdx, totalCards,
}: SegmentNavigatorProps) {
  const countLabel = totalExpected !== null
    ? `${totalLoaded} / ${totalExpected}`
    : `${totalLoaded}`;

  return (
    <div className="shrink-0 bg-slate-50 border-b border-slate-200 px-6 py-3
                    flex flex-col gap-2">

      {/* Navigation row */}
      <div className="flex items-center gap-3">
        {/* Prev */}
        <button
          onClick={onPrev}
          disabled={currentIdx === 0}
          className="w-8 h-8 flex items-center justify-center rounded-lg
                     border border-slate-300 text-slate-600 text-sm font-bold
                     hover:border-bgu-orange hover:text-bgu-orange
                     disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          aria-label="Previous segment"
        >
          ‹
        </button>

        {/* Segment label */}
        <span className="font-display font-bold text-sm text-black">
          Segment{" "}
          <span className="text-bgu-orange">{currentIdx + 1}</span>
          {" "}of{" "}
          <span className="text-bgu-orange">{countLabel}</span>
          {isProcessing && totalExpected !== null && totalLoaded < totalExpected && (
            <span className="text-slate-400 font-normal"> (+{totalExpected - totalLoaded} loading…)</span>
          )}
        </span>

        {/* Next */}
        <button
          onClick={onNext}
          disabled={currentIdx >= totalLoaded - 1}
          className="w-8 h-8 flex items-center justify-center rounded-lg
                     border border-slate-300 text-slate-600 text-sm font-bold
                     hover:border-bgu-orange hover:text-bgu-orange
                     disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          aria-label="Next segment"
        >
          ›
        </button>

        {/* Processing pulse */}
        {isProcessing && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-bgu-orange font-medium">
            <span className="w-2 h-2 bg-bgu-orange rounded-full animate-ping" />
            Live
          </span>
        )}
      </div>

      {/* Segment dot strip — click any dot to jump */}
      {totalLoaded > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {Array.from({ length: totalExpected ?? totalLoaded }).map((_, i) => {
            const loaded = i < totalLoaded;
            const active = i === currentIdx;
            return (
              <button
                key={i}
                onClick={() => loaded && onJump(i)}
                disabled={!loaded}
                title={loaded ? `Jump to segment ${i + 1}` : `Segment ${i + 1} — processing…`}
                className={`w-3 h-3 rounded-full transition-all duration-200
                  ${active
                    ? "bg-bgu-orange scale-125"
                    : loaded
                      ? "bg-slate-400 hover:bg-bgu-orange"
                      : "bg-slate-200 cursor-not-allowed"
                  }`}
              />
            );
          })}
        </div>
      )}

      {/* Live progress text */}
      {isProcessing && progress && (
        <p className="text-xs text-slate-500 truncate">{progress}</p>
      )}

      {/* Card counter */}
      {totalCards !== undefined && totalCards > 0 && (
        <div className="flex justify-end">
          <span className="font-mono text-xs text-slate-400 tabular-nums" dir="ltr">
            {activeCardIdx ?? 1}/{totalCards}
          </span>
        </div>
      )}
    </div>
  );
});

// ── Chat Input ─────────────────────────────────────────────────────────────────
// Owns its own input state so typing ONLY re-renders this small component,
// not the heavy SummaryPageInner with all its semantic cards.

interface ChatInputProps {
  disabled:    boolean;
  onSend:      (msg: string) => void;
  prefillText?: string;
  prefillKey?:  number;
}

const ChatInput = memo(function ChatInput({ disabled, onSend, prefillText, prefillKey }: ChatInputProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // When prefillKey changes, populate the textarea and focus it
  useEffect(() => {
    if (prefillKey === undefined || prefillKey === 0) return;
    setValue(prefillText ?? "");
    textareaRef.current?.focus();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillKey]);

  function handleSend() {
    const msg = value.trim();
    if (!msg || disabled) return;
    onSend(msg);
    setValue("");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  }

  return (
    <div className="px-5 py-4 border-t border-slate-200 bg-white shrink-0">
      <div className="flex gap-2 items-end">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="שאל את המורה... (Enter to send)"
          rows={2}
          dir="rtl"
          disabled={disabled}
          className="flex-1 resize-none border-2 border-slate-200 rounded-xl px-4 py-2.5
                     text-black text-sm placeholder-slate-400 leading-relaxed
                     focus:outline-none focus:border-bgu-orange transition-colors duration-200
                     disabled:opacity-60 disabled:cursor-not-allowed"
          style={{ fontFamily: "'Noto Sans Hebrew', Arial, sans-serif" }}
        />
        <button
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          className="btn-orange px-4 py-2.5 self-stretch rounded-xl"
          aria-label="Send message"
        >
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"
               className="w-5 h-5 rotate-90">
            <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
          </svg>
        </button>
      </div>
      <p className="text-[0.7rem] text-slate-400 mt-1.5 text-right">
        Shift+Enter for new line · יאללה נמשיך to advance topics
      </p>
    </div>
  );
});

// ── Main Page ──────────────────────────────────────────────────────────────────

function SummaryPageInner() {
  const router = useRouter();
  const { state, dispatch, hydrated } = useAppState();
  const result    = state.result;
  const activeJob = state.activeJob;

  // ── Segment navigation ─────────────────────────────────────────────────────
  const [currentSegIdx, setCurrentSegIdx] = useState(0);

  // Auto-advance to newest segment as they arrive (while user hasn't manually
  // navigated). Stop auto-advancing once the user clicks Prev/Next.
  const [userNavigated, setUserNavigated] = useState(false);
  useEffect(() => {
    if (!userNavigated && result && result.chunks.length > 0) {
      // Stay on segment 0 by default — user reads from the beginning
    }
  }, [result?.chunks.length, userNavigated]);

  // Keep currentSegIdx in bounds as new chunks arrive
  useEffect(() => {
    if (result && currentSegIdx >= result.chunks.length && result.chunks.length > 0) {
      setCurrentSegIdx(result.chunks.length - 1);
    }
  }, [result?.chunks.length, currentSegIdx]);

  // ── Live polling (resumes if still processing) ─────────────────────────────
  // If the user refreshes mid-processing, activeJob.jobId is still in
  // sessionStorage, so we can resume polling transparently.
  const stopPollingRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!hydrated) return;
    if (!activeJob?.jobId) return;
    if (activeJob.status === "done" || activeJob.status === "error") return;

    const stop = startJobPolling(activeJob.jobId, (job: Job) => {
      dispatch({ type: "UPDATE_FROM_JOB", payload: job });

      if (job.status === "done" || job.status === "error") {
        stop();
        stopPollingRef.current = null;
      }
    });

    stopPollingRef.current = stop;
    return () => { stop(); stopPollingRef.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrated, activeJob?.jobId]);

  // ── Redirect guard ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (hydrated && (!result || result.chunks.length === 0) && !activeJob) {
      router.replace("/");
    }
  }, [hydrated, result, activeJob, router]);

  // ── Transcript viewer ──────────────────────────────────────────────────────
  const [showTranscript, setShowTranscript] = useState(false);

  // ── Master summary toggle ──────────────────────────────────────────────────
  const [showMasterSummary, setShowMasterSummary] = useState(false);

  // ── Master summary card counter (IntersectionObserver) ─────────────────────
  const masterScrollRef  = useRef<HTMLDivElement>(null);
  const [activeCardIdx, setActiveCardIdx] = useState(1);
  const [totalCards,    setTotalCards]    = useState(0);

  // ── Segment-view card counter (IntersectionObserver) ──────────────────────
  const segScrollRef       = useRef<HTMLDivElement>(null);
  const [activeSegCardIdx, setActiveSegCardIdx] = useState(1);
  const [totalSegCards,    setTotalSegCards]    = useState(0);

  useEffect(() => {
    if (!showMasterSummary || !masterScrollRef.current) {
      setTotalCards(0);
      return;
    }
    const root  = masterScrollRef.current;
    const cards = Array.from(root.querySelectorAll<HTMLElement>("[data-card-idx]"));
    setTotalCards(cards.length);
    if (cards.length === 0) return;
    setActiveCardIdx(1);

    // Track the set of cards that currently have any pixel inside the scroll
    // container.  The topmost one (minimum index) is the "active" card.
    const visible = new Set<number>();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const idx = parseInt(entry.target.getAttribute("data-card-idx") ?? "0", 10);
          if (entry.isIntersecting) visible.add(idx);
          else visible.delete(idx);
        });
        if (visible.size > 0) setActiveCardIdx(Math.min(...visible));
      },
      { root, threshold: 0 },
    );
    cards.forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, [showMasterSummary]);

  useEffect(() => {
    if (showMasterSummary || showTranscript || !segScrollRef.current) {
      setTotalSegCards(0);
      return;
    }
    const root  = segScrollRef.current;
    const cards = Array.from(root.querySelectorAll<HTMLElement>("[data-card-idx]"));
    setTotalSegCards(cards.length);
    if (cards.length === 0) return;
    setActiveSegCardIdx(1);

    const visible = new Set<number>();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          const idx = parseInt(entry.target.getAttribute("data-card-idx") ?? "0", 10);
          if (entry.isIntersecting) visible.add(idx);
          else visible.delete(idx);
        });
        if (visible.size > 0) setActiveSegCardIdx(Math.min(...visible));
      },
      { root, threshold: 0 },
    );
    cards.forEach((card) => observer.observe(card));
    return () => observer.disconnect();
  }, [showMasterSummary, showTranscript, currentSegIdx, result?.chunks.length]);

  // ── Chat state ─────────────────────────────────────────────────────────────
  const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError,   setChatError]   = useState<string | null>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  // ── Chat prefill (from quiz/card chat icons) ───────────────────────────────
  const [prefillText, setPrefillText] = useState("");
  const [prefillKey,  setPrefillKey]  = useState(0);

  const handleChatRequest = useCallback((text: string) => {
    setPrefillText(text);
    setPrefillKey((k) => k + 1);
  }, []);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatHistory]);

  // ── Segment navigation handlers ────────────────────────────────────────────
  // Declared here (before early returns) so the hook call order is always
  // the same regardless of which early-return branch fires.
  const handlePrev = useCallback(() => {
    setUserNavigated(true);
    setCurrentSegIdx((i) => Math.max(0, i - 1));
  }, []);

  const handleNext = useCallback(() => {
    setUserNavigated(true);
    setCurrentSegIdx((i) => Math.min((result?.chunks.length ?? 1) - 1, i + 1));
  }, [result?.chunks.length]);

  const handleJump = useCallback((idx: number) => {
    setUserNavigated(true);
    setCurrentSegIdx(idx);
  }, []);

  // ── Quiz blocks for the current segment ───────────────────────────────────
  // Show the mid-quiz on roughly the middle segment, and the end-quiz on the
  // last segment. For a single-segment lecture both blocks appear together.
  const segmentQuizBlocks = useMemo((): QuizBlockData[] => {
    if (!result?.quiz) return [];
    if (activeJob?.status === "processing" || activeJob?.status === "queued") return [];
    const total = result.chunks.length;
    if (total === 0) return [];
    const midAt  = Math.floor((total - 1) / 2);
    const blocks: QuizBlockData[] = [];
    if (currentSegIdx === midAt) {
      const b = result.quiz.blocks.find((b) => b.position === "mid");
      if (b) blocks.push(b);
    }
    if (currentSegIdx === total - 1) {
      const b = result.quiz.blocks.find((b) => b.position === "end");
      if (b) blocks.push(b);
    }
    return blocks;
  }, [result?.quiz, result?.chunks.length, currentSegIdx, activeJob?.status]);

  // ── Pre-hydration render ───────────────────────────────────────────────────
  if (!hydrated) return <PageSkeleton />;
  if (!result || result.chunks.length === 0) return <PageSkeleton />;

  // ── Derived values ─────────────────────────────────────────────────────────
  const isProcessing  = activeJob?.status === "processing" || activeJob?.status === "queued";
  const currentChunk  = result.chunks[currentSegIdx];
  const masterReady   = !isProcessing && !!result.summary;

  // Full transcript: prefer the concatenated field, fall back to joining chunks
  const fullTranscript = result.transcript && result.transcript.trim()
    ? result.transcript
    : result.chunks
        .map((c, i) => `--- חלק ${i + 1} ---\n\n${c.transcript}`)
        .join("\n\n");

  /**
   * Chat context strategy:
   *   - If the master summary is ready (job done), send it — it is the most
   *     concise representation and covers all segments.
   *   - While processing, concatenate all loaded segment summaries so the
   *     tutor can answer questions about any segment seen so far.
   */
  const chatSummaryContext = masterReady
    ? result.summary
    : result.chunks
        .map((c) => `Segment ${c.idx + 1}:\n${c.summary}`)
        .join("\n\n---\n\n");

  // ── Handlers ──────────────────────────────────────────────────────────────

  async function handleSend(msg: string) {
    if (!msg || chatLoading) return;

    const newHistory: ChatMessage[] = [...chatHistory, { role: "user", text: msg }];
    setChatHistory(newHistory);
    setChatLoading(true);
    setChatError(null);

    try {
      const reply = await sendChatMessage({
        userMessage:    msg,
        summaryContext: chatSummaryContext,
        chatHistory:    chatHistory.map((h) => ({ role: h.role, text: h.text })),
        model:          state.settings.model,
      });
      setChatHistory((prev) => [...prev, { role: "model", text: reply }]);
    } catch (err: unknown) {
      setChatError(err instanceof Error ? err.message : String(err));
    } finally {
      setChatLoading(false);
    }
  }

  function download(content: string, filename: string) {
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  }

  function handleDownloadTranscript() {
    const date   = new Date().toISOString().slice(0, 10);
    const course = state.selectedCourse;
    download(fullTranscript, `transcript_${course}_${date}.txt`);
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-screen overflow-hidden bg-slate-100">

      {/* ══ LEFT PANE — Hebrew Summary (65%) ════════════════════════════════ */}
      <section className="w-[65%] h-full flex flex-col bg-white border-r border-slate-200">

        {/* Header */}
        <div className="bg-bgu-blue px-8 flex items-center justify-between shrink-0 h-[88px]">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              onClick={() => { dispatch({ type: "CLEAR_RESULT" }); stopPollingRef.current?.(); }}
              className="text-bgu-orange/80 hover:text-bgu-orange transition-colors text-sm font-medium"
            >
              ← Back
            </Link>
            <span className="text-white/30">|</span>
            <h1 className="font-display font-bold text-bgu-orange text-lg">Lecture Summary</h1>
            {isProcessing && (
              <span className="text-xs bg-bgu-orange/20 text-bgu-orange border border-bgu-orange/40
                               rounded-full px-2 py-0.5 font-semibold animate-pulse">
                Processing…
              </span>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => { setShowTranscript((v) => !v); setShowMasterSummary(false); }}
              className={`text-xs px-3 py-1.5 rounded-lg transition-colors ${
                showTranscript
                  ? "bg-bgu-orange/20 text-bgu-orange border border-bgu-orange/50"
                  : "bg-white/10 hover:bg-bgu-orange/20 text-bgu-orange border border-bgu-orange/30"
              }`}
            >
              📄 {showTranscript ? "Hide Transcript" : "Show Transcript"}
            </button>
            {masterReady && (
              <button
                onClick={() => download(result.summary, "master_summary.txt")}
                className="text-xs bg-white/10 hover:bg-bgu-orange/20 text-bgu-orange border border-bgu-orange/30 px-3 py-1.5 rounded-lg transition-colors"
              >
                ⬇ Master
              </button>
            )}
            <button
              onClick={() => download(
                result.chunks.map((c) => `Segment ${c.idx + 1}:\n${c.summary}`).join("\n\n---\n\n"),
                "summaries.txt"
              )}
              className="text-xs bg-white/10 hover:bg-bgu-orange/20 text-bgu-orange border border-bgu-orange/30 px-3 py-1.5 rounded-lg transition-colors"
            >
              ⬇ Summaries
            </button>
          </div>
        </div>

        {/* Master Summary button bar — only visible when ready */}
        {masterReady && result.chunks.length > 1 && (
          <div className={`shrink-0 border-b px-6 py-2.5 flex items-center justify-between transition-colors ${
            showMasterSummary
              ? "bg-bgu-orange/10 border-bgu-orange/40"
              : "bg-orange-50 border-bgu-orange/20"
          }`}>
            <button
              onClick={() => { setShowMasterSummary((v) => !v); setShowTranscript(false); }}
              className={`flex items-center gap-2 font-display font-bold text-sm px-4 py-1.5 rounded-lg transition-colors ${
                showMasterSummary
                  ? "bg-bgu-orange text-white shadow-sm"
                  : "bg-white border border-bgu-orange/50 text-bgu-orange hover:bg-bgu-orange/10"
              }`}
            >
              🎯 {showMasterSummary ? "Hide Master Summary" : "Master Summary"}
            </button>
            {showMasterSummary && (
              <button
                onClick={() => download(result.summary, "master_summary.txt")}
                className="flex items-center gap-1.5 text-xs bg-white border border-bgu-orange/40
                           text-bgu-orange hover:bg-bgu-orange/10 px-3 py-1.5 rounded-lg transition-colors"
              >
                ⬇ Download
              </button>
            )}
          </div>
        )}

        {/* Segment Navigator — hidden while master summary is open */}
        {!showMasterSummary && !showTranscript && (
          <SegmentNavigator
            currentIdx={currentSegIdx}
            totalLoaded={result.chunks.length}
            totalExpected={activeJob?.totalSegments ?? (masterReady ? result.totalSegments : null)}
            isProcessing={isProcessing}
            progress={activeJob?.progress ?? ""}
            onPrev={handlePrev}
            onNext={handleNext}
            onJump={handleJump}
            activeCardIdx={totalSegCards > 0 ? activeSegCardIdx : undefined}
            totalCards={totalSegCards > 0 ? totalSegCards : undefined}
          />
        )}

        {/* Content area — master summary / transcript / segment summary */}
        {showMasterSummary ? (
          /* ── Master Summary panel ── */
          <div ref={masterScrollRef} className="flex-1 overflow-y-auto px-10 py-8" dir="rtl" lang="he">
            <SummaryRenderer
              text={result.summary}
              showCardNumbers
              quizData={result.quiz ?? undefined}
              onChatRequest={handleChatRequest}
            />
          </div>
        ) : showTranscript ? (
          /* ── Transcript panel ── */
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Toolbar */}
            <div className="shrink-0 px-6 py-2 bg-[#0d0d14] border-b border-purple-900/40
                            flex items-center justify-between">
              <span className="text-xs text-purple-300 font-mono font-semibold tracking-wide">
                📄 תמלול מלא
              </span>
              <button
                onClick={handleDownloadTranscript}
                className="text-xs bg-purple-900/40 hover:bg-purple-800/50 text-purple-200
                           border border-purple-700/40 px-3 py-1 rounded-lg transition-colors"
              >
                ⬇ הורד תמלול
              </button>
            </div>
            {/* Scrollable transcript */}
            <div className="flex-1 overflow-y-auto px-8 py-6 bg-[#0a0a0f]
                            font-mono text-sm text-slate-300 leading-relaxed"
                 dir="rtl" lang="he">
              {fullTranscript ? (
                <TranscriptRenderer text={fullTranscript} />
              ) : (
                <span className="text-slate-500 italic">
                  {isProcessing ? "התמלול יוצג כשהעיבוד יסתיים…" : "אין תמלול זמין"}
                </span>
              )}
            </div>
          </div>
        ) : (
          /* ── Segment summary ── */
          <div ref={segScrollRef} className="flex-1 overflow-y-auto px-10 py-8" dir="rtl" lang="he">
            {currentChunk ? (
              <>
                <SummaryRenderer text={currentChunk.summary} showCardNumbers onChatRequest={handleChatRequest} />
                {segmentQuizBlocks.map((block) => (
                  <QuizBlockUI
                    key={block.position}
                    questions={block.questions}
                    position={block.position}
                    onChatRequest={handleChatRequest}
                  />
                ))}
              </>
            ) : (
              <div className="flex items-center gap-3 text-slate-400 text-sm">
                <div className="w-4 h-4 border-2 border-slate-300 border-t-bgu-orange rounded-full animate-spin" />
                Waiting for first segment…
              </div>
            )}
          </div>
        )}
      </section>

      {/* ── Fixed card counter — only visible in master summary view ── */}
      {showMasterSummary && totalCards > 0 && (
        <div
          className="fixed bottom-6 left-6 z-50 pointer-events-none
                     flex items-center gap-1.5
                     bg-slate-900/85 backdrop-blur-sm
                     text-white rounded-xl px-4 py-2
                     shadow-lg"
          dir="ltr"
        >
          <span className="text-bgu-orange font-bold text-sm tabular-nums">{activeCardIdx}</span>
          <span className="text-slate-500 text-xs">{'‎'}מתוך{'‎'}</span>
          <span className="text-white font-semibold text-sm tabular-nums">{totalCards}</span>
        </div>
      )}

      {/* ══ RIGHT PANE — AI Tutor Chat (35%) ════════════════════════════════ */}
      <section className="w-[35%] h-full flex flex-col bg-white">

        {/* Chat header */}
        <div className="bg-bgu-blue px-6 shrink-0 h-[88px] flex flex-col justify-center">
          <h2 className="font-display font-bold text-bgu-orange text-base">AI Tutor Chat</h2>
          <p className="text-white/60 text-xs mt-0.5">
            Powered by Gemini ·{" "}
            {masterReady
              ? "Context: master summary"
              : `Context: ${result.chunks.length} segment${result.chunks.length !== 1 ? "s" : ""} loaded`}
          </p>
        </div>

        {/* Stateless context badge */}
        <div className="bg-orange-50 border-b border-bgu-orange/20 px-5 py-2.5 shrink-0">
          <p className="text-xs text-bgu-orange-dark font-medium">
            📎{" "}
            {masterReady
              ? "Using master summary as AI context."
              : "Using all loaded segments as AI context. More context added as segments complete."}
          </p>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-3">
          {chatHistory.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center gap-3">
              <span className="text-5xl">🎓</span>
              <p className="text-sm font-medium text-slate-500">
                {isProcessing
                  ? "You can already ask questions — the tutor has read the available segments."
                  : "Ask anything about the lecture"}
              </p>
              <div className="flex flex-col gap-2 mt-2 w-full max-w-[240px]">
                {[
                  "סכם לי את הנושאים העיקריים",
                  "תסביר לי את המושג הראשון",
                  "תן לי שאלות מבחן",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSend(q)}
                    className="text-xs text-bgu-orange border border-bgu-orange/30
                               rounded-lg px-3 py-2 hover:bg-orange-50 transition-colors text-right"
                    dir="rtl"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {chatHistory.map((msg, i) => <ChatBubble key={i} msg={msg} />)}

          {chatLoading && (
            <div className="flex justify-start">
              <div className="bubble-ai flex items-center gap-2 text-slate-400">
                <div className="flex gap-1">
                  <span className="w-2 h-2 bg-slate-300 rounded-full animate-bounce [animation-delay:0ms]" />
                  <span className="w-2 h-2 bg-slate-300 rounded-full animate-bounce [animation-delay:150ms]" />
                  <span className="w-2 h-2 bg-slate-300 rounded-full animate-bounce [animation-delay:300ms]" />
                </div>
              </div>
            </div>
          )}

          {chatError && (
            <div className="text-xs text-red-500 bg-red-50 rounded-lg px-4 py-2 border border-red-200">
              {chatError}
            </div>
          )}

          <div ref={chatBottomRef} />
        </div>

        {/* Input — rendered by ChatInput which owns its own state */}
        <ChatInput
          disabled={chatLoading}
          onSend={handleSend}
          prefillText={prefillText}
          prefillKey={prefillKey}
        />
      </section>
    </div>
  );
}

export default function SummaryPage() {
  return (
    <AppProvider>
      <SummaryPageInner />
    </AppProvider>
  );
}
