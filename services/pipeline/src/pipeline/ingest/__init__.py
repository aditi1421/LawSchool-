"""Ingestion: extraction with provenance, OCR fallback, doc-type classification."""

from pipeline.ingest.classify import classify_doc_type
from pipeline.ingest.extract import PageExtract, extract_pages
from pipeline.ingest.matter import MatterStore

__all__ = ["MatterStore", "PageExtract", "classify_doc_type", "extract_pages"]
