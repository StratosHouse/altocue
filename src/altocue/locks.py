"""Speech models, browser renders and video encodes are heavy. Run one at a time, per machine, across projects.

A machine with room for more, such as a render PC, sets ALTOCUE_HEAVY_JOBS to run that many at once.
"""

from __future__ import annotations

import fcntl
import os
import platform
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

GB = 1024**3
MEMORY_PER_WORKER = 5  # GB of machine memory set aside for each browser worker
MOST_WORKERS = 4

_held = threading.local()


def heavy_jobs() -> int:
    """How many heavy jobs this machine runs at once: one, unless ALTOCUE_HEAVY_JOBS says more."""
    chosen = os.environ.get("ALTOCUE_HEAVY_JOBS", "")
    return int(chosen) if chosen.isdigit() and int(chosen) > 0 else 1


def _take(paths: list[Path]) -> object:
    """Hold the first free lock of `paths`, or None when all are taken."""
    for path in paths:
        handle = path.open("w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            handle.close()
    return None


@contextmanager
def heavy(label: str) -> Iterator[None]:
    """Wait for a free heavy slot on this machine, then hold it.

    A job that holds one can call a step that asks for it again. `flock` would wait on itself.
    """
    if getattr(_held, "yes", False):
        yield
        return
    folder = Path.home() / ".altocue"
    folder.mkdir(parents=True, exist_ok=True)
    count = heavy_jobs()
    paths = [folder / "heavy.lock"] + [folder / f"heavy-{n}.lock" for n in range(2, count + 1)]
    handle = _take(paths)
    if handle is None:
        print(f"Waiting for another Altocue job to finish before {label}.", flush=True)
        if count == 1:
            handle = paths[0].open("w")
            fcntl.flock(handle, fcntl.LOCK_EX)
        while handle is None:
            time.sleep(0.5)
            handle = _take(paths)
    _held.yes = True
    try:
        yield
    finally:
        _held.yes = False
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def memory_gb() -> float:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GB


def free_memory_gb() -> float | None:
    """What the system could hand over now, or None where that cannot be read."""
    if platform.system() == "Darwin":  # the same figure `memory_pressure` prints as a percentage
        try:
            done = subprocess.run(["sysctl", "-n", "kern.memorystatus_level"], capture_output=True, text=True, check=False)
        except OSError:  # /usr/sbin is not on every PATH
            return None
        return memory_gb() * int(done.stdout) / 100 if done.stdout.strip().isdigit() else None
    info = Path("/proc/meminfo")
    found = re.search(r"MemAvailable:\s+(\d+) kB", info.read_text()) if info.exists() else None
    return int(found.group(1)) / 1024**2 if found else None


def render_workers() -> int:
    """Browser workers that fit in this machine's memory. `ALTOCUE_RENDER_WORKERS` overrides."""
    chosen = os.environ.get("ALTOCUE_RENDER_WORKERS", "")
    if chosen.isdigit() and int(chosen) > 0:
        return int(chosen)
    return max(1, min(MOST_WORKERS, int(memory_gb() // MEMORY_PER_WORKER)))
