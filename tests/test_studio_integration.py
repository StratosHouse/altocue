"""The new pack must respect speech timing and real visible picture in QA."""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from altocue.models import Word
from altocue.motion import formats
from altocue.qa import gates
from altocue.transcribe import Envelope, tighten_start


def test_leading_pause_is_not_part_of_the_first_word():
    envelope = Envelope.of_levels(np.r_[np.full(30, -65.0), np.full(20, -18.0), np.full(100, -65.0)])
    fixed = tighten_start(Word("Here's", 0.0, 0.9), envelope)
    assert fixed.start == pytest.approx(0.48)
    assert fixed.end == 0.9 and fixed.text == "Here's"
    # The 120 ms before voiced energy remains, including soft consonants.
    assert 0.6 - fixed.start >= 0.119


@pytest.mark.parametrize("levels", [np.full(150, -30.0), np.r_[np.full(5, -65.0), np.full(145, -18.0)]])
def test_a_noisy_or_already_close_start_is_unchanged(levels):
    word = Word("Hello", 0.0, 0.9)
    assert tighten_start(word, Envelope.of_levels(levels)) == word


def test_row_reveals_follow_resolved_absolute_speech_times():
    from altocue.timeline import resolve_items

    items, _ = resolve_items(
        [{
            "type": "studio_depth_handoff", "title": "Roles", "start": 10, "duration": 6,
            "items": [{"text": "Plan", "at": {"word": "plan"}}, {"text": "Build", "at": {"word": "build"}}],
        }],
        [{"text": "plan", "start": 10.1, "end": 10.4}, {"text": "build", "start": 12.3, "end": 12.8}],
        20,
    )
    drawing = formats.find("studio_depth_handoff").draw(items[0])
    assert "animation-delay:0.1000s" in drawing
    assert "animation-delay:2.3000s" in drawing


def test_a_directed_film_heading_stays_above_the_picture():
    from altocue.motion.scenes import _place

    item = {"type": "hook", "text": "Same take. Cleaner edit.", "place": "left:120px;top:260px;width:660px", "start": 0, "end": 3}
    placed = _place(item, (1080, 1920))
    assert placed["place"] == item["place"]
    assert "zone_class" not in placed


def context(items):
    c = SimpleNamespace(file=Path("final.mp4"), footage_end=10, fps=60, timeline={"items": items}, planned=lambda *args: [], inserts=lambda: [])
    c.unplanned = lambda found, planned: gates.Context.unplanned(c, found, planned)
    return c


def test_only_declared_opaque_holds_can_explain_a_flat_graphic(monkeypatch):
    monkeypatch.setattr(gates, "frozen_runs", lambda *args: [(2, 3)])
    full = {"type": "studio_depth_handoff", "start": 1, "end": 4, "held": True}
    assert gates.gate_frozen(context([full])).passed
    assert not gates.gate_frozen(context([{**full, "held": False}])).passed
    assert not gates.gate_frozen(context([{**full, "place": "width:400px"}])).passed
    assert not gates.gate_frozen(context([{**full, "type": "typewriter"}])).passed
    assert not gates.gate_frozen(context([{**full, "start": 4, "end": 5}])).passed


@pytest.mark.parametrize("smaller", [False, True])
def test_face_qa_reads_the_visible_picture_for_a_full_replacement(monkeypatch, tmp_path, smaller):
    from PIL import Image

    item = {"type": "studio_depth_handoff", "start": 0, "end": 4}
    if smaller:
        item["place"] = "width:400px"
    c = context([item])
    c._seen = None
    c.clip = SimpleNamespace(duration=10)
    c.plate = Path("plate.mp4")
    monkeypatch.setattr(gates.rules, "on_screen", lambda line: [{"text": "Hello", "start": 0, "end": 4, "layer": "captions"}])
    monkeypatch.setattr(gates.ocr, "reader", lambda: ("test", ""))
    monkeypatch.setattr(gates.faces, "model_path", lambda: tmp_path / "face")

    def grab(file, at, dest, **kwargs):
        Image.new("RGB", (32, 32)).save(dest)
        return dest

    monkeypatch.setattr(gates, "grab", grab)
    monkeypatch.setattr(gates.ocr, "read_images", lambda paths: [{"file": str(p), "text": "Hello", "box": [0.1, 0.1, 0.2, 0.1]} for p in paths])
    monkeypatch.setattr(gates.faces, "find", lambda path: [[0.1, 0.1, 0.3, 0.3]] if path.name.startswith("plate_") else [])
    seen = gates.sightings(c)
    assert bool(seen["faces"][0]["boxes"]) is smaller


def test_every_builtin_has_an_offline_portable_prompt_with_its_valid_native_example():
    import json

    from altocue import library
    from altocue.motion import formats

    for row in library.builtins():
        example = json.loads(row["prompt"].split("Start from this valid example:\n", 1)[1])
        assert example["type"] == row["id"]
        formats.check(example)
        assert row["use_cases"] == row["jobs"]
        assert row["sound_notes"]
        assert "ocular" not in row["prompt"].lower()
        assert "do not fetch audio or spend automatically" in row["prompt"]


def test_adjacent_titles_never_share_the_sixty_fps_boundary_frame():
    import re
    from decimal import Decimal

    from altocue.motion.html import _attrs

    def interval(a, b):
        attrs = dict(re.findall(r'(data-[a-z-]+)="([^"]+)"', _attrs(a, b, 2)))
        start = Decimal(attrs["data-start"])
        return start, start + Decimal(attrs["data-duration"])

    for frame in [60, 75, 899, 1024, 1400]:
        boundary = frame / 60
        a = interval(11 / 60, boundary)
        b = interval(boundary, boundary + 2.5)
        assert a[1] < Decimal(frame) / 60
        assert b[0] <= Decimal(frame) / 60 < b[1]
        assert a[1] <= b[0]


def test_short_caption_word_does_not_expand_into_the_next_word():
    from altocue.motion.html import _attrs

    assert 'data-duration="0.024999"' in _attrs(1, 1.025, 1)


def test_explicit_empty_note_stays_empty_and_starter_note_is_counted():
    fmt = formats.find('studio_satin_title')
    assert fmt.example['note'] == 'Illustrative sample'
    assert 'Illustrative sample' not in fmt.draw({**fmt.example,'note':''})


def test_held_caption_cannot_survive_into_the_first_frame_of_the_next_shot():
    from altocue import caption, cut

    edl = SimpleNamespace(fps=60, segments=[SimpleNamespace(id='a', start=0, end=373 / 60)])
    ends = cut.segment_ends(edl)
    pages = caption.build_pages([{'text':'Slack','start':5.697,'end':6.097,'segment':'a'}],ends=ends)
    assert pages[-1]['end'] <= 373 / 60


def test_film_reading_hold_never_excuses_a_frozen_presenter_or_missing_speech():
    from altocue.qa.film import reading_holds, unplanned_stops

    line = {'scenes':[{'id':'a','start':0,'end':3,'reading_hold':True}], 'items':[{'scene':'a','type':'studio_save_card'}]}
    found = {'frozen':[{'from':1,'to':2.5,'to_end':False}]}
    assert not unplanned_stops(found,line,60)
    assert reading_holds(line,speechless=True)==[(0,3)]
    line['scenes'][0]['voice']='voice.wav'
    assert not reading_holds(line,speechless=True)
    line['scenes'][0].pop('voice')
    line['scenes'][0]['says']='Required speech'
    assert not reading_holds(line,speechless=True)
    line['scenes'][0].pop('says')
    line['items'].append({'scene':'a','type':'footage','file':'presenter.mp4'})
    assert unplanned_stops(found,line,60)==found['frozen']
    line['items'][-1]['file']='actual-grade-proof.png'
    assert not unplanned_stops(found,line,60)
    line['scenes'][0].pop('reading_hold')
    assert unplanned_stops(found,line,60)==found['frozen']
    line['scenes'][0]['reading_hold']=True
    line['backdrops']=[{'type':'footage','file':'background.mp4','start':0,'end':3}]
    assert unplanned_stops(found,line,60)==found['frozen']



def test_adjacent_speechless_graphic_holds_cover_the_whole_reading_stretch():
    from altocue.qa.film import reading_holds

    line = {'scenes':[{'id':'a','start':0,'end':3,'reading_hold':True}, {'id':'b','start':3,'end':6,'reading_hold':True}],
            'items':[{'scene':'a','type':'studio_approval_gate'}, {'scene':'b','type':'studio_save_card'}]}
    assert reading_holds(line, speechless=True) == [(0, 6)]
    c = context([])
    assert not gates.Context.unplanned(c, [(2.2, 3.8)], reading_holds(line, speechless=True))
    line['scenes'][1]['start'] = 3.1
    assert gates.Context.unplanned(c, [(2.2, 3.8)], reading_holds(line, speechless=True)) == [(2.2, 3.8)]


def test_joining_reading_holds_never_bridges_a_scene_that_requires_speech_or_video():
    from altocue.qa.film import reading_holds

    line = {'scenes':[{'id':'a','start':0,'end':3,'reading_hold':True}, {'id':'b','start':3,'end':6,'reading_hold':True}],
            'items':[{'scene':'a','type':'studio_approval_gate'}, {'scene':'b','type':'studio_save_card'}]}
    for key, value in [('says', 'Expected speech'), ('voice', 'voice.wav')]:
        line['scenes'][1][key] = value
        assert reading_holds(line, speechless=True) == [(0, 3)]
        line['scenes'][1].pop(key)
    line['items'].append({'scene':'b','type':'footage','file':'presenter.mp4'})
    assert reading_holds(line, speechless=True) == [(0, 3)]
