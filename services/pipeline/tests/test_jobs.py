"""Job record tests. Real Postgres (the uniqueness guard is a DB index)."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from pipeline.jobs import (
    JobAlreadyRunning,
    active_job,
    create_job,
    get_job,
    list_jobs,
    reconcile_stale_jobs,
    run_job,
)

from tests.conftest import requires_db

pytestmark = requires_db


def _matter(repo) -> str:
    return repo.create("M", today=date(2026, 7, 15)).matter_id


def test_job_lifecycle_success(repo) -> None:
    mid = _matter(repo)
    job_id = create_job(mid, "artifacts")
    assert get_job(job_id)["status"] == "queued"

    run_job(job_id, lambda: ({"violations": []}, "ollama"))

    job = get_job(job_id)
    assert job["status"] == "succeeded"
    assert job["result"] == {"violations": []}
    assert job["provider"] == "ollama"  # which model produced it is not incidental
    assert job["started_at"] and job["finished_at"]


def test_failure_is_recorded_not_raised(repo) -> None:
    """The worker has nobody to catch it. An exception that only reaches the
    logs leaves the job at 'running' forever, which the UI reads as 'working'."""
    mid = _matter(repo)
    job_id = create_job(mid, "artifacts")

    def boom():
        raise RuntimeError("record too long for qwen2.5:14b")

    run_job(job_id, boom)  # must not raise

    job = get_job(job_id)
    assert job["status"] == "failed"
    assert "record too long" in job["error"]
    assert job["finished_at"]


def test_second_job_is_refused_while_one_is_live(repo) -> None:
    mid = _matter(repo)
    create_job(mid, "artifacts")
    with pytest.raises(JobAlreadyRunning, match="already running"):
        create_job(mid, "artifacts")


def test_concurrent_creates_race_and_only_one_wins(repo) -> None:
    """The real guard: two clicks a millisecond apart both pass a Python
    'is one running?' check. The partial unique index does not."""
    mid = _matter(repo)

    def attempt(_):
        try:
            return create_job(mid, "artifacts")
        except JobAlreadyRunning:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    assert sum(r is not None for r in results) == 1
    assert len(list_jobs(mid, "artifacts")) == 1


def test_different_kinds_and_matters_do_not_block_each_other(repo) -> None:
    a, b = _matter(repo), _matter(repo)
    create_job(a, "artifacts")
    create_job(a, "draft")  # different kind, same matter
    create_job(b, "artifacts")  # different matter
    assert len(list_jobs(a)) == 2
    assert len(list_jobs(b)) == 1


def test_slot_frees_once_the_job_finishes(repo) -> None:
    mid = _matter(repo)
    first = create_job(mid, "artifacts")
    run_job(first, lambda: ({}, "claude"))

    second = create_job(mid, "artifacts")  # no longer blocked
    assert second != first
    assert active_job(mid, "artifacts")["job_id"] == second


def test_failed_job_also_frees_the_slot(repo) -> None:
    mid = _matter(repo)
    first = create_job(mid, "artifacts")

    def boom():
        raise RuntimeError("ollama not running")

    run_job(first, boom)
    assert create_job(mid, "artifacts")  # a failure must not wedge the matter


def test_active_job_reports_only_live_ones(repo) -> None:
    mid = _matter(repo)
    assert active_job(mid, "artifacts") is None
    job_id = create_job(mid, "artifacts")
    assert active_job(mid, "artifacts")["job_id"] == job_id
    run_job(job_id, lambda: ({}, "ollama"))
    assert active_job(mid, "artifacts") is None


def test_restart_fails_orphaned_jobs(repo) -> None:
    """Workers are threads; a restart kills them silently. Rows left 'running'
    would spin the UI forever and keep the uniqueness index blocking."""
    mid = _matter(repo)
    job_id = create_job(mid, "artifacts")

    assert reconcile_stale_jobs() == 1

    job = get_job(job_id)
    assert job["status"] == "failed"
    assert "restarted" in job["error"]
    assert create_job(mid, "artifacts")  # matter is usable again


def test_jobs_die_with_their_matter(repo) -> None:
    mid = _matter(repo)
    create_job(mid, "artifacts")
    repo.delete(mid)  # DPDP hard delete leaves nothing behind
    assert list_jobs(mid) == []


def test_params_survive_for_a_failed_job(repo) -> None:
    """A failed draft must still say what it was asked to draft — the caller
    is long gone by then."""
    mid = _matter(repo)
    job_id = create_job(
        mid, "draft", {"doc_type": "bail_application", "instructions": "urge parity"}
    )
    run_job(job_id, lambda: (_ for _ in ()).throw(RuntimeError("model unavailable")))

    job = get_job(job_id)
    assert job["status"] == "failed"
    assert job["params"]["doc_type"] == "bail_application"
    assert job["params"]["instructions"] == "urge parity"
