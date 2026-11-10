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
