"use client";

import type { Citation } from "@/lib/types";

export function citationLabel(cite: Citation): string {
  const para = cite.para != null ? ` ¶${cite.para}` : "";
  return `${cite.file} p.${cite.page}${para}`;
}

export default function CitationChip({
  cite,
  onJump,
}: {
  cite: Citation;
  onJump: (cite: Citation) => void;
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onJump(cite);
      }}
      title={`Open ${cite.file} at page ${cite.page}`}
      className="inline-flex items-center gap-1 rounded-sm border border-rule bg-panel px-1.5 py-0.5 font-mono text-[11px] leading-4 text-ink-soft transition-colors hover:border-accent hover:bg-accent-wash hover:text-accent-deep"
    >
      <span
        aria-hidden
        className="inline-block h-1.5 w-1.5 rounded-full bg-accent/70"
      />
      {citationLabel(cite)}
    </button>
  );
}
