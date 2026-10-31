"""The codex second opinion, through a stand-in binary, and the agent definition."""

from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from altocue.errors import AltocueError
from altocue.project import Project
from altocue.review import codex, report, rubric

ROOT = Path(__file__).resolve().parents[1]


def stand_in(folder: Path, log: Path, *, answer: dict | None = None, code: int = 0) -> Path:
    """A fake `codex` that records how it was called and what it could see."""
    folder.mkdir(parents=True, exist_ok=True)
    good = answer or {"scores": {k: {"score": 4, "why": "ok"} for k in rubric.DIMENSIONS}, "notes": [], "could_not_judge": []}
    script = folder / "codex"
    script.write_text(
        f"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
seen = sorted(os.listdir(args[args.index("-C") + 1]))
json.dump({{"args": args, "stdin": sys.stdin.read(), "cwd": args[args.index("-C") + 1], "listing": seen}}, open({str(log)!r}, "w"))
open(out, "w").write({json.dumps(json.dumps(good))})
sys.exit({code})
"""
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return folder


@pytest.fixture
def project(tmp_path):
    project = Project.create(tmp_path / "p")
    (project.root / "edl.json").write_text("{}")
    for name in ("sheets/sheet_001.jpg", "cuts/cut_001.jpg", "hook/hook_01.jpg"):
        path = project.review_dir / "pack" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpg")
    return project


@pytest.fixture
def fake(tmp_path, monkeypatch):
    log = tmp_path / "called.json"
    monkeypatch.setenv("PATH", f"{stand_in(tmp_path / 'bin', log)}{os.pathsep}{os.environ['PATH']}")
    return log


def test_no_upload_without_opt_in(project, fake):
    with pytest.raises(AltocueError) as error:
        codex.review(project, "prompt", upload=False, say=lambda t: None)
    assert "--upload" in str(error.value) and not fake.exists()


def test_warning_names_where_frames_go(project, fake):
    said = []
    codex.review(project, "prompt", upload=True, say=said.append)
    assert "OpenAI" in " ".join(said) and fake.exists()


def test_missing_codex_is_a_clear_error(project, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(AltocueError) as error:
        codex.review(project, "prompt", upload=True, say=lambda t: None)
    assert "codex" in str(error.value).lower() and error.value.fix


def test_runs_read_only_from_a_folder_with_nothing_but_the_pack(project, fake):
    raw, info = codex.review(project, "the prompt", upload=True, say=lambda t: None)
    called = json.loads(fake.read_text())
    args = called["args"]
    assert args[0] == "exec" and "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in args and "--ignore-rules" in args and "--skip-git-repo-check" in args
    assert "--output-schema" in args and "--image" in " ".join(args)
    assert called["stdin"] == "the prompt" and args[-1] == "-"
    assert not called["cwd"].startswith(str(project.root))
    assert "edl.json" not in called["listing"]
    assert raw["scores"]["hook"]["score"] == 4
    assert info["vendor"] == "openai" and info["sees"] == "stills"


def test_schema_file_is_the_rubric(project, tmp_path, monkeypatch):
    log = tmp_path / "called.json"
    captured = {}
    real = codex.subprocess.run

    def spy(cmd, **kwargs):
        captured["schema"] = json.loads(Path(cmd[cmd.index("--output-schema") + 1]).read_text())
        return real(cmd, **kwargs)

    monkeypatch.setenv("PATH", f"{stand_in(tmp_path / 'bin2', log)}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(codex.subprocess, "run", spy)
    codex.review(project, "p", upload=True, say=lambda t: None)
    assert captured["schema"] == rubric.schema()


def test_failure_is_reported(project, tmp_path, monkeypatch):
    log = tmp_path / "called.json"
    monkeypatch.setenv("PATH", f"{stand_in(tmp_path / 'bin3', log, code=3)}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(AltocueError):
        codex.review(project, "p", upload=True, say=lambda t: None)


def test_image_cap_keeps_sheets_and_hook_first(project):
    for index in range(80):
        (project.review_dir / "pack" / "cuts" / f"cut_{index:03d}.jpg").write_bytes(b"jpg")
    chosen = codex.pick_images(project.review_dir / "pack")
    assert len(chosen) <= codex.MAX_IMAGES
    assert any("sheet" in p.name for p in chosen) and any("hook" in p.name for p in chosen)


def test_report_says_when_not_every_image_was_sent():
    result = {"reviewer": {"sees": "stills", "images": 30, "images_in_pack": 80}}
    assert any("30 of the pack's 80" in line for line in report.limits(result, []))


def agent_text() -> str:
    return (ROOT / "agents" / "editor-in-chief.md").read_text()


def frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    block = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in block.strip().splitlines())


def test_agent_uses_only_plugin_safe_fields():
    fields = frontmatter(agent_text())
    assert set(fields) <= {"name", "description", "tools", "model", "disallowedTools"}
    assert fields["name"] == "editor-in-chief" and "Bash" not in fields["tools"]


def test_agent_is_kept_blind_and_cannot_decide():
    text = agent_text()
    for path in ("edl.json", "timeline.json", "shots.json", "motion.json", "work/", "rounds.json"):
        assert path in text
    assert "review/PACK.md" in text and "review/verdict.json" in text
    assert "Code decides" in text


def test_my_files_hold_no_em_dashes():
    src = ROOT / "src" / "altocue"
    mine = [
        *(src / "qa").rglob("*.*"),
        *(src / "review").rglob("*.*"),
        ROOT / "agents" / "editor-in-chief.md",
        *ROOT.glob("tests/test_qa*.py"),
        *ROOT.glob("tests/test_review*.py"),
        ROOT / "tests" / "canaries.py",
    ]
    for path in mine:
        if path.suffix in {".py", ".md", ".toml", ".swift"}:
            assert chr(0x2014) not in path.read_text(), path
