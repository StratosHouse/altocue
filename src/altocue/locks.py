"""Speech models, browser renders and video encodes are heavy. Run one at a time, per machine, across projects."""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

GB = 1024**3
MEMORY_PER_WORKER = 5  # GB of machine memory set aside for each browser worker
MOST_WORKERS = 4

_held = threading.local()


@contextmanager
def heavy(label: str) -> Iterator[None]:
    """Wait for any other heavy Altocue job on this machine, then hold the lock.

    A job that holds it can call a step that asks for it again. `flock` would wait on itself.
    """
    if getattr(_held, "yes", False):
        yield
        return
    path = Path.home() / ".altocue" / "heavy.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"Waiting for another Altocue job to finish before {label}.", flush=True)
            fcntl.flock(handle, fcntl.LOCK_EX)
        _held.yes = True
        try:
            yield
        finally:
            _held.yes = False
            fcntl.flock(handle, fcntl.LOCK_UN)


def memory_gb() -> float:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GB


def render_workers() -> int:
    """Browser workers that fit in this machine's memory. `ALTOCUE_RENDER_WORKERS` overrides."""
    chosen = os.environ.get("ALTOCUE_RENDER_WORKERS", "")
    if chosen.isdigit() and int(chosen) > 0:
        return int(chosen)
    return max(1, min(MOST_WORKERS, int(memory_gb() // MEMORY_PER_WORKER)))
