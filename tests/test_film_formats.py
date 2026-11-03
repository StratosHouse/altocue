"""Three pieces the feature film needs: a take's real cut on a track, a wall of real stock, and a badge for each feature."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from altocue import cut, live
from altocue.errors import AltocueError
from altocue.models import Clip, Edl, Removed, Segment, Word
from altocue.motion import formats, scenes
from altocue.project import Project
from altocue.transcribe import classify

REPO = Path(__file__).resolve().parents[1]

# A take with a filler, a doubled phrase, a retake cue and a long pause, cut the way the cut stage writes it.
SAID = [
    ("So", 0.0, 0.3),
    ("um", 0.4, 0.6),
    ("this", 0.7, 0.9),
    ("is", 0.9, 1.1),
    ("this", 1.1, 1.3),
    ("is", 1.3, 1.5),
    ("it.", 1.5, 1.9),
    ("Wait,", 2.2, 2.5),
    ("redo.", 2.5, 2.9),
    ("This", 4.2, 4.5),
    ("is", 4.5, 4.7),
    ("it.", 4.7, 5.2),
]
CUT = Edl(
    fps=30,
    segments=[Segment(id="s001", clip="a", start=4.1, end=5.3)],
    removed=[
        Removed(clip="a", start=0.0, end=2.2, kind="retake", evidence="line before a spoken cue"),
        Removed(clip="a", start=0.4, end=0.6, kind="filler", evidence='filler: "um"'),
        Removed(clip="a", start=0.7, end=1.1, kind="stutter", evidence='phrase said twice: "this is this is"'),
        Removed(clip="a", start=2.2, end=2.9, kind="cue", evidence='spoken cue: "Wait, redo."'),
        Removed(clip="a", start=2.9, end=4.2, kind="silence", evidence="long pause: 1.30 s"),
    ],
)


@pytest.fixture
def take(tmp_path: Path) -> Project:
    project = Project.create(tmp_path / "take")
    project.save_clips([Clip(id="a", path=str(tmp_path / "a.mp4"), duration=5.6, fps=30, width=1080, height=1920, has_audio=False)])
    project.save_words("a", classify([Word(text=text, start=start, end=end) for text, start, end in SAID]), "test")
    project.save_edl(CUT)
    return project


def track_item(**over) -> dict:
    found = cut.track(Project.open(over.pop("root"))) if "root" in over else None
    made = {
        "type": "timeline_cut",
        "title": "Raw take",
        "before": "5.6 s",
        "after": "1.2 s",
        "words": found["words"] if found else [{"text": text, "start": start, "end": end} for text, start, end in SAID],
        "kept": found["kept"] if found else [{"start": 4.1, "end": 5.3}],
        "removed": found["removed"] if found else [r.to_dict() for r in CUT.removed],
        "raw": 5.6,
        "start": 0.0,
        "end": 4.0,
    }
    return {**made, **over}


# timeline_cut


def test_a_cut_track_is_the_real_words_and_the_real_cut_of_a_project(take):
    found = cut.track(take)
    assert found["clip"] == "a" and found["raw"] == pytest.approx(5.6)
    assert [w["text"] for w in found["words"]] == [text for text, _, _ in SAID]
    assert found["kept"] == [{"start": 4.1, "end": 5.3}]
    assert [r["kind"] for r in found["removed"]] == ["retake", "filler", "stutter", "cue", "silence"]
    assert found["kept_seconds"] == pytest.approx(1.2) and found["removals"] == 5
    assert {"um": "filler", "Wait,": "cue"}.items() <= {w["text"]: w["kind"] for w in found["words"]}.items()


def test_a_cut_track_names_a_clip_the_cut_never_used(take):
    with pytest.raises(AltocueError, match="b"):
        cut.track(take, clip="b")


def test_removed_words_strike_as_the_playhead_reaches_them_then_fold_away(take):
    drawn = formats.find("timeline_cut").draw(track_item(root=take.root))
    struck = re.findall(r'<span class="w x[^"]*" style="--s:([\d.]+)s;--c:([\d.]+)s"><u></u>([^<]+)</span>', drawn)
    kept = re.findall(r'<span class="w">([^<]+)</span>', drawn)
    assert [text.strip() for *_, text in struck] == ["So", "um", "this", "is", "this", "is", "it.", "Wait,", "redo."]
    assert [text.strip() for text in kept] == ["This", "is", "it."]
    times = [float(at) for at, _, _ in struck]
    assert times == sorted(times) and len(set(times)) > 1
    assert all(float(fold) > max(times) for _, fold, _ in struck)  # every word is struck before any folds


def test_each_removal_is_tagged_with_its_kind_and_a_pause_shows_where_it_was(take):
    drawn = formats.find("timeline_cut").draw(track_item(root=take.root))
    tags = re.findall(r'<em class="tg[^"]*"[^>]*>([^<]+)</em>', drawn)
    assert tags == ["retake", "filler", "stutter", "retake", "cue", "pause"]
    assert drawn.index(">pause<") > drawn.index(">redo.") and drawn.index(">pause<") < drawn.index(">This")


def test_the_bar_folds_down_to_the_share_the_cut_keeps(take):
    drawn = formats.find("timeline_cut").draw(track_item(root=take.root))
    kept = [float(w) for w in re.findall(r'<i class="sg k" style="--w:([\d.]+)%', drawn)]
    gone = [float(w) for w in re.findall(r'<i class="sg x" style="--w:([\d.]+)%', drawn)]
    assert sum(kept) == pytest.approx(1.2 / 5.6 * 100, abs=0.1)
    assert sum(kept) + sum(gone) == pytest.approx(100, abs=0.1)
    assert "5.6 s" in drawn and "1.2 s" in drawn and 'class="ph"' in drawn


def test_a_long_take_sets_smaller_type_so_it_still_fits():
    short = formats.find("timeline_cut").draw(track_item())
    words = [{"text": "wordy" * 2, "start": i * 0.3, "end": i * 0.3 + 0.2} for i in range(48)]
    long = formats.find("timeline_cut").draw(track_item(words=words, removed=[], kept=[{"start": 0, "end": 15}], raw=15))
    size = lambda html: float(re.search(r"--fs:([\d.]+)", html).group(1))  # noqa: E731
    assert size(long) < size(short)


def test_a_live_number_can_be_read_from_a_cut(take, tmp_path):
    root = tmp_path / "film"
    root.mkdir()
    numbers = {name: {"source": {"cut": str(take.root), "read": name}, "format": ".1f"} for name in ("raw", "kept")}
    numbers["removals"] = {"source": {"cut": str(take.root), "read": "removals"}}
    board = {"scenes": [{"id": "a", "items": [{"type": "say_line", "text": "{live:raw} s to {live:kept} s, {live:removals} cuts"}]}]}
    filled, record = live.fill(board, numbers, root)
    assert filled["scenes"][0]["items"][0]["text"] == "5.6 s to 1.2 s, 5 cuts"
    assert live.check(root, filled, record) == []
    take.save_edl(Edl(fps=30, segments=[Segment(id="s001", clip="a", start=3.9, end=5.3)], removed=CUT.removed[:4]))
    assert live.check(root, filled, record)


def test_an_unknown_cut_reading_says_what_can_be_read(take, tmp_path):
    with pytest.raises(AltocueError, match="raw"):
        live.value({"cut": str(take.root), "read": "words"}, tmp_path)


# feature_badge


def test_a_badge_draws_its_icon_and_names_the_feature():
    fmt = formats.find("feature_badge")
    drawn = fmt.draw({"icon": "scissors", "text": "Cutting", "start": 0, "end": 3})
    assert "<svg" in drawn and "<path" in drawn and ">Cutting<" in drawn
    assert fmt.group == "film" and fmt.area == "full"


def test_every_badge_icon_is_drawn_in_code_or_carries_its_licence():
    from altocue.motion.formats import film

    icons = film.badge_icons()
    assert {"scissors", "cursor-text", "waveform", "palette", "badge-cc", "crop", "movie", "sparkles", "music", "rocket", "clip-cut"} <= set(icons)
    assert {"checks", "rubber-stamp", "package"} <= set(icons)
    folder = REPO / "src" / "altocue" / "data" / "icons"
    notice = (folder / "LICENSE").read_text()
    assert "MIT License" in notice and "Paweł Kuna" in notice
    assert "Tabler Icons" in (REPO / "LICENSES.md").read_text() and "src/altocue/data/icons/LICENSE" in (REPO / "LICENSES.md").read_text()
    shipped = json.loads((folder / "tabler.json").read_text())
    assert shipped["version"] and shipped["licence"] == "MIT"


def test_an_icon_outside_the_set_is_refused_before_the_render():
    with pytest.raises(AltocueError, match="icon"):
        formats.check({"type": "feature_badge", "icon": "unicorn", "text": "Magic"})


def test_a_badge_holds_still_over_the_camera_and_keeps_to_its_corner():
    assert "feature_badge" in scenes.STEADY
    drawn = formats.find("feature_badge").draw({"icon": "crop", "text": "Reframe", "corner": "tr", "start": 0, "end": 3})
    assert 'class="badge tr"' in drawn


# library_scroll


def wall(count: int = 6, **over) -> dict:
    items = [{"image": f"wall/{index}.jpg", "text": "Pixabay licence"} for index in range(count)]
    return {"type": "library_scroll", "query": "ocean drone", "items": items, "start": 0.0, "end": 3.0, **over}


def test_the_search_types_itself_then_the_results_scroll_past():
    drawn = formats.find("library_scroll").draw(wall())
    keys = [float(at) for at in re.findall(r'<b class="k" style="--t:([\d.]+)s">', drawn)]
    assert len(keys) == len("ocean drone") and keys == sorted(keys)
    reveal = float(re.search(r'class="wall" style="--t:([\d.]+)s', drawn).group(1))
    assert reveal > keys[-1]
    assert {f"wall/{index}.jpg" for index in range(6)} == set(re.findall(r'<img src="([^"]+)"', drawn))


def test_every_frame_in_the_wall_carries_its_licence():
    drawn = formats.find("library_scroll").draw(wall())
    tiles = drawn.count('<figure class="tile"')
    assert tiles >= 6 and drawn.count(">Pixabay licence</figcaption>") == tiles == drawn.count("<img")
    assert formats.find("library_scroll").group == "product"


def test_a_wall_with_no_frames_is_an_error():
    with pytest.raises(AltocueError, match="items"):
        formats.check({"type": "library_scroll", "query": "ocean drone", "items": []})


def test_a_wall_can_show_pictures_with_no_search_typed():
    """The colour beat shows every look on one take, named, with nothing searched."""
    drawn = formats.find("library_scroll").draw({**wall(), "query": ""})
    assert 'class="sb"' not in drawn and 'class="ls bare"' in drawn
    assert float(re.search(r'class="wall" style="--t:([\d.]+)s', drawn).group(1)) < 0.2


def test_a_recut_starts_from_the_cut_before_it():
    """Edit the script and the take is cut again: only what the earlier cut kept is on the track, and only the new removal strikes."""
    item = track_item(
        base=[{"start": 4.1, "end": 5.3}],
        kept=[{"start": 4.1, "end": 4.75}],
        removed=[*[r.to_dict() for r in CUT.removed], {"start": 4.75, "end": 5.3, "kind": "manual"}],
        before="1.2 s",
        after="0.6 s",
        raw=None,
    )
    drawn = formats.find("timeline_cut").draw(item)
    struck = [text.strip() for text in re.findall(r'<span class="w x[^"]*" style="[^"]*"><u></u>([^<]+)</span>', drawn)]
    kept = [text.strip() for text in re.findall(r'<span class="w">([^<]+)</span>', drawn)]
    assert kept == ["This", "is"] and struck == ["it."]
    assert re.findall(r'<em class="tg[^"]*"[^>]*>([^<]+)</em>', drawn) == ["cut"]
    widths = [float(w) for w in re.findall(r'<i class="sg [kx]" style="--w:([\d.]+)%', drawn)]
    assert sum(widths) == pytest.approx(100, abs=0.1) and widths[0] == pytest.approx(0.65 / 1.2 * 100, abs=0.5)
