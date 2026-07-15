"use client";

import { useEffect, useState } from "react";
import { ApiError, createDraft, draftExportUrl, getDraft, listDrafts } from "@/lib/api";
import type {
  Citation,
  DraftDocType,
  DraftDocument,
  DraftParagraph,
  DraftSummary,
  DraftViolation,
} from "@/lib/types";
import CitationChip from "./CitationChip";

const DOC_TYPES: { value: DraftDocType; label: string }[] = [
  { value: "legal_notice", label: "Legal notice" },
  { value: "written_statement", label: "Written statement" },
  { value: "bail_application", label: "Bail application" },
  { value: "plaint", label: "Plaint" },
];

const DOC_TYPE_LABELS: Record<DraftDocType, string> = Object.fromEntries(
  DOC_TYPES.map((t) => [t.value, t.label]),
) as Record<DraftDocType, string>;

/* Placeholders the drafter could not fill from the record look like
   "[● name of addressee ]" — emphasize them so they cannot be missed. */
const PLACEHOLDER_RE = /(\[●[^\]]*\])/;

function DraftText({ text }: { text: string }) {
  const parts = text.split(PLACEHOLDER_RE);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <span
            key={i}
            className="rounded-sm bg-verify-wash px-1 font-medium text-verify"
          >
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}

function Paragraph({
  para,
  number,
  onJump,
}: {
  para: DraftParagraph;
  number: number | null;
  onJump: (cite: Citation) => void;
}) {
  const factual = para.kind === "factual";
  return (
    <div className="flex gap-3">
      <span className="w-6 shrink-0 text-right font-mono text-xs leading-relaxed text-ink-muted">
        {number !== null ? `${number}.` : ""}
      </span>
      <div className="min-w-0 flex-1">
        <p
          className={`text-sm leading-relaxed ${
            factual ? "text-ink" : "text-ink-muted"
          }`}
        >
          <DraftText text={para.text} />
        </p>
        {(para.cites.length > 0 || !para.verified) && (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {para.cites.map((c, i) => (
              <CitationChip
                key={`${c.file}-${c.page}-${c.para}-${i}`}
                cite={c}
                onJump={onJump}
              />
            ))}
            {!para.verified && (
              <span className="rounded-sm bg-oxblood-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-oxblood">
                unverified
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function DraftView({
  matterId,
  draftId,
  draft,
  onJump,
}: {
  matterId: string;
  draftId: string;
  draft: DraftDocument;
  onJump: (cite: Citation) => void;
}) {
  let n = 0;
  return (
    <div className="border-t border-rule-soft bg-paper/60 px-4 py-4">
      {draft.court_header && (
        <p className="whitespace-pre-line text-center font-mono text-[11px] leading-relaxed text-ink-soft">
          {draft.court_header}
        </p>
      )}
      <p className="mt-2 text-center font-display text-sm font-bold text-ink">
        {draft.title}
      </p>

      <div className="mt-4 space-y-3">
        {draft.paragraphs.map((para, i) => (
          <Paragraph
            key={i}
            para={para}
            number={para.kind === "factual" ? ++n : null}
            onJump={onJump}
          />
        ))}
      </div>

      {draft.prayer.length > 0 && (
        <div className="mt-5">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-muted">
            Prayer
          </p>
          <ol className="mt-1.5 space-y-1.5">
            {draft.prayer.map((item, i) => (
              <li key={i} className="flex gap-3">
                <span className="w-6 shrink-0 text-right font-mono text-xs leading-relaxed text-ink-muted">
                  {String.fromCharCode(97 + i)})
                </span>
                <p className="min-w-0 flex-1 text-sm leading-relaxed text-ink">
                  <DraftText text={item} />
                </p>
              </li>
            ))}
          </ol>
        </div>
      )}

      {draft.missing_info.length > 0 && (
        <div className="mt-5 rounded-sm border border-verify/30 bg-verify-wash px-3 py-2.5">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-verify">
            To fill before filing
          </p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4">
            {draft.missing_info.map((item, i) => (
              <li key={i} className="text-xs leading-snug text-verify">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4">
        <a
          href={draftExportUrl(matterId, draftId)}
          className="inline-block rounded-sm border border-accent px-3 py-1.5 text-sm font-semibold text-accent transition-colors hover:bg-accent-wash"
        >
          Download .docx
        </a>
      </div>
    </div>
  );
}

export default function DraftPanel({
  matterId,
  onJump,
}: {
  matterId: string;
  onJump: (cite: Citation) => void;
}) {
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [docType, setDocType] = useState<DraftDocType>("legal_notice");
  const [instructions, setInstructions] = useState("");

  const [generating, setGenerating] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [violations, setViolations] = useState<DraftViolation[]>([]);

  const [openId, setOpenId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Record<string, DraftDocument>>({});

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDrafts(matterId)
      .then((d) => {
        if (!cancelled) setDrafts(d);
      })
      .catch((err) => {
        // 404 simply means no drafts exist for this matter yet.
        if (!cancelled && !(err instanceof ApiError && err.status === 404)) {
          setError(
            err instanceof Error ? err.message : "Failed to load drafts.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [matterId]);

  useEffect(() => {
    if (!generating) return;
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [generating]);

  const draftDocument = async () => {
    if (generating) return;
    setElapsed(0);
    setGenerating(true);
    setError(null);
    try {
      const res = await createDraft(matterId, docType, instructions.trim());
      setDrafts((prev) => [
        {
          draft_id: res.draft_id,
          doc_type: res.draft.doc_type,
          title: res.draft.title,
          paragraphs: res.draft.paragraphs.length,
          missing_info: res.draft.missing_info.length,
        },
        ...prev.filter((d) => d.draft_id !== res.draft_id),
      ]);
      setLoaded((prev) => ({ ...prev, [res.draft_id]: res.draft }));
      setViolations(res.violations);
      setOpenId(res.draft_id);
      setInstructions("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Drafting failed.");
    } finally {
      setGenerating(false);
    }
  };

  const toggle = async (draftId: string) => {
    if (openId === draftId) {
      setOpenId(null);
      return;
    }
    if (loaded[draftId]) {
      setOpenId(draftId);
      return;
    }
    setLoadingId(draftId);
    setError(null);
    try {
      const draft = await getDraft(matterId, draftId);
      setLoaded((prev) => ({ ...prev, [draftId]: draft }));
      setOpenId(draftId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load draft.");
    } finally {
      setLoadingId(null);
    }
  };

  return (
    <div className="flex min-h-0 shrink-0 flex-col border-t border-rule bg-panel">
      <div className="shrink-0 px-4 pb-3 pt-3">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-muted">
          Drafting
        </p>

        {generating ? (
          <div className="mt-2">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium text-ink-soft">
                Drafting {DOC_TYPE_LABELS[docType].toLowerCase()}…
              </p>
              <span className="font-mono text-xs text-ink-muted">
                {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
              </span>
            </div>
            <div className="analyzing-bar mt-2 h-1.5 rounded-full bg-rule-soft" />
            <p className="mt-2 text-xs text-ink-muted">
              Composing the document from the record — every factual paragraph
              carries a citation. This can take a few minutes.
            </p>
          </div>
        ) : (
          <form
            className="mt-2 flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void draftDocument();
            }}
          >
            <select
              value={docType}
              onChange={(e) => setDocType(e.target.value as DraftDocType)}
              className="shrink-0 rounded-sm border border-rule bg-paper px-2 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            >
              {DOC_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder='e.g. "Demand possession within 15 days"'
              className="min-w-0 flex-1 rounded-sm border border-rule bg-paper px-3 py-2 text-sm text-ink placeholder:text-ink-muted/70 focus:border-accent focus:outline-none"
            />
            <button
              type="submit"
              className="shrink-0 rounded-sm bg-accent px-4 py-2 text-sm font-semibold text-panel transition-colors hover:bg-accent-deep disabled:cursor-not-allowed disabled:opacity-50"
            >
              Draft
            </button>
          </form>
        )}

        {violations.length > 0 && !generating && (
          <span
            title={violations
              .map((v) => `${v.kind} — ¶${v.paragraph}`)
              .join("\n")}
            className="mt-2 inline-block rounded-sm border border-verify/30 bg-verify-wash px-2 py-1 text-[11px] font-medium text-verify"
          >
            {violations.length} unverified{" "}
            {violations.length === 1 ? "paragraph" : "paragraphs"} flagged
          </span>
        )}

        {error && <p className="mt-2 text-xs text-oxblood">{error}</p>}
      </div>

      {drafts.length > 0 && (
        <div className="pane-scroll max-h-[40vh] min-h-0 overflow-y-auto border-t border-rule-soft">
          <ul>
            {drafts.map((d) => (
              <li key={d.draft_id} className="border-b border-rule-soft">
                <button
                  type="button"
                  onClick={() => void toggle(d.draft_id)}
                  className={`flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-accent-wash/50 ${
                    openId === d.draft_id ? "bg-accent-wash/40" : ""
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
                    {d.title}
                  </span>
                  <span className="shrink-0 rounded-sm bg-panel-2 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-muted">
                    {DOC_TYPE_LABELS[d.doc_type] ?? d.doc_type}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-ink-muted">
                    {d.paragraphs} ¶¶
                  </span>
                  {d.missing_info > 0 && (
                    <span className="shrink-0 rounded-sm border border-verify/30 bg-verify-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-verify">
                      {d.missing_info} to fill
                    </span>
                  )}
                  {loadingId === d.draft_id && (
                    <span className="shrink-0 text-[10px] text-ink-muted">
                      Loading…
                    </span>
                  )}
                </button>
                {openId === d.draft_id && loaded[d.draft_id] && (
                  <DraftView
                    matterId={matterId}
                    draftId={d.draft_id}
                    draft={loaded[d.draft_id]}
                    onJump={onJump}
                  />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
