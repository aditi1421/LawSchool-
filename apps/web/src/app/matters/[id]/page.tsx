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
  uploadFile,
} from "@/lib/api";
import type {
  Citation,
  DocumentRecord,
  MatterArtifacts,
  MatterManifest,
  Violation,
} from "@/lib/types";
import type { ViewerTarget } from "@/components/PdfViewer";
import ArtifactTabs from "@/components/ArtifactTabs";
import DraftPanel from "@/components/DraftPanel";
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

export default function MatterWorkspace({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();

  const [manifest, setManifest] = useState<MatterManifest | null>(null);
  const [artifacts, setArtifacts] = useState<MatterArtifacts | null>(null);
  const [violations, setViolations] = useState<Violation[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [viewerTarget, setViewerTarget] = useState<ViewerTarget | null>(null);
  const nonceRef = useRef(0);

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

  useEffect(() => {
    if (!generating) return;
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [generating]);

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

  const generate = async () => {
    if (generating) return;
    setElapsed(0);
    setGenerating(true);
    setNotice(null);
    try {
      const res = await generateArtifacts(id);
      setArtifacts(res.artifacts);
      setViolations(res.violations);
    } catch (err) {
      setNotice(
        err instanceof Error ? err.message : "Brief generation failed.",
      );
    } finally {
      setGenerating(false);
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
                  <li key={doc.file}>
                    <button
                      type="button"
                      onClick={() =>
                        jumpTo({ file: doc.file, page: 1, para: null })
                      }
                      className="flex w-full items-center gap-2 rounded-sm border border-rule-soft bg-paper px-2.5 py-1.5 text-left transition-colors hover:border-accent hover:bg-accent-wash/60"
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
                      {doc.needs_ocr_pages.length > 0 && (
                        <span
                          title={`Pages needing OCR: ${doc.needs_ocr_pages.join(", ")}`}
                          className="shrink-0 rounded-sm border border-verify/30 bg-verify-wash px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-verify"
                        >
                          needs OCR · {doc.needs_ocr_pages.length}
                        </span>
                      )}
                    </button>
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
                  disabled={documents.length === 0}
                  title={
                    documents.length === 0
                      ? "Upload case-file PDFs first"
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
              </div>
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
