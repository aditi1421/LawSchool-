"use client";

import { useRef, useState } from "react";

export default function UploadZone({
  onFiles,
  busy,
}: {
  onFiles: (files: File[]) => void;
  busy: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const accept = (list: FileList | null) => {
    if (!list) return;
    const pdfs = Array.from(list).filter(
      (f) =>
        f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"),
    );
    if (pdfs.length > 0) onFiles(pdfs);
  };

  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        accept(e.dataTransfer.files);
      }}
      className={`flex w-full flex-col items-center gap-1 rounded-sm border border-dashed px-4 py-5 text-center transition-colors ${
        dragOver
          ? "border-accent bg-accent-wash"
          : "border-rule bg-panel hover:border-ink-muted"
      } ${busy ? "cursor-wait opacity-60" : "cursor-pointer"}`}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        className="hidden"
        onChange={(e) => {
          accept(e.target.files);
          e.target.value = "";
        }}
      />
      <span className="text-sm font-medium text-ink-soft">
        {busy ? "Reading the record…" : "Add case-file PDFs"}
      </span>
      <span className="text-xs text-ink-muted">
        Drop files here or click to browse · PDF only
      </span>
    </button>
  );
}
