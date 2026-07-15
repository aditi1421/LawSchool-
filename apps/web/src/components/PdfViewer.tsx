"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

export interface ViewerTarget {
  url: string;
  fileName: string;
  page: number;
  /** Bump to re-trigger a jump to the same page. */
  nonce: number;
  /** Exact text of the cited paragraph — highlighted on the target page. */
  highlightText?: string | null;
}

const normalize = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();

const escapeHtml = (s: string) =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const PAGE_GAP = 16;

export default function PdfViewer({ target }: { target: ViewerTarget | null }) {
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pageWidth, setPageWidth] = useState<number>(0);

  const scrollRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingJump = useRef<{ page: number; nonce: number } | null>(null);

  // Measure the pane so pages fill the available width.
  // ResizeObserver fires once on observe, so no initial measure is needed.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() =>
      setPageWidth(Math.max(240, el.clientWidth - 48)),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const jumpToPage = useCallback((page: number) => {
    const el = pageRefs.current.get(page);
    if (!el) return false;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    el.classList.remove("cite-pulse");
    // Force a reflow so the animation restarts on repeated jumps.
    void el.offsetWidth;
    el.classList.add("cite-pulse");
    if (pulseTimer.current) clearTimeout(pulseTimer.current);
    pulseTimer.current = setTimeout(
      () => el.classList.remove("cite-pulse"),
      1500,
    );
    return true;
  }, []);

  // Reset per-document state as soon as the target file changes (during
  // render, per the React "derived state" pattern — avoids an effect).
  const [renderedUrl, setRenderedUrl] = useState<string | null>(null);
  if ((target?.url ?? null) !== renderedUrl) {
    setRenderedUrl(target?.url ?? null);
    setLoadError(null);
    setNumPages(0);
    setCurrentPage(1);
  }

  // React to a new jump target (pure DOM work — scroll + pulse).
  useEffect(() => {
    if (!target) return;
    pendingJump.current = { page: target.page, nonce: target.nonce };
    // If the document (same file) is already rendered, jump immediately.
    if (numPages > 0 && jumpToPage(Math.min(target.page, numPages))) {
      pendingJump.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.url, target?.page, target?.nonce]);

  const onDocLoad = useCallback(({ numPages: n }: { numPages: number }) => {
    setNumPages(n);
    setCurrentPage(1);
  }, []);

  // Once pages exist in the DOM, complete any pending jump.
  const onPageRendered = useCallback(
    (page: number) => {
      const pending = pendingJump.current;
      if (pending && page === Math.min(pending.page, numPages || pending.page)) {
        if (jumpToPage(page)) pendingJump.current = null;
      }
    },
    [jumpToPage, numPages],
  );

  // Track which page is closest to the top of the scroll pane.
  const onScroll = useCallback(() => {
    const container = scrollRef.current;
    if (!container) return;
    const top = container.getBoundingClientRect().top;
    let best = 1;
    let bestDist = Infinity;
    pageRefs.current.forEach((el, page) => {
      const dist = Math.abs(el.getBoundingClientRect().top - top - PAGE_GAP);
      if (dist < bestDist) {
        bestDist = dist;
        best = page;
      }
    });
    setCurrentPage(best);
  }, []);

  const file = useMemo(() => target?.url ?? null, [target?.url]);

  // Highlight text-layer spans belonging to the cited paragraph: a span is
  // marked when its text appears in the paragraph (PDF text items are line
  // fragments, so substring containment is reliable on text-layer PDFs).
  const highlightText = target?.highlightText ?? null;
  const highlightNormalized = useMemo(
    () => (highlightText ? normalize(highlightText) : null),
    [highlightText],
  );
  const makeTextRenderer = useCallback(
    (page: number) => {
      if (!highlightNormalized || !target || page !== target.page) {
        return undefined;
      }
      return ({ str }: { str: string }) => {
        // Chunking strips the leading para number ("2. "), so strip it from
        // the PDF line fragment too before matching.
        const frag = normalize(str).replace(/^\(?\d{1,3}[.)]\s*/, "");
        // Long-enough fragments only: avoids false hits like "PLAINT" (a
        // heading) matching inside "plaintiff" in the paragraph text.
        if (
          frag.length >= 12 &&
          frag.split(" ").length >= 3 &&
          highlightNormalized.includes(frag)
        ) {
          return `<mark class="cite-mark">${escapeHtml(str)}</mark>`;
        }
        return escapeHtml(str);
      };
    },
    [highlightNormalized, target],
  );

  if (!target) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-panel-2 px-8 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-full border border-rule bg-panel text-2xl text-ink-muted">
          §
        </div>
        <p className="font-display text-lg text-ink-soft">The record</p>
        <p className="max-w-xs text-sm leading-relaxed text-ink-muted">
          Select a brief entry or citation on the left to open the source PDF
          at the cited page.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-panel-2">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-rule bg-panel px-4 py-2">
        <span
          className="truncate font-mono text-xs text-ink-soft"
          title={target.fileName}
        >
          {target.fileName}
        </span>
        <span className="shrink-0 rounded-sm border border-rule bg-panel-2 px-2 py-0.5 font-mono text-[11px] text-ink-muted">
          p. {currentPage}
          {numPages > 0 ? ` / ${numPages}` : ""}
        </span>
      </div>

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="pane-scroll min-h-0 flex-1 overflow-y-auto px-6 py-4"
      >
        {loadError ? (
          <div className="mx-auto mt-16 max-w-sm rounded-sm border border-oxblood/30 bg-oxblood-wash p-4 text-sm text-oxblood">
            Could not load {target.fileName}: {loadError}
          </div>
        ) : (
          <Document
            key={file ?? undefined}
            file={file}
            onLoadSuccess={onDocLoad}
            onLoadError={(err) => setLoadError(err.message)}
            loading={
              <div className="mt-16 text-center text-sm text-ink-muted">
                Opening {target.fileName}…
              </div>
            }
            error={
              <div className="mt-16 text-center text-sm text-oxblood">
                Failed to render PDF.
              </div>
            }
          >
            {Array.from({ length: numPages }, (_, i) => i + 1).map((page) => (
              <div
                key={page}
                ref={(el) => {
                  if (el) pageRefs.current.set(page, el);
                  else pageRefs.current.delete(page);
                }}
                data-page={page}
                className="mx-auto mb-4 w-fit scroll-mt-4 rounded-[2px] bg-white shadow-card"
              >
                <Page
                  pageNumber={page}
                  width={pageWidth || undefined}
                  customTextRenderer={makeTextRenderer(page)}
                  onRenderSuccess={() => onPageRendered(page)}
                  loading={
                    <div
                      style={{
                        width: pageWidth || 480,
                        height: (pageWidth || 480) * 1.414,
                      }}
                      className="flex items-center justify-center text-xs text-ink-muted"
                    >
                      p. {page}
                    </div>
                  }
                />
                <div className="border-t border-rule-soft px-2 py-1 text-right font-mono text-[10px] text-ink-muted">
                  {target.fileName} · p. {page}
                </div>
              </div>
            ))}
          </Document>
        )}
      </div>
    </div>
  );
}
