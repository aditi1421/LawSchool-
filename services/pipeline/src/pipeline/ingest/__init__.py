"""Ingestion: extraction with provenance, OCR fallback, doc-type classification."""

from pipeline.ingest.classify import classify_doc_type
from pipeline.ingest.extract import PageExtract, UnreadablePdf, extract_pages

__all__ = ["PageExtract", "UnreadablePdf", "classify_doc_type", "extract_pages"]
