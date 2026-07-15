"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  createDraft,
  draftExportUrl,
  getDraft,
  listDrafts,
  listJobs,
} from "@/lib/api";
import { isJobLive } from "@/lib/types";
import type {
  Citation,
  DraftDocType,
  DraftDocument,
  DraftParagraph,
  DraftSummary,
  JobProvider,
  JobRecord,
  ListOfDatesEntry,
} from "@/lib/types";
import { useJob, useJobElapsed } from "@/lib/useJob";
import CitationChip from "./CitationChip";
import ProviderChip from "./ProviderChip";

const DOC_TYPES: { value: DraftDocType; label: string }[] = [
  { value: "legal_notice", label: "Legal notice" },
  { value: "written_statement", label: "Written statement" },
  { value: "bail_application", label: "Bail application" },
  { value: "plaint", label: "Plaint" },
  { value: "synopsis_and_list_of_dates", label: "Synopsis & list of dates" },
  { value: "writ_petition", label: "Writ petition (Art. 226)" },
  { value: "slp", label: "SLP (Art. 136)" },
];

const DOC_TYPE_LABELS: Record<DraftDocType, string> = Object.fromEntries(
  DOC_TYPES.map((t) => [t.value, t.label]),
) as Record<DraftDocType, string>;

/* Placeholders the drafter could not fill from the record look like
   "[● name of addressee ]" — emphasize them so they cannot be missed. */
const PLACEHOLDER_RE = /(\[●[^\]]*\])/;

/** Which model wrote each draft, by draft id, read off the matter's job
 *  history. The job record is the only thing that knows — a draft on its own
 *  does not carry its author. */
function providersByDraft(jobs: JobRecord[]): Record<string, JobProvider> {
  const map: Record<string, JobProvider> = {};
  for (const job of jobs) {
    if (job.kind !== "draft" || job.status !== "succeeded") continue;
    const draftId = job.result?.draft_id;
    if (draftId && job.provider) map[draftId] = job.provider;
  }
  return map;
}

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
  marker,
  onJump,
}: {
  para: DraftParagraph;
  marker: string | null; // "3." for factual paragraphs, "A." for grounds
  onJump: (cite: Citation) => void;
}) {
  if (para.kind === "heading") {
    return (
      <p className="pt-1 text-center text-xs font-bold uppercase tracking-widest text-ink">
        {para.text}
      </p>
    );
  }
  const factual = para.kind === "factual" || para.kind === "ground";
  return (
    <div className="flex gap-3">
      <span className="w-6 shrink-0 text-right font-mono text-xs leading-relaxed text-ink-muted">
        {marker ?? ""}
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

/** Numbered factual paragraphs, lettered grounds, plain boilerplate. */
function ProseSection({
  paragraphs,
  numbered,
  onJump,
}: {
  paragraphs: DraftParagraph[];
  numbered: boolean;
  onJump: (cite: Citation) => void;
}) {
  let n = 0;
  let g = 0;
  return (
    <div className="space-y-3">
      {paragraphs.map((para, i) => (
        <Paragraph
          key={i}
          para={para}
          marker={
            para.kind === "ground"
              ? `${String.fromCharCode(65 + g++ % 26)}.`
              : para.kind === "factual" && numbered
                ? `${++n}.`
                : null
          }
          onJump={onJump}
        />
      ))}
    </div>
  );
}

/** The List of Dates: derived from the verified chronology, so every row is
 *  cited and an undated event says so instead of guessing. */
function ListOfDates({
  entries,
  onJump,
}: {
  entries: ListOfDatesEntry[];
  onJump: (cite: Citation) => void;
}) {
  const shown = (iso: string | null) => {
    if (!iso) return "Undated";
    const [y, m, d] = iso.split("-");
    return `${d}.${m}.${y}`; // the record's own convention, day first
  };
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-rule text-left text-[11px] uppercase tracking-widest text-ink-muted">
          <th className="w-24 py-1.5 pr-3 font-semibold">Date</th>
          <th className="py-1.5 font-semibold">Event</th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e, i) => (
          <tr key={i} className="border-b border-rule-soft align-top">
            <td className="py-2 pr-3 font-mono text-xs text-ink-soft">
              {shown(e.event_date)}
            </td>
            <td className="py-2">
              <p className="leading-relaxed text-ink">{e.event}</p>
              <div className="mt-1 flex flex-wrap items-center gap-1">
                {e.cites.map((c, j) => (
                  <CitationChip
                    key={`${c.file}-${c.page}-${c.para}-${j}`}
                    cite={c}
                    onJump={onJump}
                  />
                ))}
                {e.confidence === "low_ocr" && (
                  <span className="rounded-sm bg-oxblood-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-oxblood">
                    low OCR
                  </span>
                )}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DraftView({
  matterId,
  draftId,
  draft,
  provider,
  onJump,
}: {
  matterId: string;
  draftId: string;
  draft: DraftDocument;
  provider: JobProvider | null;
  onJump: (cite: Citation) => void;
}) {
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
      {provider && (
        <p className="mt-1.5 text-center">
          <ProviderChip provider={provider} />
        </p>
      )}

      {/* Paperbook front matter first, in filing order. */}
      {(draft.synopsis?.length ?? 0) > 0 && (
        <div className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-muted">
            Synopsis
          </p>
          <div className="mt-2">
            <ProseSection
              paragraphs={draft.synopsis}
              numbered={false}
              onJump={onJump}
            />
          </div>
        </div>
      )}
      {(draft.list_of_dates?.length ?? 0) > 0 && (
        <div className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-muted">
            List of dates & events
          </p>
          <div className="mt-2">
            <ListOfDates entries={draft.list_of_dates} onJump={onJump} />
          </div>
        </div>
      )}

      <div className="mt-4">
        <ProseSection paragraphs={draft.paragraphs} numbered onJump={onJump} />
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

  const [draftJobId, setDraftJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const [openId, setOpenId] = useState<string | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<Record<string, DraftDocument>>({});
  /** Which model wrote each draft, by draft id. Kept per draft rather than as
   *  one "last provider": the list mixes runs, and a document has to be
   *  attributable to the model that actually wrote it, not the latest one. */
  const [providers, setProviders] = useState<Record<string, JobProvider>>({});

  const [error, setError] = useState<string | null>(null);

  const { job: draftJob, isLive: draftLive } = useJob(draftJobId);
  const elapsed = useJobElapsed(draftJob, draftLive);
  const generating = starting || draftLive;

  // What this session's run flagged, straight off the job record. Starting a
  // new one clears it, which is right: the count belonged to the old document.
  const finished =
    draftJob?.kind === "draft" && draftJob.status === "succeeded"
      ? draftJob
      : null;
  const violations = finished?.result?.violations ?? [];

  // While a resumed job runs, the label has to come from the job's own params
  // — the dropdown in this tab may never have been touched.
  const runningType =
    (draftJob?.kind === "draft" ? draftJob.params.doc_type : null) ?? docType;

  const draftFailure =
    draftJob?.status === "failed" ? draftJob.error : null;

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

  // Drafting outlives the tab that asked for it: adopt anything still running,
  // and attribute the drafts already on the list to the model that wrote each.
  useEffect(() => {
    let cancelled = false;
    listJobs(matterId, "draft")
      .then((jobs) => {
        if (cancelled) return;
        setProviders(providersByDraft(jobs));
        const live = jobs.find(isJobLive);
        if (live) setDraftJobId(live.job_id); // polling resumes from here

        // A draft that failed while the tab was closed is still news — see the
        // same reasoning in the matter page. Newest job only, so a later
        // success clears it.
        const newest = jobs[0];
        if (newest && newest.status === "failed" && newest.error) {
          setError(newest.error);
        }
      })
      .catch(() => {
        // The panel still works without the job history; drafting will report
        // any real problem when it is asked for one.
      });
    return () => {
      cancelled = true;
    };
  }, [matterId]);

  // The draft landed. Put it on the list and open it to the page.
  useEffect(() => {
    if (draftJob?.kind !== "draft" || draftJob.status !== "succeeded") return;
    const draftId = draftJob.result?.draft_id;
    if (!draftId) return;

    let cancelled = false;
    Promise.all([
      listDrafts(matterId),
      getDraft(matterId, draftId),
      listJobs(matterId, "draft"),
    ])
      .then(([list, draft, jobs]) => {
        if (cancelled) return;
        setDrafts(list);
        setLoaded((prev) => ({ ...prev, [draftId]: draft }));
        setProviders(providersByDraft(jobs));
        setOpenId(draftId);
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err instanceof Error ? err.message : "Failed to load the draft.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [draftJob, matterId]);

  const draftDocument = async () => {
    if (generating) return;
    setStarting(true);
    setError(null);
    try {
      const { job_id } = await createDraft(
        matterId,
        docType,
        instructions.trim(),
      );
      setDraftJobId(job_id);
      // The job owns the instructions now — free the box for the next one.
      setInstructions("");
    } catch (err) {
      // 409: this matter already has a draft being written, here or in another
      // tab. The user wants that document, not an error — follow it instead.
      if (err instanceof ApiError && err.status === 409) {
        const live = await listJobs(matterId, "draft")
          .then((jobs) => jobs.find(isJobLive))
          .catch(() => undefined);
        if (live) {
          setDraftJobId(live.job_id);
          return;
        }
        // It finished between the refusal and the lookup — show it rather than
        // repeating the server's "already running".
        setError("A draft just finished for this matter — it is in the list below.");
        void listDrafts(matterId)
          .then(setDrafts)
          .catch(() => {});
        return;
      }
      setError(err instanceof Error ? err.message : "Drafting failed.");
    } finally {
      setStarting(false);
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
                Drafting {DOC_TYPE_LABELS[runningType].toLowerCase()}…
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
              disabled={generating}
              className="shrink-0 rounded-sm bg-accent px-4 py-2 text-sm font-semibold text-panel transition-colors hover:bg-accent-deep disabled:cursor-not-allowed disabled:opacity-50"
            >
              Draft
            </button>
          </form>
        )}

        {!generating && (finished?.result?.revised ?? 0) > 0 && (
          <span className="mt-2 mr-2 inline-block rounded-sm border border-rule bg-panel-2 px-2 py-1 text-[11px] font-medium text-ink-muted">
            verified after {finished?.result?.revised}{" "}
            {finished?.result?.revised === 1 ? "revision" : "revisions"}
          </span>
        )}

        {violations.length > 0 && !generating && (
          <span
            title={violations
              .map((v) => `${v.kind} — ¶${v.paragraph}`)
              .join("\n")}
            className="mt-2 inline-block rounded-sm border border-verify/30 bg-verify-wash px-2 py-1 text-[11px] font-medium text-verify"
          >
            {violations.length}{" "}
            {violations.length === 1 ? "item" : "items"} flagged against the record
          </span>
        )}

        {/* A failed job's message is written for the person reading it — the
            record too long for the local model, Ollama not running, no credits
            left. It is shown word for word. */}
        {(draftFailure ?? error) && (
          <p className="mt-2 whitespace-pre-line rounded-sm border border-oxblood/30 bg-oxblood-wash px-3 py-2 text-xs leading-relaxed text-oxblood">
            {draftFailure ?? error}
          </p>
        )}
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
                    provider={providers[d.draft_id] ?? null}
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
