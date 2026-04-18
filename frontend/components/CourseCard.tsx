"use client";

import type { CourseKey } from "@/lib/AppContext";

export interface CourseCardProps {
  id: CourseKey;
  emoji: string;
  hebrewName: string;
  englishName: string;
  color: string;          // Tailwind-compatible hex or CSS color
  recordings: number;
  summaries: number;
  isSelected: boolean;
  onSelect: (id: CourseKey) => void;
}

export default function CourseCard({
  id,
  emoji,
  hebrewName,
  englishName,
  color,
  recordings,
  summaries,
  isSelected,
  onSelect,
}: CourseCardProps) {
  return (
    <button
      onClick={() => onSelect(id)}
      className="group relative flex flex-col items-center justify-center gap-2
                 w-full min-h-[140px] rounded-xl2 border-2 p-4
                 bg-white text-center
                 transition-all duration-200 ease-out
                 focus:outline-none focus:ring-2 focus:ring-bgu-orange focus:ring-offset-2
                 hover:-translate-y-1 hover:shadow-card-hover"
      style={{
        borderColor: isSelected ? color : "#e2e8f0",
        backgroundColor: isSelected ? `${color}12` : "#ffffff",
        boxShadow: isSelected ? `0 4px 24px 0 ${color}33` : undefined,
      }}
      aria-pressed={isSelected}
    >
      {/* Selected tick */}
      {isSelected && (
        <span
          className="absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center text-white text-xs font-bold"
          style={{ backgroundColor: color }}
        >
          ✓
        </span>
      )}

      {/* Emoji */}
      <span className="text-4xl leading-none">{emoji}</span>

      {/* Hebrew name */}
      <span
        className="font-display font-bold text-sm text-black leading-tight"
        style={{ direction: "rtl" }}
      >
        {hebrewName}
      </span>

      {/* English name */}
      <span className="text-xs text-slate-500 font-medium">{englishName}</span>

      {/* Hover tooltip with stats */}
      <div
        className="absolute bottom-[calc(100%+8px)] left-1/2 -translate-x-1/2
                   bg-bgu-blue text-white text-xs rounded-xl px-3 py-2
                   whitespace-nowrap shadow-lg pointer-events-none
                   opacity-0 group-hover:opacity-100 transition-opacity duration-150
                   z-10"
      >
        <div>🎙 {recordings} recordings</div>
        <div>📄 {summaries} summaries</div>
        {/* Tooltip arrow */}
        <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-bgu-blue" />
      </div>
    </button>
  );
}
