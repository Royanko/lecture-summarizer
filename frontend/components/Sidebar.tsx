"use client";

import { useAppState } from "@/lib/AppContext";
import type { ModelKey, LangKey } from "@/lib/AppContext";

const MODELS: { id: ModelKey; label: string }[] = [
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash ⚡ (Recommended)" },
  { id: "gemini-2.5-pro",   label: "Gemini 2.5 Pro 🧠 (Most Capable)" },
];

const LANGS: { id: LangKey; label: string }[] = [
  { id: "he", label: "Hebrew (עברית)" },
  { id: "en", label: "English" },
  { id: "ar", label: "Arabic (عربية)" },
];

export default function Sidebar() {
  const { state, dispatch } = useAppState();
  const { settings } = state;

  function update(patch: Parameters<typeof dispatch>[0]) {
    dispatch(patch);
  }

  return (
    <aside className="w-[25%] min-w-[220px] max-w-[300px] bg-bgu-blue text-white flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="px-6 pt-8 pb-6 border-b border-bgu-blue-light">
        <p className="text-bgu-orange text-xs font-display font-bold tracking-widest uppercase mb-1">
          BGU Lecture Summarizer
        </p>
        <h2 className="font-display font-bold text-xl text-white leading-tight">
          Operation Settings
        </h2>
      </div>

      {/* Settings */}
      <div className="px-6 py-6 flex flex-col gap-6 flex-1">

        {/* AI Model */}
        <div className="flex flex-col gap-2">
          <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            AI Model
          </label>
          <select
            value={settings.model}
            onChange={(e) =>
              update({ type: "SET_SETTINGS", payload: { model: e.target.value as ModelKey } })
            }
            className="w-full bg-bgu-blue-dark text-white border border-bgu-blue-light rounded-lg
                       px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-bgu-orange
                       appearance-none cursor-pointer"
          >
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
        </div>

        {/* Lecture Language */}
        <div className="flex flex-col gap-2">
          <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Lecture Language
          </label>
          <select
            value={settings.lang}
            onChange={(e) =>
              update({ type: "SET_SETTINGS", payload: { lang: e.target.value as LangKey } })
            }
            className="w-full bg-bgu-blue-dark text-white border border-bgu-blue-light rounded-lg
                       px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-bgu-orange
                       appearance-none cursor-pointer"
          >
            {LANGS.map((l) => (
              <option key={l.id} value={l.id}>{l.label}</option>
            ))}
          </select>
        </div>

        {/* Segment Length */}
        <div className="flex flex-col gap-2">
          <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Segment Length
            <span className="ml-2 text-bgu-orange font-bold">{settings.chunkMinutes} min</span>
          </label>
          <input
            type="range"
            min={5}
            max={20}
            step={1}
            value={settings.chunkMinutes}
            onChange={(e) =>
              update({ type: "SET_SETTINGS", payload: { chunkMinutes: Number(e.target.value) } })
            }
            className="w-full accent-bgu-orange cursor-pointer"
          />
          <div className="flex justify-between text-xs text-slate-400">
            <span>5 min</span>
            <span>20 min</span>
          </div>
          <p className="text-xs text-slate-400">
            Only applies to files &gt;500 MB split by ffmpeg.
          </p>
        </div>

      </div>

      {/* Footer */}
      <div className="px-6 py-5 border-t border-bgu-blue-light">
        <div className="text-xs text-slate-400 leading-relaxed">
          <p className="font-semibold text-slate-300 mb-1">How to use</p>
          <ol className="list-decimal list-inside space-y-1">
            <li>Select a course below</li>
            <li>Upload a lecture file</li>
            <li>Wait for AI processing</li>
            <li>Read summary &amp; chat with the tutor</li>
          </ol>
        </div>
        <p className="mt-4 text-[0.7rem] text-slate-500">
          BGU Lecture Summarizer · Powered by Gemini
        </p>
      </div>
    </aside>
  );
}
