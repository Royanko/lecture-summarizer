"use client";

import { memo, useEffect, useId, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import type { QuizData, QuizQuestion } from "@/lib/api";

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

// ── LaTeX normalization ───────────────────────────────────────────────────────

/**
 * Normalizes LaTeX delimiters in raw markdown text before it reaches remark-math.
 *
 * 1. Wraps every $$...$$ block with blank lines so remark-math promotes it to
 *    block-level (display) math rather than leaving it inline. Also strips any
 *    leading/trailing spaces on the $$ line so they don't become whitespace-only
 *    "blank" lines that confuse the paragraph parser.
 * 2. Collapses runs of 3+ newlines back to 2.
 * 3. Strips the leading indentation from the line immediately after each $$
 *    block when it has 4+ leading spaces. The blank line inserted in step 1
 *    turns any indented list continuation (including 8-space nested-list
 *    items) into an indented code block; removing the indent restores normal
 *    paragraph rendering. Lines with <4 leading spaces are untouched (they
 *    never trigger code-block parsing).
 * 4. Inserts a space between Hebrew characters and adjacent $ delimiters so
 *    inline math is not visually glued to surrounding RTL text.
 */
function normalizeLatex(text: string): string {
  // Step 1: ensure blank lines around display-math blocks.
  // [ \t]* strips leading/trailing horizontal whitespace on the $$ line so it
  // does not become a whitespace-only line that remark treats as a paragraph break.
  let out = text.replace(
    /[ \t]*\$\$([\s\S]*?)\$\$[ \t]*/g,
    (_, inner: string) => `\n\n$$${inner}$$\n\n`,
  );
  // Step 2: collapse 3+ newlines to exactly 2
  out = out.replace(/\n{3,}/g, "\n\n");
  // Step 3: globally strip 4+ leading spaces from every line that follows a
  // blank line (\n\n). The Step-1 replacement lifts $$...$$ blocks to
  // column 0 by consuming the original leading indent, which breaks any
  // surrounding list context. Once the list is broken, ALL subsequent
  // blank-line + 4-space-indented lines are parsed by remark as indented
  // code blocks — including lines that come after *original* blank lines
  // (e.g. the blank line between "שלב 1" and "שלב 2" within a 3-step
  // proof), not just lines immediately after a $$ block. Stripping 4+
  // leading spaces globally prevents those indented code blocks while
  // keeping unindented content untouched.
  out = out.replace(/\n\n {4,}/g, "\n\n");
  // Step 4: space between Hebrew chars and $ (U+0590-05FF and U+FB1D-FB4F)
  out = out.replace(/([֐-׿יִ-ﭏ])\$/g, "$1 $");
  out = out.replace(/\$([֐-׿יִ-ﭏ])/g, "$ $1");
  return out;
}
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
        : heading.replace(/^[^֐-׿a-zA-Z0-9(]+/, "").trim();
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

// ── Quiz question component ────────────────────────────────────────────────────

const CHOICE_LETTERS = ["א", "ב", "ג", "ד"];

function QuizQuestionCard({
  question,
  onChatRequest,
}: {
  question: QuizQuestion;
  onChatRequest?: (prefill: string) => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);

  function handleChoice(idx: number) {
    if (selected !== null) return; // one attempt only
    setSelected(idx);
  }

  const answered = selected !== null;
  const correct  = selected === question.answer_idx;

  return (
    <div className="quiz-question" dir="rtl">
      {/* Question header */}
      <div className="quiz-question__header">
        <span className="quiz-question__num">שאלה {question.num}</span>
        <span className="quiz-question__text">{question.text}</span>
        {onChatRequest && (
          <button
            className="quiz-chat-btn"
            title="שאל את המורה על שאלה זו"
            onClick={() => onChatRequest(`לגבי שאלה ${question.num}: `)}
            aria-label="שאל את המורה"
          >
            💬
          </button>
        )}
      </div>

      {/* Choices */}
      <div className="quiz-question__choices">
        {question.choices.map((choice, idx) => {
          let cls = "quiz-choice";
          if (answered) {
            if (idx === question.answer_idx) cls += " quiz-choice--correct";
            else if (idx === selected)       cls += " quiz-choice--wrong";
            else                             cls += " quiz-choice--dim";
          } else {
            cls += " quiz-choice--idle";
          }
          return (
            <button
              key={idx}
              className={cls}
              onClick={() => handleChoice(idx)}
              disabled={answered}
            >
              <span className="quiz-choice__letter">{CHOICE_LETTERS[idx]}.</span>
              <span className="quiz-choice__text">{choice.replace(/^[א-ד]\.\s*/, "")}</span>
              {answered && idx === question.answer_idx && (
                <span className="quiz-choice__icon">✓</span>
              )}
              {answered && idx === selected && idx !== question.answer_idx && (
                <span className="quiz-choice__icon">✗</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Feedback */}
      {answered && (
        <p className={`quiz-feedback ${correct ? "quiz-feedback--correct" : "quiz-feedback--wrong"}`}>
          {correct ? "✅ נכון!" : `❌ לא נכון — התשובה הנכונה היא: ${question.choices[question.answer_idx]}`}
        </p>
      )}
    </div>
  );
}

export function QuizBlock({
  questions,
  position,
  onChatRequest,
}: {
  questions: QuizQuestion[];
  position: "mid" | "end";
  onChatRequest?: (prefill: string) => void;
}) {
  const label = position === "mid" ? "שאלות חזרה — אמצע הרצאה" : "שאלות חזרה — סיום הרצאה";
  return (
    <div className="quiz-block" dir="rtl">
      <div className="quiz-block__header">
        <span className="quiz-block__icon">{position === "mid" ? "📝" : "🏁"}</span>
        <span className="quiz-block__title">{label}</span>
      </div>
      <div className="quiz-block__questions">
        {questions.map((q) => (
          <QuizQuestionCard key={q.num} question={q} onChatRequest={onChatRequest} />
        ))}
      </div>
    </div>
  );
}

// ── SummaryRenderer ───────────────────────────────────────────────────────────

// Wrapped in memo so typing in the chat input (which triggers SummaryPageInner
// re-render) does NOT re-render the heavy summary view when `text` hasn't changed.
const SummaryRenderer = memo(function SummaryRenderer({
  text,
  showCardNumbers = false,
  quizData,
  onChatRequest,
}: {
  text:            string;
  showCardNumbers?: boolean;
  quizData?:       QuizData;
  onChatRequest?:  (prefill: string) => void;
}) {
  const segments = parseSegments(text).map((seg) => ({
    ...seg,
    content: normalizeLatex(seg.content),
  }));

  // Count total semantic cards first (needed to know where to splice the mid quiz)
  const totalCards = segments.filter((s) => s.type !== "text").length;
  const midInsertAfter = Math.floor(totalCards / 2);

  // Separate counter for semantic cards only (text segments are not counted).
  // Must be a plain variable — map() is synchronous so sequential mutation is safe.
  let cardCounter = 0;

  const midBlock = quizData?.blocks.find((b) => b.position === "mid");
  const endBlock = quizData?.blocks.find((b) => b.position === "end");

  const rendered: React.ReactNode[] = [];

  segments.forEach((seg, i) => {
    if (seg.type === "text") {
      rendered.push(
        <ReactMarkdown
          key={i}
          remarkPlugins={[remarkMath, remarkGfm]}
          rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false, errorColor: "inherit" }], rehypeRaw]}
          components={summaryComponents}
        >
          {seg.content}
        </ReactMarkdown>
      );
      return;
    }

    cardCounter++;
    const cardNum = cardCounter;
    const cfg = TAG_CONFIG[seg.type];
    const headerText = seg.title
      ? `${cfg.emoji} ${cfg.label}: ${seg.title}`
      : `${cfg.emoji} ${cfg.label}`;

    // Build the card label for the chat prefill
    const cardLabel = seg.title
      ? `${cfg.label}: ${seg.title}`
      : cfg.label;

    rendered.push(
      <div
        key={i}
        className={`semantic-card semantic-card--${cfg.cls}`}
        data-card-idx={cardNum}
      >
        {/* Header — flex row so the card-number badge sits on the
            physical-left end while the title flows RTL on the right */}
        <div
          className="semantic-card__header"
          style={{ display: "flex", alignItems: "center" }}
        >
          <div style={{ flex: 1 }}>
            <ReactMarkdown
              remarkPlugins={[remarkMath, remarkGfm]}
              rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
              components={{ p: ({ children }) => <>{children}</> }}
            >
              {headerText}
            </ReactMarkdown>
          </div>

          {/* Chat icon */}
          {onChatRequest && (
            <button
              className="card-chat-btn"
              title={`שאל את המורה על ${cardLabel}`}
              onClick={() => onChatRequest(`לגבי כרטיס ${cardNum} (${cardLabel}): `)}
              aria-label="שאל את המורה"
            >
              💬
            </button>
          )}

          {showCardNumbers && (
            <span
              dir="ltr"
              style={{
                flexShrink: 0,
                marginRight: "0.6rem",
                fontSize: "0.7rem",
                fontFamily: "Inter, system-ui, sans-serif",
                fontWeight: 500,
                opacity: 0.5,
                background: "rgba(0,0,0,0.07)",
                borderRadius: "0.35rem",
                padding: "0.15rem 0.45rem",
                letterSpacing: "0.02em",
                whiteSpace: "nowrap",
              }}
            >
              {cardNum}/{totalCards}
            </span>
          )}
        </div>

        <div className="semantic-card__body">
          <ReactMarkdown
            remarkPlugins={[remarkMath, remarkGfm]}
            rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
            components={summaryComponents}
          >
            {seg.content}
          </ReactMarkdown>
        </div>
      </div>
    );

    // Splice mid-quiz block after the midpoint card
    if (midBlock && cardCounter === midInsertAfter) {
      rendered.push(
        <QuizBlock
          key="quiz-mid"
          questions={midBlock.questions}
          position="mid"
          onChatRequest={onChatRequest}
        />
      );
    }
  });

  // Append end-quiz block after last card
  if (endBlock) {
    rendered.push(
      <QuizBlock
        key="quiz-end"
        questions={endBlock.questions}
        position="end"
        onChatRequest={onChatRequest}
      />
    );
  }

  return <div className="summary-prose">{rendered}</div>;
});

export default SummaryRenderer;
