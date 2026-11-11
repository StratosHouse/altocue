"""The brand kit comes first: nothing makes an asset until the project has read one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from altocue import brand as brands
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.project import Project


def blank(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "p")
    brands.set_brand(project, "")
    return Project.open(project.root)


def test_a_new_project_starts_with_no_brand():

    shipped = (Path(brands.__file__).parent / "project.py").read_text()
    assert 'name = ""               # empty until `altocue brand ingest`' in shipped


def test_no_asset_before_the_kit(tmp_path):
    project = blank(tmp_path)
    with pytest.raises(AltocueError, match="no brand kit yet"):
        brands.load(project)
    assert main(["run", "--project", str(project.root)]) != 0


def kit(tmp_path: Path) -> Path:
    folder = tmp_path / "Acme kit"
    (folder / "fonts").mkdir(parents=True)
    (folder / "colours.txt").write_text("Primary #0B1F3A\nAccent #FF5A1F\nLight #F7F4EE\nPanel #13294B\nMuted #C9C3B8\n")
    (folder / "logo-main.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    shutil_font = Path(brands.data_path("fonts", "Geist-Variable.ttf"))
    (folder / "fonts" / "Acme-Bold.ttf").write_bytes(shutil_font.read_bytes())
    return folder


def test_a_kit_folder_becomes_the_project_brand(tmp_path):
    project = blank(tmp_path)
    done = brands.ingest(project, kit(tmp_path))
    brand = brands.load(Project.open(project.root))
    assert done["brand"] == "brand/brand.json"
    assert brand.colors["panel"] == "#0b1f3a"
    assert brand.colors["accent"] == "#ff5a1f"
    assert brand.colors["ink"] == "#f7f4ee"
    assert brand.colors["surface"] == "#13294b"
    assert brand.data["font"]["family"] == "Acme"
    assert brand.font_file == project.root / "brand" / "Acme-Bold.ttf"
    assert brand.logo == project.root / "brand" / "logo-main.png"
    assert 'name = "brand/brand.json"' in (project.root / "altocue.toml").read_text()


def test_a_brand_json_is_taken_as_written_and_a_thin_kit_says_what_it_kept(tmp_path):
    project = blank(tmp_path)
    folder = tmp_path / "json-kit"
    folder.mkdir()
    (folder / "brand.json").write_text(json.dumps({"name": "Zed", "colors": {"accent": "#123456"}}))
    done = brands.ingest(project, folder / "brand.json")
    assert brands.load(Project.open(project.root)).colors["accent"] == "#123456"
    assert "face" in done["kept"] and "logo" in done["kept"]


def test_the_house_look_is_a_choice_and_new_can_read_the_kit(tmp_path, clip):
    project = blank(tmp_path)
    assert main(["brand", "ingest", "--house", "--project", str(project.root)]) == 0
    assert brands.load(Project.open(project.root)).data["name"] == "Altocue"
    folder = kit(tmp_path)
    assert main(["new", str(tmp_path / "q"), "--input", str(clip), "--brand", str(folder)]) == 0
    assert brands.load(Project.open(tmp_path / "q")).colors["accent"] == "#ff5a1f"


def test_new_names_both_ways_past_the_gate(tmp_path, clip, capsys, monkeypatch):
    from altocue import project as projects

    monkeypatch.setattr(projects, "DEFAULT_CONFIG", projects.DEFAULT_CONFIG.replace('name = "default"        #', 'name = ""               #', 1))
    assert main(["new", str(tmp_path / "first"), "--input", str(clip)]) == 0
    out = capsys.readouterr().out
    assert "altocue brand ingest <kit folder>" in out and "altocue brand ingest --house" in out and "--brand house" in out
    assert f"-p {tmp_path / 'first'}" in out


def test_a_render_stops_on_the_gate_before_any_work(tmp_path, monkeypatch, capsys):
    from altocue import render

    project = blank(tmp_path)
    worked = []
    monkeypatch.setattr(render.frame, "frame_project", lambda project: worked.append("frame"))
    monkeypatch.setattr(render.plate, "build", lambda project, draft: worked.append("plate"))
    with pytest.raises(AltocueError, match="no brand kit yet"):
        render.build(project, draft=True)
    for command in (["render", "--draft"], ["caption"], ["motion"], ["run"]):
        assert main([*command, "--project", str(project.root)]) == 1
        said = capsys.readouterr().err
        assert "no brand kit yet" in said and "altocue brand ingest --house" in said
    assert worked == []


def test_status_has_a_brand_row_and_next_never_points_past_it(tmp_path, capsys):
    from altocue import status

    project = blank(tmp_path)
    stages = status.collect(project)
    assert (stages[0].name, stages[0].state) == ("brand", "not run")
    assert "--house" in stages[0].detail
    assert status.next_command(stages, ".") == "altocue brand ingest <kit folder> -p ."
    brands.ingest(project, None, house=True)
    stages = status.collect(Project.open(project.root))
    assert (stages[0].state, stages[0].detail) == ("done", "the house look, chosen on purpose")
    assert "brand" not in status.next_command(stages, ".")


def test_the_house_look_message_names_no_file_that_was_not_written(tmp_path, capsys):
    project = blank(tmp_path)
    assert main(["brand", "ingest", "--house", "--project", str(project.root)]) == 0
    out = capsys.readouterr().out
    assert "house look, chosen on purpose" in out and "brand/brand.json" not in out
    assert not (project.root / "brand").exists()


def test_a_kit_that_is_not_there_says_what_to_give(tmp_path):
    project = blank(tmp_path)
    with pytest.raises(AltocueError) as caught:
        brands.ingest(project, tmp_path / "nowhere")
    assert caught.value.fix and "house" in caught.value.fix


def test_the_cut_ends_by_saying_the_brand_comes_before_a_run(tmp_path, capsys, monkeypatch):
    from altocue import cut
    from altocue.models import Edl, Segment

    project = blank(tmp_path)
    edl = Edl(fps=30, segments=[Segment(id="s001", clip="a", start=0.0, end=1.0)])
    monkeypatch.setattr(cut, "run", lambda args: print("Next: `altocue run` rebuilds the film from this cut.") or project.save_edl(edl) and 0)
    assert main(["cut", "--project", str(project.root)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("Next: `altocue run`")
    assert "no brand yet" in out[1] and "altocue brand ingest --house" in out[1] and "<kit folder>" in out[1]
    brands.ingest(project, None, house=True)
    assert main(["cut", "--project", str(project.root)]) == 0
    assert "no brand yet" not in capsys.readouterr().out
