"use client";

import { useState } from "react";
import type { Citation, MatterArtifacts } from "@/lib/types";
import CitationChip from "./CitationChip";

type TabKey =
  | "chronology"
  | "proceedings"
  | "contentions"
  | "issues"
  | "documents"
  | "conflicts";

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function formatDate(iso: string | null): string {
  if (!iso) return "undated";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? m[2]} ${m[1]}`;
}

function LowOcrBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-verify/30 bg-verify-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-verify">
      verify — low-confidence OCR
    </span>
  );
}

function Chips({
  cites,
  onJump,
}: {
  cites: Citation[];
  onJump: (c: Citation) => void;
}) {
  if (cites.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {cites.map((c, i) => (
        <CitationChip key={`${c.file}-${c.page}-${c.para}-${i}`} cite={c} onJump={onJump} />
      ))}
    </span>
  );
}

function Row({
  cites,
  onJump,
  children,
}: {
  cites: Citation[];
  onJump: (c: Citation) => void;
  children: React.ReactNode;
}) {
  const clickable = cites.length > 0;
  return (
    <div
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? () => onJump(cites[0]) : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onJump(cites[0]);
              }
            }
          : undefined
      }
      className={`border-b border-rule-soft px-4 py-3 transition-colors ${
        clickable
          ? "cursor-pointer hover:bg-accent-wash/50 focus:bg-accent-wash/50 focus:outline-none"
          : ""
      }`}
    >
      {children}
    </div>
  );
}

function Empty({ label }: { label: string }) {
  return (
    <p className="px-4 py-10 text-center text-sm text-ink-muted">{label}</p>
  );
}

function SideLabel({
  side,
  tone,
}: {
  side: string;
  tone: "accent" | "oxblood";
}) {
  return (
    <span
      className={`text-[10px] font-semibold uppercase tracking-widest ${
        tone === "accent" ? "text-accent" : "text-oxblood"
      }`}
    >
      {side}
    </span>
  );
}

export default function ArtifactTabs({
  artifacts,
  fileNames,
  onJump,
}: {
  artifacts: MatterArtifacts;
  fileNames: string[];
  onJump: (cite: Citation) => void;
}) {
  const [tab, setTab] = useState<TabKey>("chronology");

  const tabs: { key: TabKey; label: string; count: number }[] = [
    { key: "chronology", label: "Chronology", count: artifacts.chronology.length },
    { key: "proceedings", label: "Proceedings", count: artifacts.proceedings.length },
    { key: "contentions", label: "Contentions", count: artifacts.contentions.length },
    { key: "issues", label: "Issues", count: artifacts.issues.length },
    { key: "documents", label: "Documents", count: artifacts.doc_index.length },
    {
      key: "conflicts",
      label: "Conflicts",
      count: artifacts.conflicts.length + artifacts.not_found.length,
    },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-rule bg-panel px-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`whitespace-nowrap border-b-2 px-3 py-2.5 text-xs font-semibold uppercase tracking-wide transition-colors ${
              tab === t.key
                ? "border-accent text-accent-deep"
                : "border-transparent text-ink-muted hover:text-ink-soft"
            }`}
          >
            {t.label}
            <span className="ml-1.5 font-mono text-[10px] font-normal text-ink-muted">
              {t.count}
            </span>
          </button>
        ))}
      </div>

      <div className="pane-scroll min-h-0 flex-1 overflow-y-auto bg-panel">
        {tab === "chronology" &&
          (artifacts.chronology.length === 0 ? (
            <Empty label="No chronology entries were extracted from the record." />
          ) : (
            artifacts.chronology.map((e, i) => (
              <Row key={i} cites={e.cites} onJump={onJump}>
                <div className="flex items-baseline gap-3">
                  <span className="w-24 shrink-0 font-mono text-xs text-ink-soft">
                    {formatDate(e.event_date)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm leading-snug text-ink">{e.event}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                      {e.actor && (
                        <span className="text-xs italic text-ink-muted">
                          {e.actor}
                        </span>
                      )}
                      <Chips cites={e.cites} onJump={onJump} />
                      {e.confidence === "low_ocr" && <LowOcrBadge />}
                    </div>
                  </div>
                </div>
              </Row>
            ))
          ))}

        {tab === "proceedings" &&
          (artifacts.proceedings.length === 0 ? (
            <Empty label="No orders or proceedings were extracted." />
          ) : (
            artifacts.proceedings.map((p, i) => (
              <Row key={i} cites={p.cites} onJump={onJump}>
                <div className="flex items-baseline gap-3">
                  <span className="w-24 shrink-0 font-mono text-xs text-ink-soft">
                    {formatDate(p.order_date)}
                  </span>
                  <div className="min-w-0 flex-1">
                    {p.court && (
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-muted">
                        {p.court}
                      </p>
                    )}
                    <p className="mt-0.5 text-sm leading-snug text-ink">
                      {p.direction}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                      {p.next_date && (
                        <span className="rounded-sm bg-panel-2 px-1.5 py-0.5 font-mono text-[11px] text-ink-soft">
                          next: {formatDate(p.next_date)}
                        </span>
                      )}
                      <Chips cites={p.cites} onJump={onJump} />
                    </div>
                  </div>
                </div>
              </Row>
            ))
          ))}

        {tab === "contentions" &&
          (artifacts.contentions.length === 0 ? (
            <Empty label="No rival contentions were extracted." />
          ) : (
            artifacts.contentions.map((c, i) => (
              <div key={i} className="border-b border-rule-soft px-4 py-3">
                <p className="font-display text-sm font-medium text-ink">
                  {c.issue}
                </p>
                <div className="mt-2 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-sm border border-rule-soft bg-paper/60 p-2.5">
                    <SideLabel side="Petitioner" tone="accent" />
                    {c.petitioner ? (
                      <>
                        <p className="mt-1 text-sm leading-snug text-ink-soft">
                          {c.petitioner.position}
                        </p>
                        <div className="mt-1.5">
                          <Chips cites={c.petitioner.cites} onJump={onJump} />
                        </div>
                      </>
                    ) : (
                      <p className="mt-1 text-xs italic text-ink-muted">
                        No position on record.
                      </p>
                    )}
                  </div>
                  <div className="rounded-sm border border-rule-soft bg-paper/60 p-2.5">
                    <SideLabel side="Respondent" tone="oxblood" />
                    {c.respondent ? (
                      <>
                        <p className="mt-1 text-sm leading-snug text-ink-soft">
                          {c.respondent.position}
                        </p>
                        <div className="mt-1.5">
                          <Chips cites={c.respondent.cites} onJump={onJump} />
                        </div>
                      </>
                    ) : (
                      <p className="mt-1 text-xs italic text-ink-muted">
                        No position on record.
                      </p>
                    )}
                  </div>
                </div>
              </div>
            ))
          ))}

        {tab === "issues" &&
          (artifacts.issues.length === 0 ? (
            <Empty label="No issues were framed or inferred." />
          ) : (
            artifacts.issues.map((issue, i) => (
              <Row key={i} cites={issue.cites} onJump={onJump}>
                <div className="flex items-baseline gap-3">
                  <span className="w-6 shrink-0 font-mono text-xs text-ink-muted">
                    {i + 1}.
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm leading-snug text-ink">{issue.text}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span
                        className={`rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                          issue.origin === "framed_by_court"
                            ? "bg-accent-wash text-accent-deep"
                            : "bg-panel-2 text-ink-muted"
                        }`}
                      >
                        {issue.origin === "framed_by_court"
                          ? "framed by court"
                          : "inferred"}
                      </span>
                      <Chips cites={issue.cites} onJump={onJump} />
                    </div>
                  </div>
                </div>
              </Row>
            ))
          ))}

        {tab === "documents" &&
          (artifacts.doc_index.length === 0 ? (
            <Empty label="No documents indexed." />
          ) : (
            artifacts.doc_index.map((d, i) => {
              const openable = fileNames.includes(d.title);
              const cites: Citation[] = openable
                ? [{ file: d.title, page: 1, para: null }]
                : [];
              return (
                <Row key={i} cites={cites} onJump={onJump}>
                  <div className="flex items-baseline gap-3">
                    <span className="w-14 shrink-0 font-mono text-xs text-ink-soft">
                      {d.exhibit_no ?? "—"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium leading-snug text-ink">
                        {d.title}
                      </p>
                      <p className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-ink-muted">
                        <span>{d.doc_type}</span>
                        <span>{formatDate(d.doc_date)}</span>
                        <span>{d.pages} pp.</span>
                        <span>{d.language}</span>
                        <span
                          className={
                            d.ocr_quality === "low" ? "text-verify" : undefined
                          }
                        >
                          OCR: {d.ocr_quality}
                        </span>
                      </p>
                    </div>
                  </div>
                </Row>
              );
            })
          ))}

        {tab === "conflicts" && (
          <>
            {artifacts.conflicts.length === 0 &&
              artifacts.not_found.length === 0 && (
                <Empty label="No conflicts detected; nothing marked as missing from the record." />
              )}
            {artifacts.conflicts.map((c, i) => (
              <div key={i} className="border-b border-rule-soft px-4 py-3">
                <p className="text-sm font-medium leading-snug text-ink">
                  <span className="mr-2 rounded-sm bg-oxblood-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-oxblood">
                    conflict
                  </span>
                  {c.fact}
                </p>
                <ul className="mt-2 space-y-2 border-l-2 border-rule pl-3">
                  {c.positions.map((p, j) => (
                    <li key={j}>
                      <p className="text-sm leading-snug text-ink-soft">
                        {p.position}
                      </p>
                      <div className="mt-1">
                        <Chips cites={p.cites} onJump={onJump} />
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            {artifacts.not_found.length > 0 && (
              <div className="px-4 py-3">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-muted">
                  Not found in the record
                </p>
                <ul className="mt-2 space-y-1.5">
                  {artifacts.not_found.map((item, i) => (
                    <li
                      key={i}
                      className="text-sm italic leading-snug text-ink-muted"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
