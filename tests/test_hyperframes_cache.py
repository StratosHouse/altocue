"""A graphics layer is rendered once for what it shows. The renderer here is a stand-in: no test starts HyperFrames."""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from altocue import brand as brands
from altocue import motion, render
from altocue.errors import AltocueError
from altocue.motion import hyperframes
from altocue.motion.layer import Layer

LAYER = b"a finished layer " * 64


@pytest.fixture
def renders(tmp_path, monkeypatch):
    """Each call the stand-in renderer got. It writes a layer the way the real one does, or fails when told to."""
    calls: list[list[str]] = []
    state = SimpleNamespace(calls=calls, fails=False)
    home = tmp_path / "renderer"
    (home / "node_modules" / "hyperframes").mkdir(parents=True)
    (home / "package.json").write_text(json.dumps({"dependencies": {"hyperframes": "1.0.0"}}))
    (home / "node_modules" / "hyperframes" / "package.json").write_text(json.dumps({"version": "1.0.0"}))
    monkeypatch.setenv("ALTOCUE_RENDERER_DIR", str(home))

    @contextmanager
    def no_lock(label):
        yield

    def fake(cmd, **_):
        calls.append(cmd)
        output = cmd[cmd.index("--output") + 1]
        with open(output, "wb") as handle:
            handle.write(LAYER[:40] if state.fails else LAYER)
        return SimpleNamespace(returncode=1 if state.fails else 0, stdout="", stderr="the browser closed")

    monkeypatch.setattr(hyperframes, "heavy", no_lock)
    monkeypatch.setattr(hyperframes.subprocess, "run", fake)
    return state


def brand_in(folder, **colors):
    data = json.loads(brands.data_path("brands", "default.json").read_text())
    data["colors"].update(colors)
    folder.mkdir(exist_ok=True)
    shutil.copy(brands.data_path("fonts", data["font"]["file"]), folder / data["font"]["file"])
    return brands.Brand(data, folder)


def timeline(text="two words", **over):
    words = [{"text": word, "start": 0.5 + index * 0.4, "end": 0.9 + index * 0.4} for index, word in enumerate(text.split())]
    page = {"start": 0.5, "end": 1.5, "segment": "a", "words": words}
    return {"width": 1080, "height": 1920, "fps": 30, "duration": 8.0, "tail": 0, "total": 8.0, "captions": [page], "items": [], **over}


def draw(tmp_path, tl=None, brand=None, **options):
    return hyperframes.HyperFramesRenderer().render(tl or timeline(), brand or brand_in(tmp_path / "kit"), tmp_path / "layers", **options)


def test_an_unchanged_layer_is_not_rendered_again(tmp_path, renders, capsys):
    first = draw(tmp_path)
    assert len(renders.calls) == 1 and first.path.read_bytes() == LAYER
    again = draw(tmp_path)
    assert len(renders.calls) == 1
    assert again == first == Layer(path=tmp_path / "layers" / "overlay.mov", renderer="hyperframes")
    assert "the last layer is used" in capsys.readouterr().out
    assert (tmp_path / "layers" / "hyperframes" / "index.html").exists()  # the stage still shows what the layer was drawn from


@pytest.mark.parametrize(
    "change",
    [
        lambda tmp_path: {"tl": timeline("three other words")},
        lambda tmp_path: {"tl": timeline(width=1920, height=1080)},
        lambda tmp_path: {"tl": timeline(fps=25)},
        lambda tmp_path: {"tl": timeline(total=9.0)},
        lambda tmp_path: {"brand": brand_in(tmp_path / "kit", accent="#ff8800")},
    ],
    ids=["caption", "size", "fps", "length", "brand"],
)
def test_a_change_to_what_the_layer_shows_renders_it_again(tmp_path, renders, change):
    draw(tmp_path)
    draw(tmp_path, **change(tmp_path))
    assert len(renders.calls) == 2
    draw(tmp_path, **change(tmp_path))
    assert len(renders.calls) == 2


def test_a_changed_font_file_renders_the_layer_again(tmp_path, renders):
    """The page names the font and the stage holds it, so the same page can still draw differently."""
    brand = brand_in(tmp_path / "kit")
    draw(tmp_path, brand=brand)
    with brand.font_file.open("ab") as handle:
        handle.write(b"another cut of the face")
    draw(tmp_path, brand=brand)
    assert len(renders.calls) == 2


def test_a_new_renderer_version_renders_the_layer_again(tmp_path, renders):
    draw(tmp_path)
    installed = tmp_path / "renderer" / "node_modules" / "hyperframes" / "package.json"
    installed.write_text(json.dumps({"version": "1.0.1"}))
    assert hyperframes.renderer_version() == "1.0.1"
    draw(tmp_path)
    assert len(renders.calls) == 2
    installed.unlink()  # with no install to read, the pin still names the renderer
    assert hyperframes.renderer_version() == "1.0.0"


def test_a_draft_and_a_final_are_kept_apart(tmp_path, renders):
    final = draw(tmp_path)
    draft = draw(tmp_path, draft=True)
    assert len(renders.calls) == 2 and draft.path != final.path
    assert "draft" in renders.calls[1] and "delivery" in renders.calls[0]
    draw(tmp_path)
    draw(tmp_path, draft=True)
    assert len(renders.calls) == 2


@pytest.mark.parametrize("damage", ["truncated", "missing", "empty", "no note", "bad note"])
def test_a_damaged_layer_is_rendered_again(tmp_path, renders, damage):
    layer = draw(tmp_path).path
    note = layer.with_suffix(".json")
    if damage == "truncated":
        layer.write_bytes(LAYER[:100])
    elif damage == "missing":
        layer.unlink()
    elif damage == "empty":
        layer.write_bytes(b"")
    elif damage == "no note":
        note.unlink()
    else:
        note.write_text("{")
    assert draw(tmp_path).path.read_bytes() == LAYER
    assert len(renders.calls) == 2


def test_a_render_that_fails_leaves_nothing_to_reuse(tmp_path, renders):
    """The renderer writes over the last layer. If it stops halfway, the old key must not vouch for the new file."""
    draw(tmp_path)
    renders.fails = True
    with pytest.raises(AltocueError, match="the browser closed"):
        draw(tmp_path, tl=timeline("three other words"))
    assert not (tmp_path / "layers" / "overlay.json").exists()
    renders.fails = False
    assert draw(tmp_path).path.read_bytes() == LAYER  # the first edit again: its layer is gone, so it is drawn
    assert len(renders.calls) == 3


def test_the_page_and_the_key_do_not_change_between_two_builds(tmp_path, renders):
    one, two = tmp_path / "one", tmp_path / "two"
    for stage in (one, two):
        hyperframes.build_stage(stage, [], brand_in(tmp_path / "kit"), hyperframes.compose(timeline(), brand_in(tmp_path / "kit")))
    assert hyperframes.layer_key(one, fps=30) == hyperframes.layer_key(two, fps=30) != hyperframes.layer_key(two, fps=15)


def test_a_render_builds_the_timeline_once(tmp_path, monkeypatch):
    """It was built for the composite and again for the graphics, and each build looks for faces under every graphic."""
    from test_locks import small_project

    monkeypatch.setenv("HOME", str(tmp_path))
    project = small_project(tmp_path)
    built, drawn = [], []
    made = {"tail": 0, "items": [{"type": "chip", "text": "One"}], "inserts": [], "total": 1.0, "sfx": False}

    class Renderer:
        id = "basic"

        def render(self, timeline, brand, dest, *, draft=False):
            drawn.append(timeline)
            return Layer(tmp_path / "l.mov", "basic")

    monkeypatch.setattr(render.frame, "frame_project", lambda project: None)
    monkeypatch.setattr(render.plate, "build", lambda project, draft: (tmp_path / "p.mp4", tmp_path / "v.wav"))
    monkeypatch.setattr(render.caption, "build", lambda project: [])
    monkeypatch.setattr(render.timelines, "build", lambda project, **_: built.append(1) or made)
    monkeypatch.setattr(motion, "pick", lambda name: Renderer())
    monkeypatch.setattr(render.mix, "mix_bed", lambda *args, **kwargs: None)
    monkeypatch.setattr(render, "compose_args", lambda *args: [])
    monkeypatch.setattr(render.ffmpeg, "run", lambda args: None)
    render.build(project)
    assert built == [1]
    assert drawn == [made] and drawn[0] is not made and drawn[0]["items"][0] is not made["items"][0]  # the renderer has its own copy
    motion.render_layers(project)  # `altocue motion` has no timeline in hand, so it still builds one
    assert built == [1, 1]
