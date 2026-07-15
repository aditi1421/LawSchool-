"""Long-running generation as tracked jobs.

Generation takes minutes. Holding an HTTP request open for that means the
browser owns the work: navigating away orphans it, a reload starts a second
one, and nothing records that it happened. Jobs move the work off the request
and into a row that survives both.

Scope, honestly: the worker is a thread in this process. That is enough for
one API instance and it is not a queue — jobs do not survive a restart (see
`reconcile_stale_jobs`), and they do not spread across machines. When there
are real users, this is the seam a Celery/RQ worker slots into; the job record
and its API do not change.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from pipeline.db.engine import session_scope
from pipeline.db.models import JobRow

logger = logging.getLogger(__name__)

JobKind = Literal["artifacts", "draft"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]


class JobAlreadyRunning(RuntimeError):
    """A job of this kind is already in flight for this matter."""


def create_job(matter_id: str, kind: JobKind, params: dict | None = None) -> str:
    """Claim the single in-flight slot for (matter, kind).

    The uniqueness is enforced by a partial unique index, not by checking
    first: two clicks a millisecond apart both pass a Python check, and the
    result is two models running and one silently overwriting the other.
    """
    job_id = uuid.uuid4().hex[:12]
    try:
        with session_scope() as s:
            s.add(
                JobRow(
                    id=job_id,
                    matter_id=matter_id,
                    kind=kind,
                    status="queued",
                    params=params or {},
                )
            )
    except IntegrityError as exc:
        # Read by a lawyer, not a developer: say what is happening, not which
        # constraint fired.
        what = "A brief is already being generated" if kind == "artifacts" else "A draft is already being generated"
        raise JobAlreadyRunning(
            f"{what} for this matter. It will finish on its own — you can leave "
            f"this page and come back."
        ) from exc
    return job_id


def get_job(job_id: str) -> dict | None:
    with session_scope() as s:
        job = s.get(JobRow, job_id)
        return _as_dict(job) if job else None


def list_jobs(matter_id: str, kind: JobKind | None = None, limit: int = 20) -> list[dict]:
    with session_scope() as s:
        q = select(JobRow).where(JobRow.matter_id == matter_id)
        if kind:
            q = q.where(JobRow.kind == kind)
        rows = s.execute(q.order_by(JobRow.created_at.desc()).limit(limit)).scalars().all()
        return [_as_dict(r) for r in rows]


def active_job(matter_id: str, kind: JobKind) -> dict | None:
    with session_scope() as s:
        row = s.execute(
            select(JobRow).where(
                JobRow.matter_id == matter_id,
                JobRow.kind == kind,
                JobRow.status.in_(("queued", "running")),
            )
        ).scalar_one_or_none()
        return _as_dict(row) if row else None


def _as_dict(job: JobRow) -> dict:
    return {
        "job_id": job.id,
        "matter_id": job.matter_id,
        "kind": job.kind,
        "status": job.status,
        "params": job.params or {},
        "result": job.result,
        "error": job.error,
        "provider": job.provider,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _mark(job_id: str, **fields: Any) -> None:
    with session_scope() as s:
        job = s.get(JobRow, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)


def run_job(job_id: str, work) -> None:
    """Execute a job's work in this thread, recording the outcome.

    Never raises: this runs in a background worker with nobody to catch it, and
    an exception that only reaches the logs leaves the job stuck at 'running'
    forever, which the UI reads as "still working".
    """
    _mark(job_id, status="running", started_at=datetime.now(UTC))
    try:
        result, provider = work()
        _mark(
            job_id,
            status="succeeded",
            result=result,
            provider=provider,
            finished_at=datetime.now(UTC),
        )
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        _mark(
            job_id,
            status="failed",
            # The message is shown to the user: our own errors are written to
            # be read (an over-long record, a missing Ollama model, no credits).
            error=f"{type(exc).__name__}: {exc}",
            finished_at=datetime.now(UTC),
        )


def reconcile_stale_jobs() -> int:
    """Fail jobs left 'running' by a previous process, at startup.

    Workers are threads, so a restart kills them silently. Without this the
    rows sit at 'running' forever: the UI spins on a job nothing is doing, and
    the partial unique index keeps blocking new ones for that matter.
    """
    with session_scope() as s:
        rows = s.execute(
            select(JobRow).where(JobRow.status.in_(("queued", "running")))
        ).scalars().all()
        for job in rows:
            job.status = "failed"
            job.error = "interrupted: the server restarted while this job was running"
            job.finished_at = datetime.now(UTC)
        return len(rows)
