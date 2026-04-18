"use client";

import { memo, useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";

// ── Types ──────────────────────────────────────────────────────────────────────

export type TagType = "DEFINITION" | "THEOREM" | "CLAIM" | "PROOF" | "EXERCISE" | "NOTE";

export const TAG_CONFIG: Record<TagType, { cls: string; emoji: string; label: string }> = {
  DEFINITION: { cls: "definition", emoji: "📖", label: "הגדרה" },
  THEOREM:    { cls: "theorem",    emoji: "💡", label: "משפט" },
  CLAIM:      { cls: "claim",      emoji: "🔷", label: "טענה" },
  PROOF:      { cls: "proof",      emoji: "✍️",  label: "הוכחה" },
  EXERCISE:   { cls: "exercise",   emoji: "🎯", label: "תרגיל" },
  NOTE:       { cls: "note",       emoji: "💜", label: "הערה" },
};

type Segment =
  | { type: "text"; content: string }
  | { type: TagType; title: string; content: string };

// ── Mermaid diagram renderer ──────────────────────────────────────────────────

// Promise-based singleton so concurrent renders never call initialize() twice.
let _mermaidPromise: Promise<typeof import("mermaid")["default"]> | null = null;

function _getMermaid(): Promise<typeof import("mermaid")["default"]> {
  if (!_mermaidPromise) {
    _mermaidPromise = import("mermaid").then((m) => {
      m.default.initialize({
        startOnLoad: false,
        theme: "default",
        fontFamily: "'Inter', 'Noto Sans Hebrew', system-ui, sans-serif",
        fontSize: 15,
        flowchart: { useMaxWidth: true, htmlLabels: true },
        sequence:  { useMaxWidth: true },
        gantt:     { useMaxWidth: true },
      });
      return m.default;
    });
  }
  return _mermaidPromise;
}

/**
 * Sanitize Mermaid code before passing it to the renderer.
 *
 * Problem: Mermaid v11 requires subgraph names that contain spaces,
 * parentheses, or other non-identifier characters to be wrapped in
 * double quotes.  LLMs routinely emit the unquoted form, e.g.
 *   subgraph Level 0 (Root)
 * which Mermaid rejects as a syntax error.  We fix those names here
 * so the renderer never sees invalid syntax.
 *
 * Rule: a subgraph line whose name is not already quoted AND contains
 * at least one character outside [A-Za-z0-9_-] is wrapped in "…".
 */
function _sanitizeMermaid(code: string): string {
  return code.replace(
    /^(\s*subgraph\s+)([^\n"'`]+)$/gm,
    (_, prefix: string, name: string) => {
      const trimmed = name.trim();
      // Already has direction keyword only (TD / LR / etc.) — skip
      if (/^(TB|TD|BT|RL|LR)$/i.test(trimmed)) return prefix + name;
      // Contains any character that needs quoting
      if (/[\s()\[\]{}|<>]/.test(trimmed)) {
        return `${prefix}"${trimmed}"`;
      }
      return prefix + name;
    }
  );
}

function MermaidDiagram({ code }: { code: string }) {
  const uid          = useId().replace(/:/g, "");
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function render() {
      try {
        const mermaid = await _getMermaid();
        const id = `mermaid-${uid}`;
        const { svg } = await mermaid.render(id, _sanitizeMermaid(code.trim()));

        if (cancelled) return;

        // Mermaid v10/v11 sometimes returns an error SVG instead of throwing.
        // Detect it by the text it injects and show the raw-code fallback instead.
        if (svg.includes("Syntax error") || svg.includes("error-icon")) {
          setError("diagram-syntax-error");
          return;
        }

        if (containerRef.current) {
          containerRef.current.innerHTML = svg;
          const svgEl = containerRef.current.querySelector("svg");
          if (svgEl) {
            svgEl.removeAttribute("height");
            svgEl.style.maxWidth = "100%";
            svgEl.style.height   = "auto";
          }
        }
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    }
    render();
    return () => { cancelled = true; };
  }, [code, uid]);

  if (error) {
    return (
      <pre className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg p-3 overflow-x-auto">
        {code}
      </pre>
    );
  }

  return (
    <div
      ref={containerRef}
      dir="ltr"
      className="my-4 flex justify-center overflow-x-auto rounded-xl
                 bg-white border border-slate-200 p-4 shadow-sm"
    />
  );
}

// ── Markdown table + code components ─────────────────────────────────────────

const summaryComponents = {
  table: ({ children, ...props }: React.HTMLAttributes<HTMLTableElement>) => (
    <table dir="rtl" style={{ textAlign: "right" }} {...props}>{children}</table>
  ),
  th: ({ children, ...props }: React.HTMLAttributes<HTMLTableCellElement>) => (
    <th className="text-right" style={{ textAlign: "right" }} {...props}>{children}</th>
  ),
  td: ({ children, ...props }: React.HTMLAttributes<HTMLTableCellElement>) => (
    <td className="text-right" style={{ textAlign: "right" }} {...props}>{children}</td>
  ),
  code: ({ className, children, ...props }: React.HTMLAttributes<HTMLElement>) => {
    const lang = /language-(\w+)/.exec(className ?? "")?.[1];
    if (lang === "mermaid") {
      return <MermaidDiagram code={String(children).trim()} />;
    }
    return <code className={className} {...props}>{children}</code>;
  },
};

// ── Segment parser ────────────────────────────────────────────────────────────

/**
 * Splits markdown text into plain-text segments and typed card segments.
 *
 * Primary match: optional "#### heading\n" immediately before [TAG]
 * Fallback match: heading lines containing a tag-label keyword (הגדרה, משפט, etc.)
 *   but missing the [TAG] marker. This handles LLM output where Gemini dropped
 *   the tag line under load.
 */

const HEADING_LABEL_TO_TAG: Record<string, TagType> = {
  "הגדרה": "DEFINITION",
  "משפט": "THEOREM",
  "טענה": "CLAIM",
  "הוכחה": "PROOF",
  "תרגיל": "EXERCISE",
  "הערה": "NOTE",
};

function parseSegments(text: string): Segment[] {
  const BLOCK_RE =
    /(?:#{1,4}[^\S\n]*([^\n]+)\n+[^\S\n]*)?\[(DEFINITION|THEOREM|CLAIM|PROOF|EXERCISE|NOTE)\]/gi;

  const hits: Array<{ start: number; end: number; tagType: TagType; subtitle: string }> = [];

  for (const m of text.matchAll(BLOCK_RE)) {
    const heading  = (m[1] ?? "").trim();
    const tagType  = m[2].toUpperCase() as TagType;
    const colonIdx = heading.indexOf(":");
    const subtitle =
      colonIdx >= 0
        ? heading.slice(colonIdx + 1).trim()
        : heading.replace(/^[^\u0590-\u05FFa-zA-Z0-9(]+/, "").trim();
    hits.push({ start: m.index!, end: m.index! + m[0].length, tagType, subtitle });
  }

  // Fallback: if no tags were found, try to match heading-only patterns
  // like "#### 💜 הערה / הסבר: Topic" without [TAG] on the next line.
  if (hits.length === 0) {
    const HEADING_RE =
      /^(####)\s+[^\n]*(הגדרה|משפט|טענה|הוכחה|תרגיל|הערה)[^\n]*/gm;
    for (const m of text.matchAll(HEADING_RE)) {
      const fullLine = m[0];
      const keyword  = m[2];
      const tagType  = HEADING_LABEL_TO_TAG[keyword];
      if (!tagType) continue;
      const colonIdx = fullLine.indexOf(":");
      const subtitle =
        colonIdx >= 0
          ? fullLine.slice(colonIdx + 1).trim()
          : "";
      hits.push({
        start: m.index!,
        end:   m.index! + fullLine.length,
        tagType,
        subtitle,
      });
    }
    // Sort by position (matchAll order is guaranteed, but be safe)
    hits.sort((a, b) => a.start - b.start);
  }

  const segments: Segment[] = [];
  let cursor = 0;

  for (let i = 0; i < hits.length; i++) {
    const { start, end, tagType, subtitle } = hits[i];
    const nextStart = i + 1 < hits.length ? hits[i + 1].start : text.length;
    const preamble = text.slice(cursor, start).replace(/\n+---\s*$/, "").trim();
    if (preamble) segments.push({ type: "text", content: preamble });
    const body = text.slice(end, nextStart).replace(/\n+---\s*$/, "").trim();
    segments.push({ type: tagType, title: subtitle, content: body });
    cursor = nextStart;
  }

  const trailing = text.slice(cursor).trim();
  if (trailing) segments.push({ type: "text", content: trailing });

  return segments;
}

// ── SummaryRenderer ───────────────────────────────────────────────────────────

// Wrapped in memo so typing in the chat input (which triggers SummaryPageInner
// re-render) does NOT re-render the heavy summary view when `text` hasn't changed.
const SummaryRenderer = memo(function SummaryRenderer({ text }: { text: string }) {
  const segments = parseSegments(text);

  return (
    <div className="summary-prose">
      {segments.map((seg, i) => {
        if (seg.type === "text") {
          return (
            <ReactMarkdown
              key={i}
              remarkPlugins={[remarkMath, remarkGfm]}
              rehypePlugins={[rehypeRaw, rehypeKatex]}
              components={summaryComponents}
            >
              {seg.content}
            </ReactMarkdown>
          );
        }

        const cfg = TAG_CONFIG[seg.type];
        const headerText = seg.title
          ? `${cfg.emoji} ${cfg.label}: ${seg.title}`
          : `${cfg.emoji} ${cfg.label}`;

        return (
          <div key={i} className={`semantic-card semantic-card--${cfg.cls}`}>
            <div className="semantic-card__header">
              <ReactMarkdown
                remarkPlugins={[remarkMath, remarkGfm]}
                rehypePlugins={[rehypeKatex]}
                components={{ p: ({ children }) => <>{children}</> }}
              >
                {headerText}
              </ReactMarkdown>
            </div>
            <div className="semantic-card__body">
              <ReactMarkdown
                remarkPlugins={[remarkMath, remarkGfm]}
                rehypePlugins={[rehypeKatex]}
                components={summaryComponents}
              >
                {seg.content}
              </ReactMarkdown>
            </div>
          </div>
        );
      })}
    </div>
  );
});

export default SummaryRenderer;
