"""The command starts fast: asking for help never waits for NumPy or OpenCV."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

PROBE = """
import contextlib, io, json, sys
from altocue.cli import main

code = None
with contextlib.redirect_stdout(io.StringIO()) as out:
    try:
        code = main(sys.argv[1:])
    except SystemExit as stop:
        code = stop.code
print(json.dumps({"code": code, "help": out.getvalue(), "loaded": sorted({"numpy", "cv2"} & set(sys.modules))}))
"""


@pytest.mark.parametrize("argv", [["--help"], ["status", "--help"], ["run", "--help"]], ids=["altocue", "status", "run"])
def test_help_loads_neither_numpy_nor_opencv(argv):
    """Each stage loads only when called. `status` used to import the frame and grade stages at the top, and both with them."""
    done = subprocess.run([sys.executable, "-c", PROBE, *argv], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert found["code"] == 0 and found["help"].startswith("usage: altocue")
    assert found["loaded"] == []


def test_status_still_reads_a_project(tmp_path, capsys):
    """The stages it asks about now load when it reaches them, so a real status must still answer."""
    from altocue.cli import main
    from altocue.project import Project

    project = Project.create(tmp_path / "video")
    assert main(["status", "--project", str(project.root)]) in (0, 1)
    assert "ingest" in capsys.readouterr().out
