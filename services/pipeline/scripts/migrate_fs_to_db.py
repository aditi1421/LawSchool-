"""Migrate filesystem matters (the v1 store) into Postgres + object storage.

The old layout, written by the retired MatterStore:

    data/matters/<matter_id>/manifest.json
                            /files/<name>.pdf
                            /pages/<name>.pdf.json
                            /artifacts.json
                            /drafts/<id>.json

Re-ingesting from the manifest would re-run OCR on every scan — hours of CPU
for pages already read. So this replays the *extracted pages* verbatim and only
re-derives what is cheap (chunks, embeddings). OCR results are preserved.

Idempotent: a matter already in Postgres is skipped unless --force.

    uv run python scripts/migrate_fs_to_db.py [--data-dir data/matters] [--force] [--dry-run]
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from pipeline.db.engine import ensure_extensions, session_scope
from pipeline.db.models import MatterRow
from pipeline.db.repository import MatterRepository, storage_key
from pipeline.embeddings import get_embedder
from pipeline.ingest.extract import PageExtract
from pipeline.models import Language
from pipeline.storage import get_storage


def load_pages(pages_path: Path) -> list[PageExtract]:
    raw = json.loads(pages_path.read_text())
    return [
        PageExtract(
            page=r["page"],
            text=r["text"],
            method=r["method"],
            confidence=r["confidence"],
            language=Language(r["language"]),
        )
        for r in raw
    ]


def migrate_matter(repo: MatterRepository, matter_dir: Path, force: bool, dry: bool) -> str:
    manifest_path = matter_dir / "manifest.json"
    if not manifest_path.exists():
        return "skip (no manifest)"
    manifest = json.loads(manifest_path.read_text())
    matter_id = manifest["matter_id"]

    with session_scope() as s:
        existing = s.get(MatterRow, matter_id)
        if existing is not None and not force:
            return "skip (already in Postgres)"
        if existing is not None:
            s.delete(existing)

    if dry:
        docs = len(manifest.get("documents", []))
        return f"would migrate: {manifest['title']!r} ({docs} document(s))"

    with session_scope() as s:
        s.add(
            MatterRow(
                id=matter_id,
                title=manifest["title"],
                created=date.fromisoformat(manifest["created"]),
            )
        )

    migrated = 0
    for doc in manifest.get("documents", []):
        filename = doc["file"]
        pdf = matter_dir / "files" / filename
        pages_json = matter_dir / "pages" / f"{filename}.json"
        if not pdf.exists() or not pages_json.exists():
            print(f"    ! {filename}: missing bytes or pages — skipped")
            continue
        key = storage_key(matter_id, filename)
        repo.storage.put(key, pdf.read_bytes())
        # Replay the stored extraction rather than re-reading the PDF: OCR'd
        # pages would otherwise cost hours of CPU to reproduce.
        # ocr_status is derived from the pages, not copied: manifests written
        # before the field existed say "not_needed" for documents that were in
        # fact OCR'd.
        repo._persist_document(matter_id, filename, key, load_pages(pages_json))
        migrated += 1

    artifacts = matter_dir / "artifacts.json"
    if artifacts.exists():
        repo.save_artifacts(matter_id, json.loads(artifacts.read_text()))

    drafts_dir = matter_dir / "drafts"
    n_drafts = 0
    if drafts_dir.is_dir():
        for d in sorted(drafts_dir.glob("*.json")):
            data = json.loads(d.read_text())
            repo.save_draft(matter_id, data.get("doc_type", "legal_notice"), data)
            n_drafts += 1

    return (
        f"migrated {manifest['title']!r}: {migrated} document(s)"
        f"{', artifacts' if artifacts.exists() else ''}"
        f"{f', {n_drafts} draft(s)' if n_drafts else ''}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data/matters")
    ap.add_argument("--force", action="store_true", help="re-migrate matters already in Postgres")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    root = Path(args.data_dir)
    if not root.is_dir():
        print(f"no filesystem matters at {root} — nothing to migrate")
        return 0

    ensure_extensions()
    repo = MatterRepository(storage=get_storage(), embedder=get_embedder())

    dirs = sorted(d for d in root.iterdir() if d.is_dir())
    print(f"found {len(dirs)} matter(s) under {root}\n")
    for d in dirs:
        print(f"  {d.name}: {migrate_matter(repo, d, args.force, args.dry_run)}")

    if not args.dry_run:
        print(
            f"\nDone. The old files under {root} are untouched — verify the app, "
            "then remove them yourself."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
