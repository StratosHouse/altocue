"""Small Swift helpers for what only macOS can do. Compiled on first use, cached by source."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

COMPILER = "swiftc"
COMPILE_TIMEOUT = 300


def _cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "altocue"


def compiled(source: Path, name: str) -> Path | None:
    """The built helper, once per version of its source. None off a Mac, or with no compiler."""
    if sys.platform != "darwin" or not shutil.which(COMPILER):
        return None
    code = source.read_bytes()
    target = _cache() / f"{name}-{hashlib.sha256(code).hexdigest()[:12]}"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(f".{os.getpid()}")
    try:
        done = subprocess.run([COMPILER, "-O", str(source), "-o", str(temp)], capture_output=True, text=True, timeout=COMPILE_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if done.returncode != 0 or not temp.exists():
        return None
    temp.replace(target)
    return target
