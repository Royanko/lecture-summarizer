"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { fetchKnowledgeBase } from "@/lib/api";
import SummaryRenderer from "@/components/SummaryRenderer";

const COURSE_META: Record<string, { emoji: string; hebrewName: string; englishName: string; color: string }> = {
  combinatorics:    { emoji: "🔢", hebrewName: "קומבינטוריקה",    englishName: "Combinatorics",    color: "#3b82f6" },
  data_structures:  { emoji: "🗂️", hebrewName: "מבני נתונים",      englishName: "Data Structures",  color: "#8b5cf6" },
  calculus:         { emoji: "📐", hebrewName: 'חדו"א',             englishName: "Calculus",         color: "#22c55e" },
  linear_algebra_2: { emoji: "🔷", hebrewName: "אלגברה לינארית 2", englishName: "Linear Algebra 2", color: "#FF7A00" },
};

export default function KnowledgeBasePage() {
  const params   = useParams();
  const router   = useRouter();
  const courseId = params.course as string;
  const meta     = COURSE_META[courseId];

  const [content,      setContent]      = useState<string | null>(null);
  const [courseLabel,  setCourseLabel]  = useState("");
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState<string | null>(null);

  useEffect(() => {
    if (!courseId) return;
    setLoading(true);
    fetchKnowledgeBase(courseId)
      .then((data) => {
        setContent(data.content);
        setCourseLabel(data.course_label);
      })
      .catch((err: Error) => {
        if (err.message === "not_found") {
          setError("no_kb");
        } else {
          setError(err.message);
        }
      })
      .finally(() => setLoading(false));
  }, [courseId]);

  function handleDownload() {
    if (!content) return;
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url;
    a.download = `knowledge_base_${courseId}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const accentColor = meta?.color ?? "#FF7A00";

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">

      {/* ── Header ── */}
      <header className="bg-bgu-blue px-8 py-4 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="text-bgu-orange/80 hover:text-bgu-orange transition-colors text-sm font-medium"
          >
            ← Back
          </Link>
          <span className="text-white/30">|</span>
          <div className="flex items-center gap-2">
            {meta && (
              <span className="text-2xl">{meta.emoji}</span>
            )}
            <div>
              <h1 className="font-display font-bold text-bgu-orange text-lg leading-tight">
                📚 ספר הקורס
              </h1>
              <p className="text-white/60 text-xs">
                {courseLabel || meta?.englishName || courseId}
              </p>
            </div>
          </div>
        </div>

        {content && (
          <button
            onClick={handleDownload}
            className="flex items-center gap-2 text-sm bg-white/10 hover:bg-bgu-orange/20
                       text-bgu-orange border border-bgu-orange/30 px-4 py-2 rounded-lg
                       transition-colors font-medium"
          >
            ⬇ Download .md
          </button>
        )}
      </header>

      {/* ── Sub-header banner ── */}
      <div
        className="px-8 py-3 flex items-center gap-3 border-b"
        style={{ backgroundColor: `${accentColor}18`, borderColor: `${accentColor}40` }}
      >
        <span className="text-sm font-semibold" style={{ color: accentColor }}>
          📖 ספר ידע מצטבר — כל ההגדרות, המשפטים, ההוכחות והתרגילים מכל ההרצאות
        </span>
        <span className="mr-auto text-xs text-slate-500">
          מתעדכן אוטומטית עם כל הרצאה חדשה
        </span>
      </div>

      {/* ── Content ── */}
      <main className="flex-1 max-w-4xl w-full mx-auto px-8 py-10">

        {loading && (
          <div className="flex flex-col items-center justify-center py-32 gap-4">
            <div className="w-10 h-10 border-4 border-bgu-orange border-t-transparent rounded-full animate-spin" />
            <p className="text-slate-500 text-sm">Loading knowledge base…</p>
          </div>
        )}

        {!loading && error === "no_kb" && (
          <div className="flex flex-col items-center justify-center py-32 gap-6 text-center">
            <span className="text-6xl">📭</span>
            <div>
              <h2 className="font-display font-bold text-xl text-slate-700 mb-2">
                No Knowledge Base Yet
              </h2>
              <p className="text-slate-500 text-sm max-w-sm">
                Process at least one lecture for this course to automatically generate the knowledge base.
              </p>
            </div>
            <button
              onClick={() => router.push("/")}
              className="btn-orange px-6 py-3 text-sm"
            >
              Upload a Lecture
            </button>
          </div>
        )}

        {!loading && error && error !== "no_kb" && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-6 py-5 text-red-700">
            <p className="font-semibold">Failed to load knowledge base</p>
            <p className="text-sm mt-1 text-red-500">{error}</p>
          </div>
        )}

        {!loading && !error && content && (
          <div dir="rtl" lang="he">
            <SummaryRenderer text={content} />
          </div>
        )}

      </main>
    </div>
  );
}
