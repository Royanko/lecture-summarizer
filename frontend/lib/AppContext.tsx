"use client";

/**
 * Global app state — replaces Streamlit's session_state.
 * See the HYDRATION SAFETY DESIGN comment block for why we never read
 * sessionStorage during render (only in useEffect after hydration).
 */

import React, {
  createContext,
  useContext,
  useReducer,
  useEffect,
  useState,
} from "react";
import type { Job } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────────

export type CourseKey =
  | "combinatorics"
  | "data_structures"
  | "calculus"
  | "linear_algebra_2";
export type LangKey  = "he" | "en" | "ar";
export type ModelKey = "gemini-2.5-flash" | "gemini-2.5-pro";

export interface AppSettings {
  model:        ModelKey;
  lang:         LangKey;
  chunkMinutes: number;
  apiKey:       string;
}

export interface ChunkEntry {
  idx:        number;
  transcript: string;
  summary:    string;
}

export interface ChatTurn {
  role: "user" | "model";
  text: string;
}

export interface AppResult {
  transcript:      string;
  summary:         string;   // master summary — empty string while processing
  course:          CourseKey;
  chunks:          ChunkEntry[];
  chatSeed:        ChatTurn[];
  savedSummary:    string | null;
  savedTranscript: string | null;
  totalSegments:   number;
}

/**
 * Tracks an in-flight background job.
 * Stored alongside AppResult so the summary page can resume polling
 * after a page refresh if the job is still running.
 */
export interface ActiveJob {
  jobId:          string;
  status:         "queued" | "processing" | "done" | "error";
  progress:       string;
  totalSegments:  number | null;  // null until the backend has split the file
  error:          string | null;
}

export interface AppState {
  settings:       AppSettings;
  result:         AppResult | null;
  selectedCourse: CourseKey;
  activeJob:      ActiveJob | null;
}

type Action =
  | { type: "SET_SETTINGS";    payload: Partial<AppSettings> }
  | { type: "SET_RESULT";      payload: AppResult }
  | { type: "SET_COURSE";      payload: CourseKey }
  | { type: "CLEAR_RESULT" }
  | { type: "SET_ACTIVE_JOB";  payload: ActiveJob }
  | { type: "CLEAR_ACTIVE_JOB" }
  /**
   * UPDATE_FROM_JOB merges a raw Job API response into state:
   *   - Updates activeJob.status / progress / totalSegments / error
   *   - Merges new chunks into result.chunks (deduped by idx, sorted)
   *   - When job is "done", writes master summary / transcript / saved paths
   */
  | { type: "UPDATE_FROM_JOB"; payload: Job }
  // Internal: merge persisted state after hydration. Never dispatch manually.
  | { type: "RESTORE";         payload: Partial<AppState> };

// ── Defaults ───────────────────────────────────────────────────────────────────

const DEFAULT_STATE: AppState = {
  settings: {
    model:        "gemini-2.5-flash",
    lang:         "he",
    chunkMinutes: 12,
    apiKey:       "",
  },
  result:         null,
  selectedCourse: "combinatorics",
  activeJob:      null,
};

// ── Reducer ────────────────────────────────────────────────────────────────────

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {

    case "SET_SETTINGS":
      return { ...state, settings: { ...state.settings, ...action.payload } };

    case "SET_RESULT":
      return { ...state, result: action.payload };

    case "SET_COURSE":
      return { ...state, selectedCourse: action.payload };

    case "CLEAR_RESULT":
      return { ...state, result: null, activeJob: null };

    case "SET_ACTIVE_JOB":
      return { ...state, activeJob: action.payload };

    case "CLEAR_ACTIVE_JOB":
      return { ...state, activeJob: null };

    case "UPDATE_FROM_JOB": {
      const job = action.payload;

      // Update activeJob tracking
      const updatedActiveJob: ActiveJob = {
        jobId:         state.activeJob?.jobId ?? job.job_id,
        status:        job.status as ActiveJob["status"],
        progress:      job.progress,
        totalSegments: job.total_segments,
        error:         job.error,
      };

      // Merge incoming chunks with existing ones (dedup by idx)
      const existingChunks = state.result?.chunks ?? [];
      const incomingChunks: ChunkEntry[] = job.chunks ?? [];

      const chunkMap = new Map<number, ChunkEntry>(
        existingChunks.map((c) => [c.idx, c])
      );
      for (const c of incomingChunks) {
        chunkMap.set(c.idx, c);
      }
      const mergedChunks = Array.from(chunkMap.values()).sort(
        (a, b) => a.idx - b.idx
      );

      // Build updated result, preserving existing fields when job hasn't set them yet
      const baseResult = state.result ?? {
        transcript:      "",
        summary:         "",
        course:          state.selectedCourse,
        chunks:          [],
        chatSeed:        [],
        savedSummary:    null,
        savedTranscript: null,
        totalSegments:   0,
      };

      const updatedResult: AppResult = {
        ...baseResult,
        chunks:          mergedChunks,
        totalSegments:   job.total_segments ?? baseResult.totalSegments,
        // Only overwrite summary / transcript / paths when the job is done
        // (they are null/undefined while processing)
        ...(job.status === "done" && {
          summary:         job.summary         ?? baseResult.summary,
          transcript:      job.transcript      ?? baseResult.transcript,
          chatSeed:        job.chat_seed       ?? baseResult.chatSeed,
          savedSummary:    job.saved_summary   ?? baseResult.savedSummary,
          savedTranscript: job.saved_transcript ?? baseResult.savedTranscript,
        }),
      };

      return {
        ...state,
        activeJob: updatedActiveJob,
        result:    updatedResult,
      };
    }

    case "RESTORE":
      return {
        ...state,
        ...action.payload,
        settings: { ...state.settings, ...(action.payload.settings ?? {}) },
      };

    default:
      return state;
  }
}

// ── Context ────────────────────────────────────────────────────────────────────

interface AppContextValue {
  state:    AppState;
  dispatch: React.Dispatch<Action>;
  hydrated: boolean;
}

const AppContext = createContext<AppContextValue | null>(null);

const SESSION_KEY = "bgu-app-state";

export function AppProvider({ children }: { children: React.ReactNode }) {
  // Always start from DEFAULT_STATE — server and client first renders match.
  const [state, dispatch] = useReducer(reducer, DEFAULT_STATE);
  const [hydrated, setHydrated] = useState(false);

  // After hydration: restore persisted state from sessionStorage.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<AppState>;
        dispatch({ type: "RESTORE", payload: parsed });
      }
    } catch {
      // Corrupted storage or private-browsing — start fresh.
    } finally {
      setHydrated(true);
    }
  }, []);

  // Persist on every change, but only after hydration to avoid overwriting
  // good persisted state with DEFAULT_STATE during the brief pre-hydration window.
  useEffect(() => {
    if (!hydrated) return;
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(state));
    } catch {
      // sessionStorage unavailable (private browsing, quota exceeded).
    }
  }, [state, hydrated]);

  return (
    <AppContext.Provider value={{ state, dispatch, hydrated }}>
      {children}
    </AppContext.Provider>
  );
}

export function useAppState(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppState must be used inside <AppProvider>");
  return ctx;
}
