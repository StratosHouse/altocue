"""The recipe catalog: hosted commands in our own words, each with its cost and what leaves the machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from altocue.cli import main
from altocue.errors import AltocueError
from altocue.generate import GenRequest, load_catalog, recipes
from altocue.motion import formats
from altocue.project import Project

REEL = ("text-behind", "freeze-frame", "over-the-shoulder", "tier-list", "glass-graph", "captions", "grade-wipe")
ASKED_FOR = (*REEL, "ugc-video", "ad-multiplier", "marketing-studio", "motion-transfer", "upscale", "reframe", "dubbing", "3d-scene")
TRAPPED = ("ugc-video", "ad-multiplier", "faceless-video")


def good(**changes) -> dict:
    base = {
        "id": "demo",
        "title": "Demo",
        "group": "tool",
        "command": "demo_tool",
        "makes": "A demo.",
        "when": "When a demo helps.",
        "inputs": ["a clip"],
        "enters_at": "source",
        "tool": "demo_tool",
        "uploads_footage": False,
        "sends": "nothing",
        "basis": "unknown",
        "as_of": "2026-11-02",
        "fallback": "none",
    }
    return {**base, **changes}


def write_catalog(tmp_path: Path, *rows: dict) -> Path:
    path = tmp_path / "recipes.toml"
    lines = ["schema = 1"]
    for row in rows:
        lines.append("[[recipe]]")
        lines += [f"{key} = {json.dumps(value)}" for key, value in row.items()]
    path.write_text("\n".join(lines) + "\n")
    return path


def say(capsys, *args: str) -> tuple[int, str, str]:
    code = main(["recipes", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# The catalog


def test_packaged_recipes_load():
    assert len(recipes.load_recipes()) >= 30


@pytest.mark.parametrize("name", ASKED_FOR)
def test_every_command_asked_for_has_a_recipe(name):
    assert name in recipes.load_recipes()


def test_every_reel_effect_is_in_the_catalog_as_a_preset():
    catalog = recipes.load_recipes()
    for name in REEL:
        assert catalog[name].group == "reel"
        assert catalog[name].loader == "get_preset_instructions"
        assert catalog[name].load == f"/{name}"


@pytest.mark.parametrize("recipe", recipes.load_recipes().values(), ids=lambda r: r.id)
def test_every_recipe_states_the_whole_record(recipe):
    assert recipe.title and recipe.makes.endswith(".") and recipe.when.endswith(".")
    assert recipe.inputs
    assert recipe.command and recipe.tool
    assert recipe.sends
    assert recipe.basis in recipes.BASES
    assert recipe.as_of
    assert recipe.fallback
    if recipe.basis == "unknown":
        assert not recipe.priced
    else:
        assert recipe.priced


def test_a_catalog_is_refused_when_a_field_is_missing(tmp_path):
    row = good()
    del row["sends"]
    with pytest.raises(AltocueError, match="sends"):
        recipes.load_recipes(write_catalog(tmp_path, row))


def test_a_catalog_is_refused_when_a_price_has_no_basis(tmp_path):
    with pytest.raises(AltocueError, match="basis"):
        recipes.load_recipes(write_catalog(tmp_path, good(basis="vibes")))


def test_a_catalog_is_refused_when_unknown_carries_a_number(tmp_path):
    with pytest.raises(AltocueError, match="unknown"):
        recipes.load_recipes(write_catalog(tmp_path, good(fixed_credits=3)))


def test_a_catalog_is_refused_when_a_workflow_loads_by_slash(tmp_path):
    row = good(loader="get_workflow_instructions", load="/ugc-video")
    with pytest.raises(AltocueError, match="by name"):
        recipes.load_recipes(write_catalog(tmp_path, row))


def test_a_catalog_is_refused_on_a_repeated_id(tmp_path):
    with pytest.raises(AltocueError, match="twice"):
        recipes.load_recipes(write_catalog(tmp_path, good(), good()))


def test_a_catalog_is_refused_on_a_strange_fallback(tmp_path):
    with pytest.raises(AltocueError, match="fallback"):
        recipes.load_recipes(write_catalog(tmp_path, good(fallback="pray")))


def test_every_format_fallback_exists():
    named = [r.fallback.split(":", 1)[1] for r in recipes.load_recipes().values() if r.fallback.startswith("format:")]
    assert all(formats.find(name) for name in named), named


def test_every_fal_fallback_was_checked_against_its_page():
    for recipe in recipes.load_recipes().values():
        if recipe.fallback.startswith("fal:"):
            assert recipe.fallback_checked, recipe.id


# The trap


def test_workflows_load_by_name_and_never_by_slash():
    workflows = [r for r in recipes.load_recipes().values() if r.loader == "get_workflow_instructions"]
    assert workflows
    assert all(not r.load.startswith("/") for r in workflows)


@pytest.mark.parametrize("name", TRAPPED)
def test_the_slash_trap_is_recorded_and_spoken(capsys, name):
    recipe = recipes.load_recipes()[name]
    assert recipe.slash_trap and recipe.load == name
    code, out, _ = say(capsys, "show", name)
    assert code == 0
    assert "get_workflow_instructions" in out
    assert f"/{name}" in out and "truth gate" in out.lower()


# Commands


def test_list_shows_cost_and_upload(capsys):
    code, out, _ = say(capsys, "list")
    assert code == 0
    assert "text-behind" in out and "ugc-video" in out
    assert "uploads" in out


def test_list_hides_what_is_excluded_unless_asked(capsys):
    _, out, _ = say(capsys, "list")
    assert "katana" not in out and "sync-agents" not in out
    _, out, _ = say(capsys, "list", "--all")
    assert "katana" in out and "sync-agents" in out


def test_list_by_group(capsys):
    _, out, _ = say(capsys, "list", "--group", "reel")
    assert "tier-list" in out and "ugc-video" not in out


def test_list_as_json(capsys):
    _, out, _ = say(capsys, "list", "--json")
    rows = json.loads(out)
    assert {"id", "group", "uploads_footage", "basis"} <= set(rows[0])


def test_search_finds_by_meaning_words(capsys):
    code, out, _ = say(capsys, "search", "language")
    assert code == 0 and "dubbing" in out


def test_search_with_no_match_says_so(capsys):
    code, out, _ = say(capsys, "search", "xylophone")
    assert code == 1 and "Nothing" in out


def test_show_prints_the_whole_record(capsys):
    code, out, _ = say(capsys, "show", "reframe")
    assert code == 0
    for word in ("Makes", "Inputs", "Cost", "Load", "Footage", "Without Higgsfield"):
        assert word in out


def test_show_an_unknown_id_suggests_the_list(capsys):
    code, _, err = say(capsys, "show", "ghost")
    assert code == 1 and "altocue recipes list" in err


def test_show_needs_an_id(capsys):
    code, _, err = say(capsys, "show")
    assert code == 1 and "id" in err


def test_show_excluded_gives_the_reason(capsys):
    _, out, _ = say(capsys, "show", "katana")
    assert "consent" in out.lower() and "excluded" in out.lower()


# Quotes


def test_quote_prints_credits_dollars_basis_date_and_says_estimate(capsys):
    code, out, _ = say(capsys, "quote", "ugc-video", "--seconds", "15", "--resolution", "1080p")
    assert code == 0
    assert "189.12 credits" in out
    assert "$15.13" in out
    assert "2026-10-08" in out
    assert "estimate" in out.lower()


def test_quote_uses_the_projects_credit_rate(capsys, tmp_path):
    project = Project.create(tmp_path / "video")
    text = (project.root / "altocue.toml").read_text().replace("usd_per_credit = 0.08", "usd_per_credit = 0.033")
    (project.root / "altocue.toml").write_text(text)
    _, out, _ = say(capsys, "quote", "ugc-video", "--seconds", "15", "--resolution", "1080p", "--project", str(project.root))
    assert "$6.24" in out


def test_quote_of_an_unknown_price_says_unknown_and_never_a_number(capsys):
    code, out, _ = say(capsys, "quote", "dubbing")
    assert code == 0
    assert "unknown" in out.lower()
    assert "$" not in out


def test_quote_of_a_free_effect_is_zero_and_states_its_basis(capsys):
    _, out, _ = say(capsys, "quote", "tier-list")
    assert "0 credits" in out and "$0.00" in out
    assert "not a live quote" in out


def test_quote_of_a_matte_effect_says_how_little_is_known(capsys):
    _, out, _ = say(capsys, "quote", "text-behind")
    assert "1 credit" in out and "observed" in out.lower()
    assert "60" in out


def test_quote_per_second_without_a_length_lists_the_rates(capsys):
    code, out, _ = say(capsys, "quote", "reframe")
    assert code == 0
    assert "480p" in out and "1080p" in out and "--seconds" in out


def test_quote_refuses_a_resolution_it_has_no_rate_for(capsys):
    code, _, err = say(capsys, "quote", "ugc-video", "--seconds", "15", "--resolution", "8k")
    assert code == 1 and "1080p" in err


def test_quote_scales_by_count(capsys):
    _, out, _ = say(capsys, "quote", "product-photoshoot", "--count", "5")
    assert "10 credits" in out


def test_quote_gives_the_reservation_to_run_next(capsys):
    _, out, _ = say(capsys, "quote", "ugc-video", "--seconds", "15", "--resolution", "1080p")
    assert "--reserve" in out and "--credits 190" in out and "--recipe ugc-video" in out


def test_quote_refuses_what_is_excluded(capsys):
    code, _, err = say(capsys, "quote", "katana")
    assert code == 1 and "excluded" in err.lower()


def test_quote_needs_an_id(capsys):
    code, _, err = say(capsys, "quote")
    assert code == 1 and "id" in err


# models.toml


def test_higgsfield_terms_are_current():
    assert load_catalog().provider("higgsfield")["terms_as_of"] == "2026-10-23"


@pytest.mark.parametrize(("tier", "resolution", "credits"), [("draft", "480p", 3), ("standard", "720p", 7), ("hero", "1080p", 16)])
@pytest.mark.parametrize("capability", ["text_to_video", "image_to_video", "reference_to_video", "frame_and_refs_to_video"])
def test_seedance_2_5_has_an_mcp_route(tier, resolution, credits, capability):
    entry = load_catalog().find("higgsfield", tier, capability, surface="mcp")
    assert entry and entry.model == "seedance_2_5"
    assert entry.params["resolution"] == resolution
    assert entry.credits_per_second == credits
    assert (entry.min_duration, entry.max_duration) == (4, 30)
    assert entry.params["mode"] == ("t2v" if capability == "text_to_video" else "omni_reference")


def test_a_run_never_picks_a_route_only_an_agent_can_drive():
    catalog = load_catalog()
    assert catalog.find("higgsfield", "hero", "text_to_video").model == "seedance_2_0"
    request = GenRequest(kind="video", tier="draft", prompt="p", aspect="9:16", duration_s=20)
    with pytest.raises(AltocueError) as caught:
        catalog.resolve("higgsfield", request)
    assert "seedance_2_5" not in str(caught.value)
