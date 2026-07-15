"""Draft persistence: JSON files under the matter directory."""

import json
import uuid
from pathlib import Path

from pipeline.drafting.models import DraftDocument
from pipeline.ingest.matter import MatterStore


def _drafts_dir(store: MatterStore, matter_id: str) -> Path:
    d = store._matter_dir(matter_id) / "drafts"
    d.mkdir(exist_ok=True)
    return d


def save_draft(store: MatterStore, draft: DraftDocument) -> str:
    draft_id = uuid.uuid4().hex[:12]
    path = _drafts_dir(store, draft.matter_id) / f"{draft_id}.json"
    path.write_text(draft.model_dump_json(indent=2))
    return draft_id


def load_draft(store: MatterStore, matter_id: str, draft_id: str) -> DraftDocument:
    path = _drafts_dir(store, matter_id) / f"{draft_id}.json"
    return DraftDocument.model_validate(json.loads(path.read_text()))


def list_drafts(store: MatterStore, matter_id: str) -> list[dict]:
    out = []
    for path in sorted(
        _drafts_dir(store, matter_id).glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        draft = DraftDocument.model_validate(json.loads(path.read_text()))
        out.append(
            {
                "draft_id": path.stem,
                "doc_type": draft.doc_type.value,
                "title": draft.title,
                "paragraphs": len(draft.paragraphs),
                "missing_info": len(draft.missing_info),
            }
        )
    return out
