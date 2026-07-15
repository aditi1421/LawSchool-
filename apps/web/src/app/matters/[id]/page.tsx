"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import {
  ApiError,
  deleteMatter,
  exportUrl,
  fileUrl,
  getChunkText,
  generateArtifacts,
  getArtifacts,
  getMatter,
  listJobs,
  startOcr,
  uploadFile,
} from "@/lib/api";
import { isJobLive } from "@/lib/types";
import type {
  ArtifactsJobRecord,
  Citation,
  DocumentRecord,
  MatterArtifacts,
  MatterManifest,
} from "@/lib/types";
import { useJob, useJobElapsed } from "@/lib/useJob";
import type { ViewerTarget } from "@/components/PdfViewer";
import ArtifactTabs from "@/components/ArtifactTabs";
import DraftPanel from "@/components/DraftPanel";
import ProviderChip from "@/components/ProviderChip";
import QueryBox from "@/components/QueryBox";
import UploadZone from "@/components/UploadZone";

const PdfViewer = dynamic(() => import("@/components/PdfViewer"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center bg-panel-2 text-sm text-ink-muted">
      Preparing viewer…
    </div>
  ),
});

/** Where a scanned document stands in the background OCR queue. A born-digital
 *  document ("not_needed") says nothing at all — silence is the good case. */
function OcrChip({ doc }: { doc: DocumentRecord }) {
  const pages = doc.needs_ocr_pages.join(", ");

  switch (doc.ocr_status) {
    case "pending":
      return (
        <span
          title={pages ? `Scanned pages queued for OCR: ${pages}` : undefined}
          className="shrink-0 rounded-sm border border-verify/30 bg-verify-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-verify"
        >
          OCR queued
        </span>
      );
    case "running":
      return (
        <span
          title={pages ? `Reading scanned pages: ${pages}` : undefined}
          className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-verify/30 bg-verify-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-verify"
        >
          <span
            className="ocr-pulse h-1 w-1 shrink-0 rounded-full bg-verify"
            aria-hidden
          />
          Reading scan…
        </span>
      );
    case "failed":
      return (
        <span
          title={doc.ocr_error ?? "OCR failed on the scanned pages."}
          className="shrink-0 rounded-sm border border-oxblood/30 bg-oxblood-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-oxblood"
        >
          OCR failed
        </span>
      );
    case "done":
      return (
        <span
          title={pages ? `Scanned pages read by OCR: ${pages}` : undefined}
          className="shrink-0 rounded-sm border border-accent/25 bg-accent-wash px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent"
        >
          OCR done
        </span>
      );
    default:
      return null;
  }
}

export default function MatterWorkspace({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();

  const [manifest, setManifest] = useState<MatterManifest | null>(null);
  const [artifacts, setArtifacts] = useState<MatterArtifacts | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);

  // Brief generation runs as a server-side job. This holds only its id — the
  // record itself lives on the server, which is what lets the work survive
  // this tab being closed.
  const [briefJobId, setBriefJobId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [briefError, setBriefError] = useState<string | null>(null);
  /** The newest finished run found on arrival — what the brief already on
   *  screen came out of, before this session started anything of its own. */
  const [priorRun, setPriorRun] = useState<ArtifactsJobRecord | null>(null);

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [retryingOcr, setRetryingOcr] = useState<string[]>([]);

  const [viewerTarget, setViewerTarget] = useState<ViewerTarget | null>(null);
  const nonceRef = useRef(0);

  const { job: briefJob, isLive: briefLive } = useJob(briefJobId);
  const elapsed = useJobElapsed(briefJob, briefLive);
  // `starting` covers the gap between the click and the 202 coming back, so a
  // second click cannot fire a POST the server would only reject with a 409.
  const generating = starting || briefLive;

  // The run that produced the brief on screen: this session's, once it lands,
  // and otherwise whatever the server says came last. Derived rather than
  // copied into state — there is one answer and the job record holds it.
  const briefRun: ArtifactsJobRecord | null =
    briefJob?.kind === "artifacts" && briefJob.status === "succeeded"
      ? briefJob
      : priorRun;
  const violations = briefRun?.result?.violations ?? [];

  const ocrActive = (manifest?.documents ?? []).some(
    (d) => d.ocr_status === "pending" || d.ocr_status === "running",
  );

  useEffect(() => {
    let cancelled = false;
    getMatter(id)
      .then((m) => {
        if (!cancelled) setManifest(m);
      })
      .catch((err) => {
        if (!cancelled)
          setLoadError(
            err instanceof Error ? err.message : "Failed to load matter.",
          );
      });
    getArtifacts(id)
      .then((a) => {
        if (!cancelled) {
          setArtifacts(a);
        }
      })
      .catch((err) => {
        // 404 simply means the brief has not been generated yet.
        if (!cancelled && !(err instanceof ApiError && err.status === 404)) {
          setNotice(
            err instanceof Error ? err.message : "Failed to load artifacts.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Pick up where the server is. Generation outlives the tab that started it,
  // so on arrival the question is never "has this page generated a brief" but
  // "is this matter's brief being generated right now" — and the answer may
  // have been set in motion by a session that is long gone.
  useEffect(() => {
    let cancelled = false;
    listJobs(id, "artifacts")
      .then((jobs) => {
        if (cancelled) return;
        const live = jobs.find(isJobLive);
        if (live) setBriefJobId(live.job_id); // polling resumes from here

        // A run that failed while nobody was watching still has to be
        // reported. Silence here is the failure mode jobs were meant to fix:
        // the tab that would have shown "Ollama is not running" is closed, and
        // without this the user learns nothing, presses Generate, and waits out
        // the same timeout to be told what the server already knew. Only the
        // newest job — a success after it makes it history, and it clears.
        const newest = jobs[0];
        if (newest && newest.status === "failed" && newest.error) {
          setBriefError(newest.error);
        }

        // Independently of anything running: the brief already on screen came
        // out of the last run that succeeded. Recorded even while a new run is
        // in flight, so that if that one fails the brief it did not replace is
        // still attributed to the model that actually wrote it.
        const done = jobs.find((j) => j.status === "succeeded");
        if (done?.kind === "artifacts") setPriorRun(done);
      })
      .catch(() => {
        // Not knowing about a job is not worth a banner on arrival — the page
        // still works, and pressing Generate will surface any real problem.
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // The job landed. Read the brief it wrote.
  useEffect(() => {
    if (briefJob?.kind !== "artifacts" || briefJob.status !== "succeeded") return;
    let cancelled = false;
    getArtifacts(id)
      .then((a) => {
        if (!cancelled) setArtifacts(a);
      })
      .catch((err) => {
        if (!cancelled)
          setBriefError(
            err instanceof Error ? err.message : "Failed to load the brief.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [briefJob, id]);

  // OCR runs on the server with no push channel back, so poll the manifest
  // while any scan is still being read and stop once they all land. Pausing
  // during an upload keeps an in-flight poll from clobbering the fresh record
  // with a snapshot taken before it existed.
  useEffect(() => {
    if (!ocrActive || uploading) return;
    let cancelled = false;
    const poll = () => {
      if (document.visibilityState === "hidden") return;
      getMatter(id)
        .then((m) => {
          if (!cancelled)
            setManifest((prev) =>
              prev ? { ...prev, documents: m.documents } : m,
            );
        })
        .catch(() => {
          // A dropped poll is not worth a banner — the next tick retries.
        });
    };
    const t = setInterval(poll, 3000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      cancelled = true;
      clearInterval(t);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [id, ocrActive, uploading]);

  const jumpTo = useCallback(
    (cite: Citation) => {
      nonceRef.current += 1;
      const nonce = nonceRef.current;
      setViewerTarget({
        url: fileUrl(id, cite.file),
        fileName: cite.file,
        page: cite.page,
        nonce,
        highlightText: null,
      });
      // Fetch the cited paragraph's exact text and highlight it in the PDF.
      getChunkText(id, cite.file, cite.page, cite.para ?? null)
        .then(({ text }) => {
          if (nonceRef.current !== nonce) return; // superseded by a newer jump
          setViewerTarget((prev) =>
            prev && prev.nonce === nonce ? { ...prev, highlightText: text } : prev,
          );
        })
        .catch(() => {}); // no text at the location — page jump still happened
    },
    [id],
  );

  const handleFiles = async (files: File[]) => {
    setUploading(true);
    setNotice(null);
    for (const file of files) {
      try {
        const record = await uploadFile(id, file);
        setManifest((prev) =>
          prev
            ? {
                ...prev,
                documents: [
                  ...prev.documents.filter((d) => d.file !== record.file),
                  record,
                ],
              }
            : prev,
        );
      } catch (err) {
        setNotice(
          `${file.name}: ${err instanceof Error ? err.message : "upload failed"}`,
        );
      }
    }
    setUploading(false);
  };

  const retryOcr = async (filename: string) => {
    if (retryingOcr.includes(filename)) return;
    setRetryingOcr((prev) => [...prev, filename]);
    setNotice(null);
    try {
      const { ocr_status } = await startOcr(id, filename);
      // Take the server's word for the new status; polling picks it up from here.
      setManifest((prev) =>
        prev
          ? {
              ...prev,
              documents: prev.documents.map((d) =>
                d.file === filename ? { ...d, ocr_status, ocr_error: null } : d,
              ),
            }
          : prev,
      );
    } catch (err) {
      setNotice(
        `${filename}: ${err instanceof Error ? err.message : "could not queue OCR"}`,
      );
    } finally {
      setRetryingOcr((prev) => prev.filter((f) => f !== filename));
    }
  };

  const generate = async () => {
    if (generating) return;
    setStarting(true);
    setBriefError(null);
    setNotice(null);
    try {
      const { job_id } = await generateArtifacts(id);
      setBriefJobId(job_id);
    } catch (err) {
      // 409 means the model is already working on this matter — from another
      // tab, or from this one before a reload. That is not a failure to report
      // to the user; the run they wanted is happening. Attach to it.
      if (err instanceof ApiError && err.status === 409) {
        const live = await listJobs(id, "artifacts")
          .then((jobs) => jobs.find(isJobLive))
          .catch(() => undefined);
        if (live) {
          setBriefJobId(live.job_id);
          return;
        }
        // It finished in the moment between the refusal and the lookup. Say so
        // plainly rather than showing the server's "already running".
        setBriefError("A brief was just generated for this matter. Reload to see it.");
        return;
      }
      setBriefError(
        err instanceof Error ? err.message : "Brief generation failed.",
      );
    } finally {
      setStarting(false);
    }
  };

  const removeMatter = async () => {
    setDeleting(true);
    try {
      await deleteMatter(id);
      router.push("/");
    } catch (err) {
      setNotice(
        err instanceof Error ? err.message : "Failed to delete matter.",
      );
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  const documents = manifest?.documents ?? [];
  const fileNames = documents.map((d) => d.file);

  // A failed job's message is written for the person reading it — an over-long
  // record, Ollama not running, no credits left. It is shown word for word.
  const briefFailure =
    briefError ?? (briefJob?.status === "failed" ? briefJob.error : null);

  if (loadError) {
    return (
      <main className="mx-auto w-full max-w-xl flex-1 px-6 py-16 text-center">
        <p className="rounded-sm border border-oxblood/30 bg-oxblood-wash px-4 py-3 text-sm text-oxblood">
          {loadError}
        </p>
        <Link
          href="/"
          className="mt-4 inline-block text-sm text-accent underline underline-offset-2"
        >
          Back to matters
        </Link>
      </main>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="flex shrink-0 items-center gap-4 border-b-2 border-ink bg-panel px-5 py-3">
        <Link
          href="/"
          className="shrink-0 font-display text-lg font-medium text-ink transition-colors hover:text-accent-deep"
          title="All matters"
        >
          lawschool
        </Link>
        <span className="text-rule" aria-hidden>
          /
        </span>
        <h1
          className="min-w-0 flex-1 truncate font-display text-lg font-medium text-ink"
          title={manifest?.title}
        >
          {manifest?.title ?? "Loading…"}
        </h1>
        {artifacts && (
          <a
            href={exportUrl(id)}
            className="shrink-0 rounded-sm border border-accent px-3 py-1.5 text-sm font-semibold text-accent transition-colors hover:bg-accent-wash"
          >
            Export .docx
          </a>
        )}
        {confirmDelete ? (
          <span className="flex shrink-0 items-center gap-2 rounded-sm border border-oxblood/30 bg-oxblood-wash px-2 py-1">
            <span className="text-xs text-oxblood">Delete permanently?</span>
            <button
              type="button"
              disabled={deleting}
              onClick={() => void removeMatter()}
              className="text-xs font-semibold text-oxblood underline underline-offset-2 disabled:opacity-50"
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmDelete(false)}
              className="text-xs text-ink-muted hover:text-ink"
            >
              Keep
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmDelete(true)}
            className="shrink-0 rounded-sm px-2 py-1.5 text-sm text-ink-muted transition-colors hover:text-oxblood"
          >
            Delete matter
          </button>
        )}
      </header>

      {notice && (
        <p className="shrink-0 border-b border-verify/30 bg-verify-wash px-5 py-2 text-xs text-verify">
          {notice}
        </p>
      )}

      {/* ── Split view ─────────────────────────────────────────── */}
      <div className="flex min-h-0 flex-1">
        {/* Left pane — the brief */}
        <section className="flex min-h-0 w-[46%] min-w-[380px] flex-col border-r border-rule">
          {/* The record */}
          <div className="shrink-0 border-b border-rule bg-panel px-4 pb-3 pt-3">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-muted">
              The record
            </p>
            <div className="mt-2">
              <UploadZone onFiles={(f) => void handleFiles(f)} busy={uploading} />
            </div>
            {documents.length > 0 && (
              <ul className="pane-scroll mt-2 max-h-36 space-y-1 overflow-y-auto">
                {documents.map((doc: DocumentRecord) => (
                  <li
                    key={doc.file}
                    className="flex items-center rounded-sm border border-rule-soft bg-paper transition-colors hover:border-accent hover:bg-accent-wash/60"
                  >
                    <button
                      type="button"
                      onClick={() =>
                        jumpTo({ file: doc.file, page: 1, para: null })
                      }
                      className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-1.5 text-left"
                    >
                      <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-soft">
                        {doc.file}
                      </span>
                      <span className="shrink-0 rounded-sm bg-panel-2 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-ink-muted">
                        {doc.doc_type}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] text-ink-muted">
                        {doc.pages.length} pp.
                      </span>
                      <OcrChip doc={doc} />
                    </button>
                    {doc.ocr_status === "failed" && (
                      <button
                        type="button"
                        disabled={retryingOcr.includes(doc.file)}
                        onClick={() => void retryOcr(doc.file)}
                        className="shrink-0 py-1.5 pr-2.5 pl-1 text-[10px] font-semibold text-oxblood underline underline-offset-2 disabled:opacity-50"
                      >
                        {retryingOcr.includes(doc.file) ? "Retrying…" : "Retry"}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Generate brief */}
          <div className="shrink-0 border-b border-rule bg-panel px-4 py-3">
            {generating ? (
              <div>
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-ink-soft">
                    Analyzing the record…
                  </p>
                  <span className="font-mono text-xs text-ink-muted">
                    {Math.floor(elapsed / 60)}:
                    {String(elapsed % 60).padStart(2, "0")}
                  </span>
                </div>
                <div className="analyzing-bar mt-2 h-1.5 rounded-full bg-rule-soft" />
                <p className="mt-2 text-xs text-ink-muted">
                  Extracting chronology, orders, contentions and issues — this
                  can take a few minutes on a large record.
                </p>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => void generate()}
                  disabled={documents.length === 0 || ocrActive || generating}
                  title={
                    documents.length === 0
                      ? "Upload case-file PDFs first"
                      : ocrActive
                        ? "Waiting for OCR to finish reading scanned pages"
                        : undefined
                  }
                  className="rounded-sm bg-accent px-4 py-1.5 text-sm font-semibold text-panel transition-colors hover:bg-accent-deep disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {artifacts ? "Regenerate brief" : "Generate brief"}
                </button>
                {violations.length > 0 && (
                  <span
                    title={violations
                      .map((v) => `${v.kind}: ${v.claim}`)
                      .join("\n")}
                    className="rounded-sm border border-verify/30 bg-verify-wash px-2 py-1 text-[11px] font-medium text-verify"
                  >
                    {violations.length} unverified{" "}
                    {violations.length === 1 ? "claim" : "claims"} flagged
                  </span>
                )}
                {artifacts && <ProviderChip provider={briefRun?.provider ?? null} />}
              </div>
            )}

            {briefFailure && (
              <p className="mt-2 whitespace-pre-line rounded-sm border border-oxblood/30 bg-oxblood-wash px-3 py-2 text-xs leading-relaxed text-oxblood">
                {briefFailure}
              </p>
            )}
          </div>

          {/* Artifacts */}
          {artifacts ? (
            <ArtifactTabs
              artifacts={artifacts}
              fileNames={fileNames}
              onJump={jumpTo}
            />
          ) : (
            <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 bg-panel px-8 text-center">
              <p className="font-display text-lg text-ink-soft">
                No brief yet
              </p>
              <p className="max-w-sm text-sm leading-relaxed text-ink-muted">
                {documents.length === 0
                  ? "Upload the case-file PDFs above, then generate a hearing-ready brief."
                  : "Generate the brief. Every extracted line will carry a citation into the record."}
              </p>
            </div>
          )}

          {/* Drafting */}
          <DraftPanel matterId={id} onJump={jumpTo} />

          {/* Ask the record */}
          <QueryBox matterId={id} onJump={jumpTo} />
        </section>

        {/* Right pane — the source */}
        <section className="min-h-0 min-w-0 flex-1">
          <PdfViewer target={viewerTarget} />
        </section>
      </div>
    </div>
  );
}
