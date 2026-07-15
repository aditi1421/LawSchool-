"""HTTP surface tests, against real Postgres + local blob storage.

Skipped when no database is reachable — see tests/conftest.py.
"""

from pathlib import Path

import pytest

from tests.conftest import requires_db
from tests.test_ingest import PLAINT_TEXT, make_scan_pdf, make_text_pdf

pytestmark = requires_db


def _upload(client, matter_id: str, name: str, pdf: Path):
    return client.post(
        f"/matters/{matter_id}/files",
        files={"file": (name, pdf.read_bytes(), "application/pdf")},
    )


def test_matter_crud_and_upload(api_client, tmp_path: Path) -> None:
    created = api_client.post("/matters", json={"title": "Sharma v. Verma"}).json()
    matter_id = created["matter_id"]

    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    resp = _upload(api_client, matter_id, "plaint.pdf", pdf)
    assert resp.status_code == 200
    assert resp.json()["doc_type"] == "plaint"
    assert resp.json()["ocr_status"] == "not_needed"

    assert api_client.get(f"/matters/{matter_id}").json()["documents"][0]["file"] == "plaint.pdf"
    assert any(m["matter_id"] == matter_id for m in api_client.get("/matters").json())

    # Non-PDF rejected before it reaches storage.
    assert api_client.post(
        f"/matters/{matter_id}/files", files={"file": ("x.txt", b"hi", "text/plain")}
    ).status_code == 422

    assert api_client.delete(f"/matters/{matter_id}").status_code == 204
    assert api_client.get(f"/matters/{matter_id}").status_code == 404


def test_upload_to_missing_matter_404s(api_client, tmp_path: Path) -> None:
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    assert _upload(api_client, "nonexistent", "plaint.pdf", pdf).status_code == 404


def test_corrupt_pdf_gives_a_clear_422_not_a_500(api_client) -> None:
    # Regression: an unreadable PDF raised through the handler, producing a 500
    # generated outside the CORS middleware — the browser then reported the
    # server error as an opaque network failure.
    matter_id = api_client.post("/matters", json={"title": "Bad"}).json()["matter_id"]
    resp = api_client.post(
        f"/matters/{matter_id}/files",
        files={"file": ("broken.pdf", b"%PDF-1.4 not really a pdf", "application/pdf")},
    )
    assert resp.status_code == 422
    assert "corrupted" in resp.json()["detail"].lower()
    assert api_client.get(f"/matters/{matter_id}").json()["documents"] == []


def test_unhandled_errors_carry_cors_headers(api_client, monkeypatch) -> None:
    """A 500 must reach the browser as JSON with CORS headers, not as an
    opaque 'Failed to fetch'."""
    from fastapi.testclient import TestClient

    from pipeline.api import app

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("pipeline.api.get_store", boom)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/matters", headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 500
    assert "kaboom" in resp.json()["detail"]
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_scanned_upload_returns_fast_and_queues_ocr(
    api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upload response must not wait on OCR — it used to block the whole
    event loop for minutes."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "pipeline.api._ocr_worker",
        lambda matter_id, filename: calls.append((matter_id, filename)),
    )
    matter_id = api_client.post("/matters", json={"title": "Scan"}).json()["matter_id"]
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)

    resp = _upload(api_client, matter_id, "scan.pdf", pdf)
    assert resp.status_code == 200
    assert resp.json()["ocr_status"] == "pending"
    assert resp.json()["needs_ocr_pages"] == [1]
    assert calls == [(matter_id, "scan.pdf")]  # queued, not run inline


def test_text_layer_upload_does_not_queue_ocr(
    api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "pipeline.api._ocr_worker", lambda matter_id, filename: calls.append(filename)
    )
    matter_id = api_client.post("/matters", json={"title": "Digital"}).json()["matter_id"]
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])

    assert _upload(api_client, matter_id, "plaint.pdf", pdf).json()["ocr_status"] == "not_needed"
    assert calls == []


def test_start_ocr_endpoint(api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.api._ocr_worker", lambda *a: None)
    matter_id = api_client.post("/matters", json={"title": "Scan"}).json()["matter_id"]
    pdf = tmp_path / "scan.pdf"
    make_scan_pdf(pdf)
    _upload(api_client, matter_id, "scan.pdf", pdf)

    resp = api_client.post(f"/matters/{matter_id}/files/scan.pdf/ocr")
    assert resp.status_code == 202
    assert resp.json()["ocr_status"] == "pending"
    assert api_client.post(f"/matters/{matter_id}/files/nope.pdf/ocr").status_code == 404


def test_delete_file(api_client, tmp_path: Path) -> None:
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    pdf = tmp_path / "wrong file.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    _upload(api_client, matter_id, "wrong file.pdf", pdf)
    assert len(api_client.get(f"/matters/{matter_id}").json()["documents"]) == 1

    assert api_client.delete(f"/matters/{matter_id}/files/wrong%20file.pdf").status_code == 204
    assert api_client.get(f"/matters/{matter_id}").json()["documents"] == []
    assert api_client.delete(f"/matters/{matter_id}/files/ghost.pdf").status_code == 404


def test_serve_pdf_from_object_storage(api_client, tmp_path: Path) -> None:
    """The split-view viewer fetches source PDFs through this route."""
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    _upload(api_client, matter_id, "plaint.pdf", pdf)

    resp = api_client.get(f"/matters/{matter_id}/files/plaint.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
    assert api_client.get(f"/matters/{matter_id}/files/ghost.pdf").status_code == 404


def test_chunk_text_endpoint_backs_pdf_highlighting(api_client, tmp_path: Path) -> None:
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    _upload(api_client, matter_id, "plaint.pdf", pdf)

    resp = api_client.get(
        f"/matters/{matter_id}/chunk", params={"file": "plaint.pdf", "page": 1, "para": 1}
    )
    assert resp.status_code == 200
    assert "sale deed" in resp.json()["text"]

    # Whole page when no para is given.
    whole = api_client.get(
        f"/matters/{matter_id}/chunk", params={"file": "plaint.pdf", "page": 1}
    )
    assert "IN THE COURT" in whole.json()["text"]

    assert api_client.get(
        f"/matters/{matter_id}/chunk", params={"file": "ghost.pdf", "page": 1}
    ).status_code == 404


def test_artifacts_404_before_generation(api_client) -> None:
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    assert api_client.get(f"/matters/{matter_id}/artifacts").status_code == 404
    assert api_client.get(f"/matters/{matter_id}/export.docx").status_code == 404


def test_generate_artifacts_on_empty_matter_is_422_not_a_crash(api_client) -> None:
    """No readable content must be a clear error, and must not queue a job."""
    matter_id = api_client.post("/matters", json={"title": "Empty"}).json()["matter_id"]
    resp = api_client.post(f"/matters/{matter_id}/artifacts")
    assert resp.status_code == 422
    assert "no readable content" in resp.json()["detail"]
    assert api_client.get(f"/matters/{matter_id}/jobs").json() == []


def test_generate_returns_a_job_not_a_held_request(
    api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The response must come back immediately with a job to poll — a
    multi-minute request held open by a browser tab is not an architecture."""
    monkeypatch.setattr("pipeline.api._run_artifacts_job", lambda *a: None)
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    _upload(api_client, matter_id, "plaint.pdf", pdf)

    resp = api_client.post(f"/matters/{matter_id}/artifacts")
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert resp.json()["status"] == "queued"

    job = api_client.get(f"/jobs/{job_id}").json()
    assert job["kind"] == "artifacts" and job["matter_id"] == matter_id
    assert api_client.get("/jobs/nonexistent").status_code == 404


def test_double_click_is_refused_not_duplicated(
    api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pipeline.api._run_artifacts_job", lambda *a: None)
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    _upload(api_client, matter_id, "plaint.pdf", pdf)

    assert api_client.post(f"/matters/{matter_id}/artifacts").status_code == 202
    second = api_client.post(f"/matters/{matter_id}/artifacts")
    assert second.status_code == 409  # not a second model racing the first
    assert "already running" in second.json()["detail"]
    assert len(api_client.get(f"/matters/{matter_id}/jobs").json()) == 1


def test_draft_returns_a_job(api_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pipeline.api._run_draft_job", lambda *a: None)
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    pdf = tmp_path / "plaint.pdf"
    make_text_pdf(pdf, [PLAINT_TEXT])
    _upload(api_client, matter_id, "plaint.pdf", pdf)

    resp = api_client.post(
        f"/matters/{matter_id}/drafts",
        json={"doc_type": "legal_notice", "instructions": "demand possession"},
    )
    assert resp.status_code == 202
    job = api_client.get(f"/jobs/{resp.json()['job_id']}").json()
    assert job["kind"] == "draft"
    # A failed draft must still say what it was asked for; the caller is gone.
    assert job["params"] == {"doc_type": "legal_notice", "instructions": "demand possession"}


def test_drafts_empty_and_404s(api_client) -> None:
    matter_id = api_client.post("/matters", json={"title": "M"}).json()["matter_id"]
    assert api_client.get(f"/matters/{matter_id}/drafts").json() == []
    assert api_client.get(f"/matters/{matter_id}/drafts/nope").status_code == 404
    assert api_client.get(f"/matters/{matter_id}/drafts/nope.docx").status_code == 404
    assert api_client.post(
        f"/matters/{matter_id}/drafts", json={"doc_type": "not_a_type"}
    ).status_code == 422
