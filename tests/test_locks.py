from __future__ import annotations

import subprocess
import sys
import time

from altocue import locks

HOLD = """
import sys
from altocue import locks
with locks.heavy("a test"):
    print("held", flush=True)
    sys.stdin.readline()
"""


def test_a_second_heavy_job_waits_its_turn(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    holder = subprocess.Popen([sys.executable, "-c", HOLD], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    second = subprocess.Popen(
        [sys.executable, "-c", 'from altocue import locks\nwith locks.heavy("the second job"):\n    print("got it")'],
        stdout=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.6)
    assert second.poll() is None
    holder.communicate("\n")
    out, _ = second.communicate(timeout=10)
    assert "Waiting for another Altocue job" in out and "got it" in out


def test_workers_follow_memory(monkeypatch):
    monkeypatch.delenv("ALTOCUE_RENDER_WORKERS", raising=False)
    monkeypatch.setattr(locks, "memory_gb", lambda: 16.0)
    assert locks.render_workers() == 3
    monkeypatch.setattr(locks, "memory_gb", lambda: 8.0)
    assert locks.render_workers() == 1
    monkeypatch.setattr(locks, "memory_gb", lambda: 64.0)
    assert locks.render_workers() == 4


def test_workers_can_be_set_by_hand(monkeypatch):
    monkeypatch.setenv("ALTOCUE_RENDER_WORKERS", "2")
    assert locks.render_workers() == 2
