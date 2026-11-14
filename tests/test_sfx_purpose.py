"""Sound effects by what they are for: every bundled sound has a purpose, and `altocue sfx for` answers a moment in words."""

from __future__ import annotations

import json

import pytest

from altocue import cli, library, mix
from altocue.stock import sfxpacks


def table() -> dict[str, dict]:
    return {row["name"]: row for row in json.loads((mix.SFX_DIR / "sounds.json").read_text())}


def test_every_bundled_sound_has_a_family_and_a_known_purpose():
    """Adding a sound without saying what it is for fails here."""
    rows = table()
    assert set(rows) == set(mix.sfx_names())
    for name in mix.sfx_names():
        row = rows[name]
        assert row.get("family") in library.SOUND_FAMILIES, name
        assert row.get("purposes") and set(row["purposes"]) <= set(library.SOUND_PURPOSES), name
        assert isinstance(row.get("tuned"), bool), name
        assert {"onset_ms", "peak_ms", "end_ms"} <= set(row), name


def test_the_index_files_every_bundled_sound_under_a_purpose():
    index = library.sound_index()
    filed = {name for purpose in index["purposes"].values() for name in purpose["sounds"]}
    assert set(mix.sfx_names()) <= filed
    assert set(index["purposes"]) == set(library.SOUND_PURPOSES)


def test_every_pack_category_reads_as_a_family():
    for category, _ in [*sfxpacks.CATEGORIES, ("other", ())]:
        family, purposes = library.CATEGORY_SOUNDS[category]
        assert family in library.SOUND_FAMILIES and set(purposes) <= set(library.SOUND_PURPOSES), category


def test_a_pack_sound_takes_its_purposes_from_its_category_and_its_name():
    close = library.pack_sound({"id": "a-close-001", "category": "swipe", "tags": ["swipe", "ui", "close"], "seconds": 0.2})
    error = library.pack_sound({"id": "an-error-002", "category": "notification", "tags": ["notification", "error"], "seconds": 0.3})
    assert close["family"] == "whoosh" and close["align"] == "peak" and "text off" in close["purposes"] and "text on" not in close["purposes"]
    assert error["purposes"] == ["notification", "error"] and error["peak_ms"] is None


def test_a_card_sliding_in_gets_a_sound_that_slides_and_how_to_place_it():
    found = library.sounds_for("a card sliding in", {"entries": []})
    assert [row["purpose"] for row in found["purposes"]] == ["text on"]
    first = found["bundled"][0]
    assert first["family"] == "whoosh" and first["align"] == "peak" and first["peak_ms"] > 0
    assert found["cue"] == {"name": first["name"], "at": "<the frame it meets>", "sync": "picture"}


@pytest.mark.parametrize(
    ("words", "purpose", "family"),
    [("big logo reveal", "reveal", None), ("saved", "success", None), ("list step", "step", "click"), ("riser", "riser", "riser"), ("wrong answer", "error", "hit")],
)
def test_moments_read_as_their_purpose(words, purpose, family):
    found = library.sounds_for(words, {"entries": []})
    assert purpose in [row["purpose"] for row in found["purposes"]]
    assert found["bundled"] and (family is None or found["bundled"][0]["family"] == family)


def test_words_that_name_no_purpose_say_so():
    found = library.sounds_for("purple elephant", {"entries": []})
    assert found["purposes"] == [] and found["bundled"] == [] and found["cue"] is None


def test_the_cli_answers_in_json(capsys):
    assert cli.main(["sfx", "for", "card", "sliding", "in", "--json", "--limit", "3"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["purposes"][0]["purpose"] == "text on" and len(reply["open_packs"]) <= 3 and "open_packs_total" in reply
    assert all({"name", "family", "align", "seconds", "peak_ms", "purposes"} <= set(row) for row in reply["bundled"])


def test_the_cli_lists_the_whole_index(capsys):
    assert cli.main(["sfx", "for", "--json"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert set(reply["purposes"]) == set(library.SOUND_PURPOSES) and len(reply["sounds"]) >= len(mix.sfx_names())
    assert cli.main(["sfx", "for"]) == 0
    assert "riser" in capsys.readouterr().out
