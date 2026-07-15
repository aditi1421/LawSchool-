"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createMatter, deleteMatter, listMatters } from "@/lib/api";
import type { MatterManifest } from "@/lib/types";

function formatCreated(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function MatterListPage() {
  const router = useRouter();
  const [matters, setMatters] = useState<MatterManifest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [showNew, setShowNew] = useState(false);
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);

  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    listMatters()
      .then(setMatters)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load matters."),
      );
  }, []);

  const create = async () => {
    const t = title.trim();
    if (!t || creating) return;
    setCreating(true);
    setError(null);
    try {
      const matter = await createMatter(t);
      router.push(`/matters/${matter.matter_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create matter.");
      setCreating(false);
    }
  };

  const remove = async (id: string) => {
    setDeleting(id);
    setError(null);
    try {
      await deleteMatter(id);
      setMatters((prev) => prev?.filter((m) => m.matter_id !== id) ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete matter.");
    } finally {
      setDeleting(null);
      setConfirmDelete(null);
    }
  };

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-6 py-10">
      <header className="flex items-end justify-between gap-4 border-b-2 border-ink pb-5">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-accent">
            Case-file intelligence
          </p>
          <h1 className="mt-1 font-display text-4xl font-medium tracking-tight text-ink">
            lawschool
          </h1>
          <p className="mt-1.5 text-sm text-ink-muted">
            Hearing-ready briefs from the record — every line cited to the
            source PDF.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowNew((v) => !v)}
          className="shrink-0 rounded-sm bg-accent px-4 py-2 text-sm font-semibold text-panel transition-colors hover:bg-accent-deep"
        >
          New matter
        </button>
      </header>

      {showNew && (
        <form
          className="mt-6 flex items-center gap-2 rounded-sm border border-rule bg-panel p-3 shadow-card"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <input
            autoFocus
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Matter title — e.g. Sharma v. State of Maharashtra, RSA 412/2023"
            className="min-w-0 flex-1 rounded-sm border border-rule bg-paper px-3 py-2 text-sm text-ink placeholder:text-ink-muted/70 focus:border-accent focus:outline-none"
          />
          <button
            type="submit"
            disabled={creating || title.trim() === ""}
            className="rounded-sm bg-accent px-4 py-2 text-sm font-semibold text-panel transition-colors hover:bg-accent-deep disabled:cursor-not-allowed disabled:opacity-50"
          >
            {creating ? "Creating…" : "Create"}
          </button>
          <button
            type="button"
            onClick={() => {
              setShowNew(false);
              setTitle("");
            }}
            className="rounded-sm px-3 py-2 text-sm text-ink-muted hover:text-ink"
          >
            Cancel
          </button>
        </form>
      )}

      {error && (
        <p className="mt-6 rounded-sm border border-oxblood/30 bg-oxblood-wash px-4 py-3 text-sm text-oxblood">
          {error}
        </p>
      )}

      {matters === null && !error && (
        <p className="mt-10 text-center text-sm text-ink-muted">
          Loading matters…
        </p>
      )}

      {matters !== null && matters.length === 0 && (
        <div className="mt-16 flex flex-col items-center gap-3 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full border border-rule bg-panel font-display text-3xl text-ink-muted">
            §
          </div>
          <h2 className="font-display text-xl text-ink-soft">No matters yet</h2>
          <p className="max-w-sm text-sm leading-relaxed text-ink-muted">
            Create a matter, upload the case-file PDFs, and generate a
            hearing-ready brief you can verify line by line against the record.
          </p>
          <button
            type="button"
            onClick={() => setShowNew(true)}
            className="mt-2 rounded-sm border border-accent px-4 py-2 text-sm font-semibold text-accent transition-colors hover:bg-accent-wash"
          >
            Create your first matter
          </button>
        </div>
      )}

      {matters !== null && matters.length > 0 && (
        <ul className="mt-6 space-y-3">
          {matters.map((m) => (
            <li key={m.matter_id}>
              <div className="group relative rounded-sm border border-rule bg-panel shadow-card transition-shadow hover:shadow-lift">
                <Link
                  href={`/matters/${m.matter_id}`}
                  className="block px-5 py-4"
                >
                  <h3 className="pr-24 font-display text-lg font-medium leading-snug text-ink group-hover:text-accent-deep">
                    {m.title}
                  </h3>
                  <p className="mt-1 flex gap-3 font-mono text-[11px] text-ink-muted">
                    <span>{formatCreated(m.created)}</span>
                    <span>
                      {m.documents.length}{" "}
                      {m.documents.length === 1 ? "document" : "documents"}
                    </span>
                  </p>
                </Link>
                <div className="absolute right-3 top-3">
                  {confirmDelete === m.matter_id ? (
                    <span className="flex items-center gap-2 rounded-sm border border-oxblood/30 bg-oxblood-wash px-2 py-1">
                      <span className="text-xs text-oxblood">
                        Delete permanently?
                      </span>
                      <button
                        type="button"
                        disabled={deleting === m.matter_id}
                        onClick={() => void remove(m.matter_id)}
                        className="text-xs font-semibold text-oxblood underline underline-offset-2 disabled:opacity-50"
                      >
                        {deleting === m.matter_id ? "Deleting…" : "Delete"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmDelete(null)}
                        className="text-xs text-ink-muted hover:text-ink"
                      >
                        Keep
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirmDelete(m.matter_id)}
                      title="Delete matter"
                      className="rounded-sm px-2 py-1 text-xs text-ink-muted opacity-0 transition-opacity hover:text-oxblood focus:opacity-100 group-hover:opacity-100"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
