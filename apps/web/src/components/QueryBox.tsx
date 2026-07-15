"use client";

import { useState } from "react";
import { queryMatter } from "@/lib/api";
import type { Citation, QueryResponse } from "@/lib/types";
import CitationChip from "./CitationChip";

export default function QueryBox({
  matterId,
  onJump,
}: {
  matterId: string;
  onJump: (cite: Citation) => void;
}) {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [result, setResult] = useState<{
    question: string;
    response: QueryResponse;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setError(null);
    try {
      const response = await queryMatter(matterId, q);
      setResult({ question: q, response });
      setQuestion("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Query failed.");
    } finally {
      setAsking(false);
    }
  };

  return (
    <div className="shrink-0 border-t border-rule bg-panel">
      {result && (
        <div className="pane-scroll max-h-48 overflow-y-auto border-b border-rule-soft px-4 py-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-muted">
            Q · {result.question}
          </p>
          {result.response.not_found ? (
            <p className="mt-1.5 text-sm italic text-ink-muted">
              not found in the record
            </p>
          ) : (
            <>
              <p className="mt-1.5 text-sm leading-relaxed text-ink">
                {result.response.answer}
              </p>
              {result.response.cites.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {result.response.cites.map((c, i) => (
                    <CitationChip key={i} cite={c} onJump={onJump} />
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
      {error && (
        <p className="border-b border-rule-soft px-4 py-2 text-xs text-oxblood">
          {error}
        </p>
      )}
      <form
        className="flex items-center gap-2 px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask the record — e.g. “When was the injunction first granted?”"
          className="min-w-0 flex-1 rounded-sm border border-rule bg-paper px-3 py-2 text-sm text-ink placeholder:text-ink-muted/70 focus:border-accent focus:outline-none"
        />
        <button
          type="submit"
          disabled={asking || question.trim() === ""}
          className="shrink-0 rounded-sm bg-accent px-4 py-2 text-sm font-semibold text-panel transition-colors hover:bg-accent-deep disabled:cursor-not-allowed disabled:opacity-50"
        >
          {asking ? "Searching…" : "Ask"}
        </button>
      </form>
    </div>
  );
}
