"""Learning from a creator's own results: one creator, matched posts, and nothing claimed too soon."""

from __future__ import annotations

import csv
import json
import os
from datetime import date, timedelta

import numpy as np
import pytest
from canaries import Case, Recipe, build, planted

from altocue import facts, learn, qa
from altocue.cli import main
from altocue.errors import AltocueError
from altocue.project import write_json
from altocue.review import pack

TODAY = date(2027, 6, 1)
START = date(2026, 9, 1)
SECRET = "SECRETNOTEXYZ"


def snapshot(key, published, *, age=10, duration=45.0, platform="youtube", **metrics):
    return {
        "platform": platform,
        "key": key,
        "published": published.isoformat(),
        "exported": (published + timedelta(days=age)).isoformat(),
        "age_days": age,
        "duration_s": duration,
        "metrics": metrics,
    }


def film(key, *, platform="youtube", **values):
    return {"platform": platform, "key": key, "facts": values}


def world(count, fact, metric, *, name="first_word_s", lead=6, platform="youtube", outcome="stayed_to_watch"):
    """`lead` posts with no film build the baseline. The rest are films with one fact and one outcome."""
    snaps, films = [], []
    for i in range(lead):
        snaps.append(snapshot(f"old{i}", START + timedelta(days=i), platform=platform, **{outcome: 50.0}))
    for i in range(count):
        key = f"v{i}"
        snaps.append(snapshot(key, START + timedelta(days=lead + i), platform=platform, **{outcome: float(metric(i))}))
        films.append(film(key, platform=platform, **{name: float(fact(i))}))
    return films, snaps


def linked(count, **kwargs):
    """A strong, clean relationship, so only the sample rules can hold it back."""
    return world(count, lambda i: i % 7, lambda i: 30 + 6 * (i % 7), **kwargs)


# Reading exports


def write_rows(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


STUDIO = ["Content", "Video title", "Video publish time", "Duration", "Views", "Average percentage viewed (%)", "Average view duration"]
META = ["Post ID", "Permalink", "Description", "Publish time", "Duration (sec)", "Views", "Reach", "Shares", "Skip rate"]


def test_a_studio_export_reads_and_its_total_row_is_dropped(tmp_path):
    path = write_rows(
        tmp_path / "Table data.csv",
        STUDIO,
        [["Total", "", "", "", "12,000", "", ""], ["abc123", "A film", "Oct 14, 2026", "47", "1,200", "61.5", "0:00:29"]],
    )
    rows, skipped = learn.read_export(path, exported=date(2026, 11, 1))
    assert skipped == 0 and len(rows) == 1
    row = rows[0]
    assert row["platform"] == "youtube" and row["key"] == "abc123" and row["age_days"] == 18
    assert row["duration_s"] == 47.0
    assert row["metrics"] == {"views": 1200.0, "average_percentage_viewed": 61.5, "average_view_duration": 29.0}


def test_a_meta_export_reads_with_its_byte_order_mark(tmp_path):
    path = write_rows(
        tmp_path / "meta.csv",
        META,
        [["111", "https://www.instagram.com/reel/AbC_1/", "A reel", "10/14/2026 18:30", "20", "900", "600", "30", "22.5%"]],
    )
    rows, _ = learn.read_export(path, exported=date(2026, 11, 1))
    assert rows[0]["platform"] == "meta" and rows[0]["key"] == "AbC_1"
    assert rows[0]["metrics"]["skip_rate"] == 22.5 and rows[0]["metrics"]["shares_per_reach"] == 0.05


def test_one_odd_address_does_not_cost_the_whole_export(tmp_path):
    rows = [
        ["1", "https://example.com/elsewhere", "x", "2026-10-01", "20", "9", "6", "3", "1"],
        ["2", "https://www.instagram.com/reel/Good1/", "x", "2026-10-01", "20", "9", "6", "3", "1"],
    ]
    found, skipped = learn.read_export(write_rows(tmp_path / "m.csv", META, rows), exported=TODAY)
    assert [row["key"] for row in found] == ["Good1"] and skipped == 1


def test_an_export_nobody_recognises_names_its_columns(tmp_path):
    path = write_rows(tmp_path / "odd.csv", ["Thing", "Other"], [["1", "2"]])
    with pytest.raises(AltocueError) as error:
        learn.read_export(path, exported=TODAY)
    assert "Thing" in error.value.fix and "--map" in error.value.fix


def test_a_column_can_be_mapped_by_hand(tmp_path):
    path = write_rows(tmp_path / "x.csv", ["Content", "Video publish time", "Duration", "Plays"], [["a", "2026-10-01", "40", "70"]])
    rows, _ = learn.read_export(path, exported=TODAY, mapping={"Plays": "views"})
    assert rows[0]["metrics"]["views"] == 70.0


@pytest.mark.parametrize(
    ("url", "platform", "key"),
    [
        ("https://www.youtube.com/watch?v=abc123&t=4", "youtube", "abc123"),
        ("https://youtu.be/abc123", "youtube", "abc123"),
        ("https://www.youtube.com/shorts/abc123", "youtube", "abc123"),
        ("https://www.instagram.com/reel/AbC_1/?igsh=x", "meta", "AbC_1"),
        ("https://www.instagram.com/p/AbC_1/", "meta", "AbC_1"),
        ("https://www.facebook.com/reel/1234567", "meta", "1234567"),
    ],
)
def test_post_addresses_match_however_they_were_copied(url, platform, key):
    assert learn.post_key(url) == (platform, key)


# Normalising


def normalised(snaps, metric="stayed_to_watch", age=7):
    return learn.normalised(learn.chosen(snaps, age)["youtube"], metric)


def test_each_post_is_held_against_its_creators_earlier_median():
    snaps = [snapshot(f"a{i}", START + timedelta(days=i), stayed_to_watch=50.0) for i in range(5)]
    snaps.append(snapshot("hit", START + timedelta(days=9), stayed_to_watch=100.0))
    assert normalised(snaps) == {"hit": pytest.approx(1.0)}


def test_later_posts_never_enter_a_baseline():
    snaps = [snapshot(f"a{i}", START + timedelta(days=i), stayed_to_watch=50.0) for i in range(6)]
    before = normalised(snaps)
    after = normalised([*snaps, snapshot("later", START + timedelta(days=30), stayed_to_watch=5000.0)])
    assert after["a5"] == before["a5"]


def test_length_is_matched():
    snaps = [snapshot(f"a{i}", START + timedelta(days=i), duration=8.0, stayed_to_watch=90.0) for i in range(5)]
    snaps.append(snapshot("long", START + timedelta(days=9), duration=45.0, stayed_to_watch=50.0))
    assert "long" not in normalised(snaps)


def test_age_is_matched():
    snaps = [snapshot(f"a{i}", START + timedelta(days=i), age=200, stayed_to_watch=90.0) for i in range(5)]
    snaps.append(snapshot("young", START + timedelta(days=9), age=10, stayed_to_watch=50.0))
    assert "young" not in normalised(snaps)


def test_each_post_uses_its_nearest_snapshot_at_or_past_the_chosen_age():
    snaps = [
        snapshot("a", START, age=3, views=1.0),
        snapshot("a", START, age=8, views=2.0),
        snapshot("a", START, age=40, views=3.0),
    ]
    assert learn.chosen(snaps, 7)["youtube"][0]["metrics"]["views"] == 2.0


def test_a_post_younger_than_the_chosen_age_waits():
    assert learn.chosen([snapshot("a", START, age=3, views=1.0)], 7) == {}


def test_youtube_baselines_never_span_a_view_change():
    early = [snapshot(f"a{i}", date(2026, 7, 1) + timedelta(days=i), stayed_to_watch=90.0) for i in range(6)]
    late = snapshot("new", date(2026, 9, 20), stayed_to_watch=50.0)
    assert "new" not in normalised([*early, late])


def test_meta_has_no_youtube_break():
    early = [snapshot(f"a{i}", date(2026, 7, 1) + timedelta(days=i), platform="meta", skip_rate=20.0) for i in range(6)]
    late = snapshot("new", date(2026, 9, 20), platform="meta", skip_rate=40.0)
    assert learn.normalised(learn.chosen([*early, late], 7)["meta"], "skip_rate")["new"] == pytest.approx(1.0)


# What may be said, and when


def run(films, snaps, pairs=()):
    return learn.analyse(films, snaps, list(pairs), age=7, today=TODAY)


def text_of(result):
    return learn.notes_text(result, TODAY)


def test_under_twenty_posts_shows_no_pattern_however_strong():
    result = run(*linked(19))
    assert result["findings"] == []
    text = text_of(result)
    assert "Too few to learn from" in text and "19" in text and "Higher" not in text


def test_twenty_posts_may_show_a_pattern_but_no_suggestion():
    result = run(*linked(20))
    assert result["findings"] and not any(f["suggest"] for f in result["findings"])
    text = text_of(result)
    assert "time to first word" in text.lower() and "To test it" not in text


def test_thirty_posts_may_suggest_a_paired_test():
    result = run(*linked(30))
    assert all(f["suggest"] for f in result["findings"])
    assert "To test it" in text_of(result)


def test_every_note_carries_size_interval_date_and_expiry():
    finding = run(*linked(30))["findings"][0]
    assert finding["posts"] == 30 and finding["low"] < finding["rho"] < finding["high"]
    text = text_of(run(*linked(30)))
    assert "30 posts" in text and "95% interval" in text and "2027-06-01" in text and "2027-08-30" in text


def test_a_film_missing_a_fact_does_not_count_towards_it():
    films, snaps = linked(25)
    for entry in films[:6]:
        entry["facts"]["first_word_s"] = None
    assert run(films, snaps)["findings"] == []


def test_noise_gives_no_pattern():
    for seed in range(5):
        rng = np.random.default_rng(seed)
        draws, outcome = {n: rng.normal(size=40) for n in learn.TESTED}, rng.normal(size=40)
        snaps = [snapshot(f"o{i}", START + timedelta(days=i), stayed_to_watch=50.0) for i in range(6)]
        films = []
        for i in range(40):
            snaps.append(snapshot(f"v{i}", START + timedelta(days=6 + i), stayed_to_watch=float(50 * 2 ** (outcome[i] / 2))))
            films.append(film(f"v{i}", **{name: float(values[i]) for name, values in draws.items()}))
        result = run(films, snaps)
        assert result["findings"] == [], seed
        assert len(result["tests"]) == len(learn.TESTED)
        assert "No reliable pattern yet" in text_of(result)


def test_a_chance_result_does_not_survive_correction():
    """Eleven tests on noise: a raw p under 0.05 turns up often, and must not become a note."""
    for seed in range(40):
        rng = np.random.default_rng(seed)
        draws, outcome = {n: rng.normal(size=30) for n in learn.TESTED}, rng.normal(size=30)
        snaps = [snapshot(f"o{i}", START + timedelta(days=i), stayed_to_watch=50.0) for i in range(6)]
        films = []
        for i in range(30):
            snaps.append(snapshot(f"v{i}", START + timedelta(days=6 + i), stayed_to_watch=float(50 * 2 ** (outcome[i] / 2))))
            films.append(film(f"v{i}", **{name: float(values[i]) for name, values in draws.items()}))
        result = run(films, snaps)
        chance = [t for t in result["tests"] if t["p"] < 0.05]
        if chance and not result["findings"]:
            assert all(t["adjusted_p"] > 0.05 for t in chance)
            return
    pytest.fail("no seed gave a chance result")


def test_at_most_five_notes():
    films, snaps = world(35, lambda i: i, lambda i: 30 + i)
    for entry in films:
        entry["facts"] = {name: entry["facts"]["first_word_s"] + index for index, name in enumerate(learn.TESTED)}
    result = run(films, snaps)
    assert len(result["findings"]) == learn.MOST_NOTES


def test_creators_are_never_pooled_across_platforms():
    youtube = world(12, lambda i: i, lambda i: 30 + i)
    meta = world(12, lambda i: i, lambda i: 30 + i, platform="meta", outcome="skip_rate")
    result = run([*youtube[0], *meta[0]], [*youtube[1], *meta[1]])
    assert result["findings"] == [] and {p["platform"] for p in result["platforms"]} == {"youtube", "meta"}
    assert all(p["comparable"] == 12 for p in result["platforms"])


@pytest.mark.parametrize("word", ["caused", "causes", "because of", "predict", "will perform", "proven", "virality", "viral", "verified"])
def test_notes_never_claim_too_much(word):
    pairs = [learn.new_pair(f"ch{i}", "text first", "a", "b", TODAY) for i in range(1)]
    text = text_of(run(*linked(30), pairs)).lower()
    text = text.replace("none of it predicts the next one", "")
    assert word not in text


def test_the_notes_say_who_they_are_for():
    text = text_of(run(*linked(30)))
    assert "Never show this file to the Editor in Chief" in text and "Association, not proof" in text


# Paired tests


def paired_world(pairs, a_better=True, order_swaps=True):
    films, snaps, logged = [], [], []
    for i in range(pairs):
        day = START + timedelta(days=i * 2)
        high, low = (40.0, 20.0) if a_better else (20.0, 40.0)
        first, second = (day, day + timedelta(days=1)) if (i % 2 or not order_swaps) else (day + timedelta(days=1), day)
        snaps.append(snapshot(f"a{i}", first, platform="meta", skip_rate=high))
        snaps.append(snapshot(f"b{i}", second, platform="meta", skip_rate=low))
        logged.append(learn.new_pair(f"p{i}", "text in the first second", f"a{i}", f"b{i}", TODAY, platform="meta"))
    return films, snaps, logged


def test_too_few_pairs_says_nothing():
    films, snaps, logged = paired_world(5)
    row = run(films, snaps, logged)["pairs"][0]
    assert row["pairs"] == 5 and row["clear"] is False
    assert "Too few pairs" in text_of(run(films, snaps, logged))


def test_enough_pairs_in_one_direction_is_reported_as_a_paired_result():
    films, snaps, logged = paired_world(7)
    result = run(films, snaps, logged)
    row = result["pairs"][0]
    assert row["pairs"] == 7 and row["a_higher"] == 7 and row["clear"] is True
    text = text_of(result)
    assert "paired" in text.lower() and "says nothing about views" in text


def test_a_split_result_is_not_clear():
    films, snaps, logged = paired_world(8)
    for snap in snaps[:8]:
        if snap["key"].startswith("a"):
            snap["metrics"]["skip_rate"] = 10.0
    assert run(films, snaps, logged)["pairs"][0]["clear"] is False


def test_a_pair_needs_both_posts_at_a_matching_age():
    films, snaps, logged = paired_world(6)
    snaps[0]["age_days"] = 300
    assert run(films, snaps, logged)["pairs"][0]["pairs"] == 5


def test_different_changes_are_not_pooled():
    films, snaps, logged = paired_world(6)
    logged[0]["change"] = "a different change"
    assert {row["change"] for row in run(films, snaps, logged)["pairs"]} == {"a different change", "text in the first second"}


def test_posting_a_first_every_time_is_called_out():
    films, snaps, logged = paired_world(7, order_swaps=False)
    assert "went up first every time" in text_of(run(films, snaps, logged))


# Notes expire


def test_notes_are_live_for_ninety_days_then_gone(tmp_path):
    path = tmp_path / learn.NOTES
    path.write_text(text_of(run(*linked(30))))
    assert learn.live_notes(path, TODAY + timedelta(days=90))
    assert learn.live_notes(path, TODAY + timedelta(days=91)) is None
    assert not path.exists()


def test_a_notes_file_with_no_date_is_not_trusted(tmp_path):
    path = tmp_path / learn.NOTES
    path.write_text("# Notes\nDo this.\n")
    assert learn.live_notes(path, TODAY) is None
    assert path.exists(), "a file Altocue did not date may be someone's own"


# The Editor in Chief stays blind


def test_notes_never_reach_the_editor_in_chief(tmp_path):
    item = build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path)
    (item.project.root / learn.NOTES).write_text(SECRET)
    write_json(item.project.root / "results" / "pairs.json", {"note": SECRET})
    with planted(item):
        gates = qa.run_gates(item.project, item.file)
        info = pack.build(item.project, item.file, gates)
    path = pack.write_prompt(item.project, info)
    shown = path.read_text() + "".join(f.read_text() for f in (item.project.review_dir / "pack").rglob("*") if f.suffix in {".json", ".txt"})
    assert SECRET not in shown and learn.NOTES in pack.FORBIDDEN


# The command


@pytest.fixture
def creator(tmp_path):
    folder = tmp_path / "creator"
    folder.mkdir()
    return folder


@pytest.fixture
def published(creator):
    item = build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), creator / "first-film")
    with planted(item):
        facts.write(item.project, facts.measure(item.project, item.file))
    return item


def post(creator, item, url="https://www.youtube.com/shorts/abc123"):
    return main(["learn", "post", str(item.project.root), "--url", url, "-p", str(creator)])


def test_posting_copies_the_facts_into_the_creators_results(creator, published):
    assert post(creator, published) == 0
    line = json.loads((creator / "results" / "films.jsonl").read_text().splitlines()[0])
    assert line["platform"] == "youtube" and line["key"] == "abc123" and line["sha256"] == qa.sha256(published.file)
    assert line["facts"]["first_word_s"] == 0.0 and set(line["facts"]) == set(learn.TESTED)


def test_posting_the_same_address_twice_replaces_it(creator, published):
    post(creator, published)
    post(creator, published)
    assert len((creator / "results" / "films.jsonl").read_text().splitlines()) == 1


def test_a_film_with_no_facts_is_turned_back(creator, tmp_path, capsys):
    item = build(Case("t", "", Recipe(burn_captions=False, seconds=3.0)), tmp_path / "x")
    assert post(creator, item) == 1
    assert "altocue facts" in capsys.readouterr().err


def test_facts_from_an_older_render_are_turned_back(creator, published, capsys):
    saved = json.loads((published.project.review_dir / "facts.json").read_text())
    write_json(published.project.review_dir / "facts.json", {**saved, "sha256": "old"})
    assert post(creator, published) == 1
    assert "another render" in capsys.readouterr().err
    write_json(published.project.review_dir / "facts.json", saved)


def test_facts_from_a_draft_are_turned_back(creator, published, capsys):
    saved = json.loads((published.project.review_dir / "facts.json").read_text())
    write_json(published.project.review_dir / "facts.json", {**saved, "stage": "draft"})
    assert post(creator, published) == 1
    assert "draft" in capsys.readouterr().err
    write_json(published.project.review_dir / "facts.json", saved)


def test_a_film_folder_is_not_a_creator_folder(published, capsys):
    root = str(published.project.root)
    assert main(["learn", "post", root, "--url", "https://youtu.be/abc", "-p", root]) == 1
    assert "creator" in capsys.readouterr().err


def test_import_matches_a_film_and_writes_notes(creator, published, capsys):
    post(creator, published)
    export = write_rows(creator / "export.csv", STUDIO, [["abc123", "A film", "Oct 14, 2026", "47", "1,200", "61.5", "0:00:29"]])
    assert main(["learn", "import", str(export), "-p", str(creator), "--exported", "2026-11-01"]) == 0
    out = capsys.readouterr().out
    assert "1 rows" in out and "1 films matched" in out
    assert "Too few to learn from" in (creator / learn.NOTES).read_text()


def test_importing_one_export_twice_changes_nothing(creator):
    export = write_rows(creator / "export.csv", STUDIO, [["abc123", "A film", "Oct 14, 2026", "47", "1,200", "61.5", "0:00:29"]])
    for _ in range(2):
        main(["learn", "import", str(export), "-p", str(creator), "--exported", "2026-11-01"])
    assert len((creator / "results" / "ledger.jsonl").read_text().splitlines()) == 1


def test_export_date_defaults_to_the_file_date(creator):
    export = write_rows(creator / "export.csv", STUDIO, [["abc123", "A film", "2026-10-01", "47", "1,200", "61.5", "0:00:29"]])
    when = TODAY.toordinal() - date(1970, 1, 1).toordinal()
    os.utime(export, (when * 86400 + 43200, when * 86400 + 43200))
    main(["learn", "import", str(export), "-p", str(creator)])
    assert json.loads((creator / "results" / "ledger.jsonl").read_text())["exported"] == TODAY.isoformat()


def test_logging_a_pair(creator):
    assert main(["learn", "pair", "--change", "text first", "--a", "https://youtu.be/one", "--b", "https://youtu.be/two", "-p", str(creator)]) == 0
    line = json.loads((creator / "results" / "pairs.jsonl").read_text())
    assert line["change"] == "text first" and line["a"] == "one" and line["b"] == "two" and line["platform"] == "youtube"


def test_a_pair_on_two_platforms_is_refused(creator, capsys):
    assert main(["learn", "pair", "--change", "x", "--a", "https://youtu.be/one", "--b", "https://www.instagram.com/reel/two/", "-p", str(creator)]) == 1
    assert "same platform" in capsys.readouterr().err


def test_notes_command_prints_only_live_notes(creator, capsys):
    (creator / learn.NOTES).write_text(text_of(run(*linked(30))))
    assert main(["learn", "notes", "-p", str(creator), "--as-of", TODAY.isoformat()]) == 0
    assert "Director notes" in capsys.readouterr().out
    assert main(["learn", "notes", "-p", str(creator), "--as-of", (TODAY + timedelta(days=100)).isoformat()]) == 0
    assert "No live notes" in capsys.readouterr().out


def test_the_bare_command_runs_the_analysis(creator, capsys):
    assert main(["learn", "-p", str(creator)]) == 0
    assert (creator / learn.NOTES).exists()


def test_tested_facts_are_all_measured():
    assert set(learn.TESTED) <= set(facts.NAMES)
