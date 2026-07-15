"""Content-addressed cache for LLM judgements.

A gate run asks the same question many times: the same claim against the same
page, the same gold event against the same generated event, run after run.
Every question is a deterministic function of its inputs, so we key on a hash
of them and never pay for the same call twice — within a run or across runs.

Backed by a JSON file under `.eval_cache/` (gitignored) so a re-run after a
crash or a tweak elsewhere in the harness is nearly free. Thread-safe: a
per-key lock means N workers asking the same question make ONE API call.
"""

import hashlib
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

DEFAULT_CACHE_DIR = Path(os.environ.get("EVAL_CACHE_DIR", ".eval_cache"))


def cache_key(*parts: str) -> str:
    """Stable hash of the inputs to one judgement."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")  # unambiguous separator
    return digest.hexdigest()


class VerdictCache:
    """Thread-safe bool cache with optional write-through JSON persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        self._data: dict[str, bool] = {}
        self.hits = 0
        self.misses = 0
        if path is not None and path.exists():
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded, dict):
                    self._data = {k: bool(v) for k, v in loaded.items()}
            except (json.JSONDecodeError, OSError):
                self._data = {}  # a corrupt cache is a cache miss, never an error

    def get_or_compute(self, key: str, compute: Callable[[], bool]) -> bool:
        with self._lock:
            if key in self._data:
                self.hits += 1
                return self._data[key]
            key_lock = self._key_locks.setdefault(key, threading.Lock())

        # Serialize concurrent askers of the SAME question; different questions
        # still run in parallel.
        with key_lock:
            with self._lock:
                if key in self._data:
                    self.hits += 1
                    return self._data[key]
                self.misses += 1
            value = compute()
            with self._lock:
                self._data[key] = value
                self._flush_locked()
            return value

    def _flush_locked(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data))
            tmp.replace(self._path)  # atomic: never leave a half-written cache
        except OSError:
            pass  # the cache is an optimisation; a write failure must not fail the gate


def default_cache(name: str) -> VerdictCache:
    """On-disk cache for one judgement kind (`support`, `event_match`, ...)."""
    return VerdictCache(DEFAULT_CACHE_DIR / f"{name}.json")
