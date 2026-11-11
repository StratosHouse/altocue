"""The weekly sweep report: what moved upstream, measured against the pins and against what a person last read."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from altocue.errors import AltocueError

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sweep = load("sweep")

PAGES = {
    "https://pypi.org/pypi/inside/json": {"info": {"version": "1.4.0", "license_expression": "MIT"}, "urls": [{"upload_time_iso_8601": "2026-10-01T00:00:00Z"}]},
    "https://pypi.org/pypi/beyond/json": {"info": {"version": "2.0.0", "license": "MIT"}, "urls": []},
    "https://pypi.org/pypi/relicensed/json": {"info": {"version": "1.0.0", "license_expression": "AGPL-3.0-only"}, "urls": []},
    "https://pypi.org/pypi/wordy/json": {
        "info": {"version": "1.0.0", "license": "Permission is hereby granted, free of charge, to any person obtaining a copy"},
        "urls": [],
    },
    "https://registry.npmjs.org/renderer/latest": {"version": "0.9.9", "license": "Apache-2.0"},
    "https://api.github.com/repos/someone/tool/releases/latest": {"tag_name": "v3.1.0", "published_at": "2026-11-02T10:00:00Z"},
    "https://api.github.com/repos/someone/tool/license": {"license": {"spdx_id": "MIT"}},
    "https://api.github.com/repos/someone/tagged/tags?per_page=1": [{"name": "n8.0"}],
}


def entry(name: str, where: str = "pypi", role: str = "used", **more: str) -> dict[str, str]:
    return {"name": name, "where": where, "id": more.pop("id", name), "role": role, "why": "a test", **more}


def run(*entries: dict[str, str]) -> dict[str, dict[str, str]]:
    rows = sweep.sweep(list(entries), PAGES.get, python={"inside": (2,), "beyond": (2,)}, node={"renderer": "0.9.8"})
    return {row["name"]: row for row in rows}


def test_a_release_inside_the_pin_is_current():
    row = run(entry("inside"))["inside"]
    assert (row["status"], row["latest"], row["date"], row["against"]) == ("current", "1.4.0", "2026-10-01", "under 2")


def test_a_release_past_the_ceiling_is_outside_the_pin():
    assert run(entry("beyond"))["beyond"]["status"] == "outside the pin"


def test_an_exact_pin_is_measured_exactly():
    rows = run(entry("renderer", "npm"), entry("shaders", "npm", id="renderer", pin="0.9.9"))
    assert rows["renderer"]["status"] == "outside the pin" and rows["renderer"]["against"] == "pinned 0.9.8"
    assert rows["shaders"]["status"] == "current"


def test_a_candidate_is_measured_against_what_a_person_last_read():
    rows = run(entry("read", "github", "candidate", id="someone/tool", seen="3.1.0"), entry("unread", "github", "candidate", id="someone/tool"))
    assert rows["read"]["status"] == "current" and rows["read"]["date"] == "2026-11-02"
    assert rows["unread"]["status"] == "new" and rows["unread"]["against"] == "never reviewed"


def test_a_repository_with_no_releases_falls_back_to_its_newest_tag():
    assert run(entry("tagged", "github", id="someone/tagged"))["tagged"]["latest"] == "n8.0"


def test_a_changed_licence_comes_first():
    rows = sweep.sweep([entry("inside"), entry("relicensed", licence="MIT")], PAGES.get, python={}, node={})
    assert [row["status"] for row in rows][0] == "licence changed" and rows[0]["against"] == "was MIT"


def test_a_licence_text_is_not_compared_as_an_id():
    assert run(entry("wordy", role="candidate", licence="MIT", seen="1.0.0"))["wordy"]["status"] == "current"


def test_what_cannot_be_reached_is_said_and_never_called_current():
    def down(url: str):
        raise AltocueError("no network")

    row = sweep.sweep([entry("inside")], down, python={}, node={})[0]
    assert row["status"] == "unreachable"
    assert "1 could not be reached" in sweep.report([row], "2026-11-16")


def test_the_watch_list_is_well_formed_and_matches_the_pins():
    entries = sweep.load()
    names = [item["name"] for item in entries]
    assert len(names) == len(set(names)) and len(names) >= 15
    python, node = sweep.python_pins(), sweep.node_pins()
    assert python["parakeet-mlx"] and node["hyperframes"]
    shaders = next(item for item in entries if item["name"] == "gl-transitions")
    assert shaders["pin"] == load("build_library").SHADER_VERSION


def test_a_watch_entry_with_no_reason_is_refused(tmp_path):
    path = tmp_path / "watch.toml"
    path.write_text('[[watch]]\nname = "x"\nwhere = "pypi"\nid = "x"\nrole = "used"\n')
    with pytest.raises(AltocueError, match="why"):
        sweep.load(path)
