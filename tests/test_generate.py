"""No real provider is called here. fal is a local HTTP server, Higgsfield a local script."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import make_clip

import altocue.generate as generate
from altocue.cli import main
from altocue.direct import Shot
from altocue.errors import AltocueError
from altocue.generate import (
    CAPABILITIES,
    TIERS,
    GenProvider,
    GenRequest,
    capability_of,
    estimate,
    generated_asset,
    get_provider,
    import_asset,
    load_catalog,
)
from altocue.generate.fal import FalProvider
from altocue.generate.higgsfield import HiggsfieldProvider
from altocue.project import Project

PACKAGE = Path(generate.__file__).parent
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
PROMPT = "She walks to the door, the coat swinging. Slow dolly in, ending on her hands."


def shot(**changes) -> dict:
    base = {
        "id": "s01",
        "purpose": "Show the door",
        "kind": "video",
        "tier": "draft",
        "prompt": PROMPT,
        "aspect": "9:16",
        "duration": 5,
        "placement": 2,
        "check": "none",
    }
    return {**base, **changes}


def still(**changes) -> dict:
    return shot(kind="image", check="none", prompt="A brass lantern on a wet stone step, warm light.", **changes)


def shots_of(*dicts: dict) -> list[Shot]:
    return [Shot.from_dict(d) for d in dicts]


class FakeFal:
    """Speaks the queue, storage and CDN paths Altocue uses, and records every call."""

    def __init__(self, media: bytes):
        self.media = media
        self.calls: list[dict] = []
        self.statuses: list[str] = ["IN_QUEUE", "IN_PROGRESS", "COMPLETED"]
        self.result_code = 200
        self.result_body: dict | None = None
        self.status_extra: dict = {}
        self.status_codes: list[int] = []
        self.submit_code = 200
        self.submit_body: dict | None = None
        self.on_status = None
        self.media_failures = 0
        self.requests: dict[str, int] = {}
        self.uploads: dict[str, bytes] = {}
        self.count = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: bytes = b"", kind: str = "application/json", headers: dict | None = None):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, data: dict, headers: dict | None = None):
                self._send(code, json.dumps(data).encode(), headers=headers)

            def _record(self, body: bytes = b"") -> dict:
                entry = {"method": self.command, "path": self.path, "auth": self.headers.get("Authorization"), "body": body}
                fake.calls.append(entry)
                return entry

            def _read(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_POST(self):
                body = self._read()
                self._record(body)
                base = f"http://{self.headers['Host']}"
                if self.path.startswith("/storage/upload/initiate"):
                    name = json.loads(body)["file_name"]
                    return self._json(200, {"upload_url": f"{base}/put/{name}", "file_url": f"{base}/cdn/{name}"})
                if fake.submit_code != 200:
                    return self._json(fake.submit_code, fake.submit_body or {"detail": "rejected"})
                fake.count += 1
                request_id = f"req-{fake.count}"
                fake.requests[request_id] = 0
                parts = self.path.strip("/").split("/")
                app = "/".join(parts[:2])
                root = f"{base}/{app}/requests/{request_id}"
                return self._json(
                    200,
                    {
                        "request_id": request_id,
                        "response_url": root,
                        "status_url": f"{root}/status",
                        "cancel_url": f"{root}/cancel",
                        "queue_position": 0,
                    },
                )

            def do_PUT(self):
                body = self._read()
                self._record(body)
                if self.path.startswith("/put/"):
                    fake.uploads[self.path.removeprefix("/put/")] = body
                    return self._send(200)
                return self._json(202, {"status": "CANCELLATION_REQUESTED"})

            def do_GET(self):
                self._record()
                path = urlsplit(self.path).path
                if path == "/redirect":
                    return self._send(302, headers={"Location": "/media/moved.mp4"})
                if path.startswith("/media/"):
                    if fake.media_failures:
                        fake.media_failures -= 1
                        return self._send(500)
                    return self._send(200, fake.media, "video/mp4")
                match = re.fullmatch(r"/[^/]+/[^/]+/requests/(req-\d+)(/status)?", path)
                if not match:
                    return self._json(404, {"detail": "no route"})
                request_id, is_status = match.groups()
                if is_status:
                    if fake.on_status:
                        fake.on_status(request_id)
                    if fake.status_codes:
                        return self._json(fake.status_codes.pop(0), {"detail": "busy"})
                    index = fake.requests[request_id]
                    fake.requests[request_id] = index + 1
                    state = fake.statuses[min(index, len(fake.statuses) - 1)]
                    return self._json(200, {"status": state, "request_id": request_id, **fake.status_extra})
                if fake.result_code != 200:
                    return self._json(fake.result_code, fake.result_body or {})
                base = f"http://{self.headers['Host']}"
                body = fake.result_body or {"video": {"url": f"{base}/media/{request_id}.mp4"}, "seed": 4242}
                return self._json(200, body, headers={"X-Fal-Billable-Units": "108"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def seen(self, method: str, fragment: str = "") -> list[dict]:
        return [c for c in self.calls if c["method"] == method and fragment in c["path"]]

    @property
    def submits(self) -> list[dict]:
        return [c for c in self.seen("POST") if "/storage/" not in c["path"]]


@pytest.fixture(scope="module")
def clip_bytes(tmp_path_factory) -> bytes:
    return make_clip(tmp_path_factory.mktemp("media") / "out.mp4", seconds=1.0, size="160x284", audio=False).read_bytes()


@pytest.fixture
def fal(clip_bytes):
    server = FakeFal(clip_bytes)
    yield server
    server.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr("altocue.generate.runner.sleep", lambda seconds: None)
    monkeypatch.setattr("altocue.generate.http.sleep", lambda seconds: None)
    monkeypatch.setenv("FAL_KEY", "test-key")


def configure(project: Project, *, provider: str = "fal", cap: float = 100.0, fal_url: str | None = None) -> Project:
    text = (project.root / "altocue.toml").read_text()
    text = text.replace('provider = ""', f'provider = "{provider}"').replace("spend_cap_usd = 0.0", f"spend_cap_usd = {cap}")
    if fal_url:
        catalog = (PACKAGE / "models.toml").read_text()
        catalog = catalog.replace("https://queue.fal.run", fal_url).replace("https://rest.fal.ai", fal_url)
        (project.root / "models.local.toml").write_text(catalog)
        text = text.replace("[generate]\n", '[generate]\nmodels = "models.local.toml"\n')
    (project.root / "altocue.toml").write_text(text)
    return Project.open(project.root)


@pytest.fixture
def project(tmp_path: Path, fal: FakeFal) -> Project:
    return configure(Project.create(tmp_path / "video"), fal_url=fal.url)


def write_shots(project: Project, *dicts: dict) -> None:
    (project.root / "shots.json").write_text(json.dumps({"shots": list(dicts)}))


def go(project: Project, *args: str) -> int:
    return main(["generate", "--project", str(project.root), *args])


def jobs(project: Project) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((project.work / "jobs").glob("*.json"))]


def spend(project: Project) -> list[dict]:
    path = project.work / "spend.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# The catalog


def test_packaged_catalog_loads_and_covers_every_tier():
    catalog = load_catalog()
    for tier in TIERS:
        for capability in ("text_to_video", "image_to_video", "reference_to_video", "text_to_image", "reference_to_image"):
            assert catalog.find("fal", tier, capability), (tier, capability)
    assert catalog.find("higgsfield", "standard", "frame_and_refs_to_video")


def test_every_catalog_entry_is_dated_and_cited():
    for entry in load_catalog().entries:
        assert entry.as_of == "2026-10-30", entry.model
        assert entry.source_url.startswith("https://"), entry.model
        assert entry.capability in CAPABILITIES
        assert entry.tier in TIERS
        if entry.provider == "fal":
            assert entry.usd_per_image or entry.usd_per_second, entry.model


def test_providers_name_their_terms():
    catalog = load_catalog()
    assert catalog.provider("fal")["terms_url"] == "https://fal.ai/terms"
    assert catalog.provider("higgsfield")["terms_url"].startswith("https://higgsfield.ai")


def test_no_model_id_lives_in_code():
    ids = {entry.model for entry in load_catalog().entries}
    code = [*PACKAGE.glob("*.py"), PACKAGE.parent / "direct.py"]
    assert ids and code
    for path in code:
        text = path.read_text()
        assert not [model for model in ids if model in text], path


def test_broken_catalog_is_refused(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text('[[model]]\nprovider = "fal"\ntier = "ultra"\n')
    with pytest.raises(AltocueError) as caught:
        load_catalog(bad)
    assert caught.value.fix


def test_duplicate_catalog_routes_are_refused(tmp_path):
    text = (PACKAGE / "models.toml").read_text()
    first = text[text.index("[[model]]") :]
    first = first[: first.index("[[model]]", 5)]
    bad = tmp_path / "dup.toml"
    bad.write_text(text + "\n" + first)
    with pytest.raises(AltocueError, match="twice"):
        load_catalog(bad)


def test_capability_follows_the_request():
    base = {"tier": "draft", "prompt": "p", "aspect": "9:16", "duration_s": 5}
    assert capability_of(GenRequest(kind="video", **base)) == "text_to_video"
    assert capability_of(GenRequest(kind="video", first_frame=Path("a"), **base)) == "image_to_video"
    assert capability_of(GenRequest(kind="video", refs=[Path("a")], **base)) == "reference_to_video"
    assert capability_of(GenRequest(kind="video", first_frame=Path("a"), refs=[Path("b")], **base)) == "frame_and_refs_to_video"
    assert capability_of(GenRequest(kind="image", **base)) == "text_to_image"
    assert capability_of(GenRequest(kind="image", refs=[Path("a")], **base)) == "reference_to_image"


def test_missing_capability_is_an_error_with_a_suggestion_never_a_downgrade():
    request = GenRequest(kind="video", tier="draft", prompt="p", aspect="9:16", duration_s=5, first_frame=Path("a"), refs=[Path("b")])
    with pytest.raises(AltocueError) as caught:
        load_catalog().resolve("fal", request)
    assert "higgsfield" in caught.value.fix


def test_nothing_anywhere_has_the_capability(tmp_path):
    catalog = load_catalog()
    catalog.entries[:] = [e for e in catalog.entries if e.capability != "reference_to_image"]
    request = GenRequest(kind="image", tier="draft", prompt="p", aspect="9:16", duration_s=5, refs=[Path("b")])
    with pytest.raises(AltocueError) as caught:
        catalog.resolve("fal", request)
    assert "models.toml" in caught.value.fix


def test_a_missing_route_never_falls_through_to_another_model():
    catalog = load_catalog()
    catalog.entries.remove(catalog.find("fal", "draft", "text_to_video"))
    with pytest.raises(AltocueError, match="no draft route"):
        catalog.resolve("fal", GenRequest(kind="video", tier="draft", prompt="p", aspect="9:16", duration_s=5))


@pytest.mark.parametrize(
    ("change", "word"),
    [
        ({"duration_s": 2}, "duration"),
        ({"duration_s": 40}, "duration"),
        ({"aspect": "4:5"}, "aspect"),
        ({"refs": [Path(str(n)) for n in range(10)]}, "reference"),
    ],
)
def test_requests_outside_a_models_limits_are_errors(change, word):
    fields = {"kind": "video", "tier": "draft", "prompt": "p", "aspect": "9:16", "duration_s": 5, **change}
    with pytest.raises(AltocueError) as caught:
        load_catalog().resolve("fal", GenRequest(**fields))
    assert word in str(caught.value)
    assert caught.value.fix


def test_audio_on_a_silent_only_route_is_refused():
    catalog = load_catalog()
    entry = catalog.find("fal", "draft", "text_to_video")
    catalog.entries[catalog.entries.index(entry)] = entry.without_field("audio")
    with pytest.raises(AltocueError, match="audio"):
        catalog.resolve("fal", GenRequest(kind="video", tier="draft", prompt="p", aspect="9:16", duration_s=5, audio=True))


# Estimates


def test_estimate_prices_seconds_and_images():
    cost = estimate(shots_of(shot(), shot(id="s02", tier="hero", duration=10), still(id="s03", tier="standard")), "fal")
    assert [round(line.usd, 4) for line in cost.lines] == [0.7735, 6.82, 0.12]
    assert cost.usd == pytest.approx(7.7135)
    assert cost.unpriced == []
    assert "bytedance/seedance-2.0/mini/text-to-video" in cost.lines[0].model


def test_estimate_uses_the_audio_rate_when_there_is_one():
    catalog = load_catalog()
    entry = catalog.find("fal", "draft", "text_to_video")
    catalog.entries[catalog.entries.index(entry)] = entry.with_price(usd_per_second=0.1, usd_per_second_audio=0.3)
    assert estimate(shots_of(shot(audio=True)), "fal", catalog=catalog).usd == pytest.approx(1.5)
    assert estimate(shots_of(shot(audio=False)), "fal", catalog=catalog).usd == pytest.approx(0.5)


def test_estimate_refuses_an_impossible_shot():
    with pytest.raises(AltocueError):
        estimate(shots_of(shot(first_frame="a.png", refs=["b.png"])), "fal")


def test_providers_satisfy_the_protocol():
    catalog = load_catalog()
    assert isinstance(FalProvider(catalog), GenProvider)
    assert isinstance(HiggsfieldProvider(catalog), GenProvider)
    assert get_provider("fal", catalog).id == "fal"
    with pytest.raises(AltocueError) as caught:
        get_provider("sora", catalog)
    assert caught.value.fix


# The spend gate


def test_cap_of_zero_blocks_all_paid_calls_with_a_clear_fix(tmp_path, fal, capsys):
    project = configure(Project.create(tmp_path / "zero"), cap=0.0, fal_url=fal.url)
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    err = capsys.readouterr().err
    assert "spend_cap_usd" in err and "0.78" in err
    assert fal.calls == []


def test_over_cap_blocks_before_any_call(tmp_path, fal, capsys):
    project = configure(Project.create(tmp_path / "low"), cap=0.5, fal_url=fal.url)
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert "0.5" in capsys.readouterr().err
    assert fal.calls == []


def test_total_is_shown_before_spending(project, capsys):
    write_shots(project, shot())
    assert go(project, "--yes") == 0
    out = capsys.readouterr().out
    assert "$0.77" in out
    assert "Total" in out


def test_no_yes_and_no_terminal_refuses(project, fal, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    write_shots(project, shot())
    assert go(project) == 1
    assert "--yes" in capsys.readouterr().err
    assert fal.calls == []


@pytest.mark.parametrize(("answer", "code", "submits"), [("y", 0, 1), ("yes", 0, 1), ("n", 1, 0), ("", 1, 0)])
def test_interactive_answer_decides(project, fal, monkeypatch, answer, code, submits):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    write_shots(project, shot())
    assert go(project) == code
    assert len(fal.submits) == submits


def test_dry_run_needs_no_key(project, fal, monkeypatch, capsys):
    monkeypatch.delenv("FAL_KEY")
    write_shots(project, shot())
    assert go(project, "--dry-run") == 0
    assert "$0.77" in capsys.readouterr().out


def test_dry_run_spends_nothing(project, fal, capsys):
    write_shots(project, shot())
    assert go(project, "--dry-run") == 0
    assert "$0.77" in capsys.readouterr().out
    assert fal.calls == []
    assert spend(project) == []


def test_no_provider_means_nothing_generates(tmp_path, fal, capsys):
    bare = Project.create(tmp_path / "bare")
    write_shots(bare, shot())
    assert go(bare, "--yes") == 1
    assert "provider" in capsys.readouterr().err


def test_missing_key_is_caught_before_the_yes(project, fal, monkeypatch, capsys):
    monkeypatch.delenv("FAL_KEY")
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert "FAL_KEY" in capsys.readouterr().err
    assert fal.calls == []


def test_invalid_shots_never_reach_the_provider(project, fal, capsys):
    write_shots(project, shot(kind="audio"))
    assert go(project, "--yes") == 1
    assert fal.calls == []


def test_uploads_are_named_before_they_happen(project, fal, capsys):
    (project.root / "key.png").write_bytes(PNG)
    write_shots(project, shot(first_frame="key.png"))
    assert go(project, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "key.png" in out and "fal.ai" in out and "public" in out
    assert fal.uploads == {}


def test_a_stale_price_is_called_out(project, capsys, monkeypatch):
    write_shots(project, shot())
    monkeypatch.setattr("altocue.generate.runner.today", lambda: "2027-01-15")
    assert go(project, "--dry-run") == 0
    assert "older than 30 days" in capsys.readouterr().out


# fal, end to end


def test_full_run_with_a_first_frame(project, fal, capsys):
    (project.root / "key.png").write_bytes(PNG)
    write_shots(project, shot(first_frame="key.png"))
    assert go(project, "--yes") == 0

    assert fal.uploads["key.png"] == PNG
    submit = fal.submits[0]
    assert submit["path"] == "/bytedance/seedance-2.0/mini/image-to-video"
    assert submit["auth"] == "Key test-key"
    body = json.loads(submit["body"])
    assert body == {
        "prompt": PROMPT,
        "image_url": f"{fal.url}/cdn/key.png",
        "resolution": "720p",
        "duration": "5",
        "aspect_ratio": "9:16",
        "generate_audio": False,
    }

    asset = generated_asset(project, "s01")
    assert asset.suffix == ".mp4"
    assert asset.read_bytes() == fal.media
    sidecar = json.loads(asset.with_name(asset.name + ".provenance.json").read_text())
    assert sidecar["prompt"] == PROMPT
    assert sidecar["model"] == "bytedance/seedance-2.0/mini/image-to-video"
    assert sidecar["provider"] == "fal"
    assert sidecar["seed"] == 4242
    assert sidecar["cost_usd"] == pytest.approx(0.7735)
    assert sidecar["terms_url"] == "https://fal.ai/terms"
    assert sidecar["date"].startswith("20")
    assert sidecar["job_id"] == "req-1"
    assert sidecar["billable_units"] == "108"
    assert len(sidecar["sha256"]) == 64

    ledger = spend(project)
    assert len(ledger) == 1
    assert ledger[0]["usd"] == pytest.approx(0.7735)
    assert ledger[0]["shot"] == "s01"
    assert ledger[0]["basis"] == "catalog"
    assert ledger[0]["billable_units"] == "108"


def test_image_shot_uses_per_image_route(project, fal):
    (project.root / "ref.png").write_bytes(PNG)
    fal.result_body = {"images": [{"url": f"{fal.url}/media/still.png"}], "description": ""}
    write_shots(project, still(tier="hero", refs=["ref.png"]))
    assert go(project, "--yes") == 0
    assert fal.submits[0]["path"] == "/fal-ai/nano-banana-2/edit"
    body = json.loads(fal.submits[0]["body"])
    assert body["image_urls"] == [f"{fal.url}/cdn/ref.png"]
    assert body["resolution"] == "4K"
    assert "duration" not in body
    assert generated_asset(project, "s01").suffix == ".png"


def test_seed_is_sent_when_the_model_takes_one(project, fal):
    fal.result_body = {"images": [{"url": f"{fal.url}/media/still.png"}]}
    write_shots(project, still(seed=7))
    assert go(project, "--yes") == 0
    assert json.loads(fal.submits[0]["body"])["seed"] == 7
    asset = generated_asset(project, "s01")
    assert json.loads(asset.with_name(asset.name + ".provenance.json").read_text())["seed"] == 7


def test_seed_the_model_cannot_take_is_recorded_as_ignored(project, fal, capsys):
    write_shots(project, shot(seed=7))
    assert go(project, "--yes") == 0
    assert "seed" not in json.loads(fal.submits[0]["body"])
    asset = generated_asset(project, "s01")
    sidecar = json.loads(asset.with_name(asset.name + ".provenance.json").read_text())
    assert sidecar["seed_requested"] == 7
    assert sidecar["seed"] == 4242
    assert "ignores the seed" in capsys.readouterr().out


def test_style_goes_into_the_prompt_and_provenance(project, fal):
    (project.root / "shots.json").write_text(json.dumps({"style": "Felt and wool.", "shots": [shot()]}))
    assert go(project, "--yes") == 0
    assert json.loads(fal.submits[0]["body"])["prompt"] == f"{PROMPT}\n\nFelt and wool."


def test_job_reference_is_on_disk_before_the_first_poll(project, fal):
    seen = {}
    fal.on_status = lambda request_id: seen.setdefault("job", jobs(project)[0])
    write_shots(project, shot())
    assert go(project, "--yes") == 0
    assert seen["job"]["job"]["job_id"] == "req-1"
    assert seen["job"]["job"]["meta"]["status_url"].endswith("/status")


def test_crash_while_polling_resumes_without_paying_twice(project, fal, monkeypatch):
    write_shots(project, shot())

    def die(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("altocue.generate.runner.sleep", die)
    with pytest.raises(KeyboardInterrupt):
        go(project, "--yes")
    assert len(fal.submits) == 1
    assert generated_asset(project, "s01", missing_ok=True) is None

    monkeypatch.setattr("altocue.generate.runner.sleep", lambda seconds: None)
    assert go(project, "--yes") == 0
    assert len(fal.submits) == 1
    assert generated_asset(project, "s01").exists()
    assert len(spend(project)) == 1


def test_resume_needs_no_cap_or_yes_because_it_is_already_paid(project, fal, monkeypatch, capsys):
    write_shots(project, shot())

    def die(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("altocue.generate.runner.sleep", die)
    with pytest.raises(KeyboardInterrupt):
        go(project, "--yes")
    monkeypatch.setattr("altocue.generate.runner.sleep", lambda seconds: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    text = (project.root / "altocue.toml").read_text().replace("spend_cap_usd = 100.0", "spend_cap_usd = 0.0")
    (project.root / "altocue.toml").write_text(text)
    assert go(project) == 0
    assert "resum" in capsys.readouterr().out.lower()


def test_crash_between_submit_and_saving_refuses_to_guess(project, fal, capsys):
    write_shots(project, shot())
    assert go(project, "--dry-run") == 0
    store = project.work / "jobs"
    store.mkdir(parents=True)
    from altocue.generate import runner

    review, provider, _ = runner.open_run(project, "shots.json", None)
    plan = runner.build_plan(project, review, provider)
    record = {"shot": "s01", "hash": plan[0].hash, "state": "submitting", "job": None, "attempt": 1}
    (store / f"s01-{plan[0].hash}.json").write_text(json.dumps(record))
    capsys.readouterr()

    assert go(project, "--yes") == 1
    err = capsys.readouterr().err
    assert "--resubmit s01" in err
    assert fal.calls == []

    assert go(project, "--yes", "--resubmit", "s01") == 0
    assert len(fal.submits) == 1


def test_lost_response_at_submit_is_held_as_unknown(project, fal, capsys, monkeypatch):
    write_shots(project, shot())

    def lost(*args, **kwargs):
        from altocue.generate.base import SubmitUnknown

        raise SubmitUnknown("Lost the connection during submit.")

    monkeypatch.setattr(FalProvider, "submit", lost)
    assert go(project, "--yes") == 1
    assert jobs(project)[0]["state"] == "submitting"
    assert go(project, "--yes") == 1
    assert "--resubmit" in capsys.readouterr().err


def test_moderated_job_is_final_and_never_retried(project, fal, capsys):
    fal.result_code = 422
    flagged = {"loc": ["body", "prompt"], "msg": "Flagged.", "type": "content_policy_violation", "categories": ["violence"]}
    fal.result_body = {"detail": [flagged]}
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert jobs(project)[0]["state"] == "moderated"
    assert "moderated" in capsys.readouterr().out

    assert go(project, "--yes") == 1
    assert len(fal.submits) == 1
    assert "change the prompt" in capsys.readouterr().out.lower()
    assert [row["usd"] for row in spend(project)] == [0]

    write_shots(project, shot(prompt=PROMPT + " The rain stops."))
    fal.result_code = 200
    fal.result_body = None
    assert go(project, "--yes") == 0
    assert len(fal.submits) == 2


def test_moderated_at_submit_is_the_same_state(project, fal):
    fal.submit_code = 422
    fal.submit_body = {"detail": [{"loc": ["body"], "msg": "Flagged.", "type": "content_policy_violation"}]}
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert jobs(project)[0]["state"] == "moderated"


def test_failed_job_is_reported_and_a_rerun_goes_through_the_gate_again(project, fal, capsys):
    fal.result_code = 500
    fal.result_body = {"detail": "Runner failed", "error_type": "runner_server_error"}
    fal.status_extra = {"error": "Runner failed", "error_type": "runner_server_error"}
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert jobs(project)[0]["state"] == "failed"
    assert "Runner failed" in capsys.readouterr().out
    assert len(fal.submits) == 1

    fal.result_code = 200
    fal.result_body = None
    fal.status_extra = {}
    assert go(project, "--yes") == 0
    assert len(fal.submits) == 2
    assert jobs(project)[0]["attempt"] == 2


def test_submit_is_never_repeated_when_the_answer_is_unclear(project, fal, capsys):
    fal.submit_code = 503
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert len(fal.submits) == 1
    assert jobs(project)[0]["state"] == "submitting"
    assert "--resubmit s01" in capsys.readouterr().err


def test_a_job_outcome_is_logged_once(project):
    from altocue.generate import store

    row = {"shot": "s01", "usd": 1.0, "job_id": "req-1", "state": "succeeded"}
    store.log_spend(project, row)
    store.log_spend(project, row)
    assert len(spend(project)) == 1


def test_rejected_submit_is_failed_not_unknown(project, fal, capsys):
    fal.submit_code = 400
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert jobs(project)[0]["state"] == "failed"
    assert "rejected" in capsys.readouterr().out


def test_bad_key_says_how_to_fix_it(project, fal, capsys):
    fal.submit_code = 401
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert "FAL_KEY" in capsys.readouterr().out


def test_expired_result_is_explained(project, fal, capsys):
    fal.result_code = 404
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert "expired" in capsys.readouterr().out


def test_cancelled_request_maps_to_canceled(project, fal):
    fal.statuses = ["COMPLETED"]
    fal.status_extra = {"error": "Cancelled", "error_type": "client_cancelled"}
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert jobs(project)[0]["state"] == "canceled"


def test_transient_poll_errors_are_ridden_out(project, fal):
    fal.status_codes = [503, 500]
    write_shots(project, shot())
    assert go(project, "--yes") == 0


def test_poll_outage_leaves_the_job_resumable(project, fal, capsys):
    fal.status_codes = [503] * 50
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert "re-run" in capsys.readouterr().err.lower()
    fal.status_codes = []
    assert go(project, "--yes") == 0
    assert len(fal.submits) == 1


def test_timeout_keeps_the_job_for_later(project, fal, capsys, monkeypatch):
    fal.statuses = ["IN_PROGRESS"]
    write_shots(project, shot())
    clock = iter(range(0, 100000, 1000))
    monkeypatch.setattr("altocue.generate.runner.monotonic", lambda: next(clock))
    assert go(project, "--yes", "--timeout", "1") == 1
    assert "still running" in capsys.readouterr().out
    assert jobs(project)[0]["state"] == "running"
    assert jobs(project)[0]["job"]["job_id"] == "req-1"


def test_finished_shots_are_skipped(project, fal, capsys):
    write_shots(project, shot())
    assert go(project, "--yes") == 0
    capsys.readouterr()
    assert go(project, "--yes") == 0
    assert len(fal.submits) == 1
    assert "already" in capsys.readouterr().out


def test_changing_a_prompt_makes_a_new_asset_and_keeps_the_old(project, fal):
    write_shots(project, shot())
    assert go(project, "--yes") == 0
    first = generated_asset(project, "s01")
    write_shots(project, shot(prompt=PROMPT + " Rain falls."))
    assert go(project, "--yes") == 0
    second = generated_asset(project, "s01")
    assert first != second and first.exists() and second.exists()


def test_going_back_to_an_earlier_prompt_reuses_its_asset_for_free(project, fal, capsys):
    write_shots(project, shot())
    assert go(project, "--yes") == 0
    first = generated_asset(project, "s01")
    write_shots(project, shot(prompt=PROMPT + " Rain falls."))
    assert go(project, "--yes") == 0
    capsys.readouterr()
    write_shots(project, shot())
    assert go(project, "--dry-run") == 0
    assert "already" in capsys.readouterr().out
    assert go(project) == 0
    assert generated_asset(project, "s01") == first
    assert len(fal.submits) == 2


def test_failed_download_is_collected_later_without_paying_or_polling_again(project, fal, capsys):
    fal.media_failures = 1
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert jobs(project)[0]["state"] == "succeeded"
    polls = len(fal.seen("GET", "/status"))
    assert go(project, "--yes") == 0
    assert len(fal.submits) == 1
    assert len(fal.seen("GET", "/status")) == polls
    assert generated_asset(project, "s01").read_bytes() == fal.media
    assert len(spend(project)) == 1


def test_video_waiting_on_a_still_outside_the_run_is_blocked(project, fal, capsys):
    write_shots(project, still(id="key"), shot(id="s02", first_frame="@key"))
    assert go(project, "--yes", "--only", "s02") == 1
    assert fal.submits == []
    assert "Generate key first" in capsys.readouterr().out


def test_changing_a_reference_file_regenerates(project, fal):
    (project.root / "key.png").write_bytes(PNG)
    write_shots(project, shot(first_frame="key.png"))
    assert go(project, "--yes") == 0
    (project.root / "key.png").write_bytes(PNG + b"1")
    assert go(project, "--yes") == 0
    assert len(fal.submits) == 2


def test_still_feeds_the_video_that_needs_it(project, fal):
    fal.result_body = None
    write_shots(project, still(id="key"), shot(id="s02", first_frame="@key"))
    original = fal.result_body

    def route(request_id):
        fal.result_body = {"images": [{"url": f"{fal.url}/media/key.png"}]} if request_id == "req-1" else original

    fal.on_status = route
    assert go(project, "--yes") == 0
    assert [c["path"] for c in fal.submits] == ["/fal-ai/nano-banana-2", "/bytedance/seedance-2.0/mini/image-to-video"]
    assert json.loads(fal.submits[1]["body"])["image_url"].startswith(f"{fal.url}/cdn/key-")


def test_a_failed_still_blocks_its_video_and_nothing_is_paid_for_it(project, fal, capsys):
    fal.result_code = 500
    fal.result_body = {"detail": "Runner failed", "error_type": "runner_server_error"}
    write_shots(project, still(id="key"), shot(id="s02", first_frame="@key"))
    assert go(project, "--yes") == 1
    assert len(fal.submits) == 1
    assert "s02" in capsys.readouterr().out


def test_only_limits_the_run(project, fal):
    write_shots(project, shot(), shot(id="s02"))
    assert go(project, "--yes", "--only", "s02") == 0
    assert len(fal.submits) == 1
    assert generated_asset(project, "s01", missing_ok=True) is None


def test_only_with_an_unknown_id_is_an_error(project, fal, capsys):
    write_shots(project, shot())
    assert go(project, "--yes", "--only", "ghost") == 1
    assert "ghost" in capsys.readouterr().err


def test_check_runs_on_the_result_and_a_failed_clip_is_not_placed(project, fal, tmp_path, capsys):
    import subprocess

    frozen = tmp_path / "frozen.mp4"
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=160x284:rate=30:duration=1"]
    subprocess.run([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(frozen)], check=True)
    fal.media = frozen.read_bytes()
    write_shots(project, shot(check="motion"))
    assert go(project, "--yes") == 1
    assert "frozen" in capsys.readouterr().out
    with pytest.raises(AltocueError):
        generated_asset(project, "s01")
    asset = generated_asset(project, "s01", require_passed=False)
    sidecar = json.loads(asset.with_name(asset.name + ".provenance.json").read_text())
    assert sidecar["check"]["passed"] is False
    assert (project.work / "generated" / "checks" / "s01" / "mid.png").exists()


def test_passing_clip_records_its_score(project, fal):
    write_shots(project, shot(check="motion"))
    assert go(project, "--yes") == 0
    asset = generated_asset(project, "s01")
    sidecar = json.loads(asset.with_name(asset.name + ".provenance.json").read_text())
    assert sidecar["check"]["passed"] is True
    assert sidecar["check"]["subject_mean"] > 0


def test_cancel_command(project, fal, monkeypatch, capsys):
    write_shots(project, shot())

    def die(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("altocue.generate.runner.sleep", die)
    with pytest.raises(KeyboardInterrupt):
        go(project, "--yes")
    assert go(project, "--cancel", "s01") == 0
    assert fal.seen("PUT", "/cancel")
    assert jobs(project)[0]["state"] == "canceled"


def test_cancel_with_nothing_running(project, capsys):
    write_shots(project, shot())
    assert go(project, "--cancel", "s01") == 1


# Secrets stay home


def test_key_never_reaches_the_cdn_or_the_presigned_upload(project, fal):
    (project.root / "key.png").write_bytes(PNG)
    write_shots(project, shot(first_frame="key.png"))
    assert go(project, "--yes") == 0
    assert all(c["auth"] is None for c in fal.seen("GET", "/media/"))
    assert all(c["auth"] is None for c in fal.seen("PUT", "/put/"))
    assert all(c["auth"] == "Key test-key" for c in fal.submits)


def test_key_is_not_sent_to_an_unknown_host(tmp_path, monkeypatch):
    catalog = load_catalog()
    catalog.providers["fal"]["queue_url"] = "https://evil.example.com"
    provider = FalProvider(catalog)
    with pytest.raises(AltocueError, match="Refusing to send FAL_KEY to evil.example.com"):
        provider.submit(GenRequest(kind="video", tier="draft", prompt="p", aspect="9:16", duration_s=5), "key")


def test_status_url_on_another_host_is_ignored(project, fal):
    provider = get_provider("fal", generate.load_catalog(project.root / "models.local.toml"))
    job = generate.JobRef(provider="fal", job_id="req-9", model="a/b/c", meta={"status_url": "https://evil.example.com/steal"})
    fal.requests["req-9"] = 0
    assert provider.poll(job).state == "queued"
    assert fal.seen("GET", "/a/b/requests/req-9/status")


def test_download_refuses_a_local_file_url(project, fal, capsys):
    fal.result_body = {"video": {"url": "file:///etc/passwd"}}
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    assert "file" in capsys.readouterr().out
    assert generated_asset(project, "s01", missing_ok=True) is None


def test_extra_fields_cannot_change_what_was_priced(project, fal, capsys):
    write_shots(project, shot(extra={"duration": "15"}))
    assert go(project, "--yes") == 1
    assert "price" in capsys.readouterr().err
    assert fal.seen("POST", "/") == []


def test_requests_name_themselves_because_cloudflare_blocks_the_default():
    from altocue.generate import http

    assert ("User-Agent", http.AGENT) in http._opener.addheaders
    assert ("User-Agent", http.AGENT) in http._fetcher.addheaders


def test_downloads_cannot_reach_a_private_network():
    from altocue.generate import http

    with pytest.raises(AltocueError, match="private"):
        http.check_download_url("https://169.254.169.254/latest/meta-data")
    with pytest.raises(AltocueError):
        http._CheckedRedirect().redirect_request(None, None, 302, "", {}, "file:///etc/passwd")


def test_redirects_do_not_carry_the_key(fal):
    from altocue.generate import http

    reply = http.call("GET", f"{fal.url}/redirect", headers={"Authorization": "Key k"})
    assert reply.status == 302
    assert fal.seen("GET", "/media/") == []


# Higgsfield


FAKE_CLI = """#!{python}
import json, os, sys
state_path = os.environ["FAKE_HF_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
state["calls"].append(args)

def save():
    json.dump(state, open(state_path, "w"))

def out(data):
    save()
    print(json.dumps(data))
    sys.exit(0)

def die(text, code=1):
    save()
    print(text, file=sys.stderr)
    sys.exit(code)

if not state.get("authed", True):
    die("Session expired. Run higgsfield auth login")
if args[:2] == ["generate", "cost"]:
    if state.get("cost_broken"):
        die("no cost today")
    out({{"credits": state.get("credits", 10)}})
if args[:2] == ["generate", "create"]:
    if state.get("create_blocked"):
        die("Prompt rejected: nsfw")
    state["jobs"] = state.get("jobs", 0) + 1
    state["polls"] = 0
    out({{"id": "job-%d" % state["jobs"], "status": "queued"}})
if args[:2] == ["generate", "get"]:
    statuses = state.get("statuses", ["queued", "in_progress", "completed"])
    index = min(state.get("polls", 0), len(statuses) - 1)
    state["polls"] = state.get("polls", 0) + 1
    status = statuses[index]
    data = {{"id": args[2], "status": status}}
    if status == "completed":
        data["result_url"] = state["result_url"]
    out(data)
die("unknown command " + " ".join(args))
"""


@pytest.fixture
def higgsfield(tmp_path: Path, fal: FakeFal, monkeypatch):
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "higgsfield"
    script.write_text(FAKE_CLI.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "hf-state.json"
    state.write_text(json.dumps({"calls": [], "result_url": f"{fal.url}/media/out.mp4"}))
    monkeypatch.setenv("FAKE_HF_STATE", str(state))
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")

    class Fake:
        path = state

        @staticmethod
        def read() -> dict:
            return json.loads(state.read_text())

        @staticmethod
        def write(**changes) -> None:
            data = json.loads(state.read_text())
            state.write_text(json.dumps({**data, **changes}))

        @staticmethod
        def creates() -> list[list[str]]:
            return [c for c in json.loads(state.read_text())["calls"] if c[:2] == ["generate", "create"]]

    return Fake


@pytest.fixture
def hf_project(tmp_path: Path, higgsfield) -> Project:
    return configure(Project.create(tmp_path / "hf"), provider="higgsfield")


def test_higgsfield_full_run(hf_project, higgsfield, fal, capsys):
    (hf_project.root / "key.png").write_bytes(PNG)
    (hf_project.root / "ref.png").write_bytes(PNG)
    write_shots(hf_project, shot(tier="standard", first_frame="key.png", refs=["ref.png"]))
    assert go(hf_project, "--yes") == 0

    create = higgsfield.creates()[0]
    assert create[2] == "seedance_2_0"
    assert "--wait" not in create
    assert "--json" in create
    assert create[create.index("--prompt") + 1] == PROMPT
    assert create[create.index("--start-image") + 1] == str(hf_project.root / "key.png")
    assert create[create.index("--image-references") + 1] == str(hf_project.root / "ref.png")
    assert create[create.index("--aspect_ratio") + 1] == "9:16"
    assert create[create.index("--duration") + 1] == "5"
    assert create[create.index("--resolution") + 1] == "720p"
    assert create[create.index("--generate_audio") + 1] == "false"

    asset = generated_asset(hf_project, "s01")
    assert asset.read_bytes() == fal.media
    sidecar = json.loads(asset.with_name(asset.name + ".provenance.json").read_text())
    assert sidecar["provider"] == "higgsfield"
    assert sidecar["model"] == "seedance_2_0"
    assert sidecar["job_id"] == "job-1"
    assert sidecar["terms_url"].startswith("https://higgsfield.ai")
    assert sidecar["cost_usd"] == pytest.approx(0.8)
    assert spend(hf_project)[0]["basis"] == "credits"
    assert "per credit" in capsys.readouterr().out


def test_higgsfield_job_is_saved_before_polling(hf_project, higgsfield, monkeypatch):
    write_shots(hf_project, shot())

    def die(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("altocue.generate.runner.sleep", die)
    with pytest.raises(KeyboardInterrupt):
        go(hf_project, "--yes")
    assert jobs(hf_project)[0]["job"]["job_id"] == "job-1"
    monkeypatch.setattr("altocue.generate.runner.sleep", lambda seconds: None)
    assert go(hf_project, "--yes") == 0
    assert len(higgsfield.creates()) == 1


def test_higgsfield_nsfw_is_moderated_and_not_retried(hf_project, higgsfield):
    higgsfield.write(statuses=["queued", "nsfw"])
    write_shots(hf_project, shot())
    assert go(hf_project, "--yes") == 1
    assert jobs(hf_project)[0]["state"] == "moderated"
    assert go(hf_project, "--yes") == 1
    assert len(higgsfield.creates()) == 1


def test_higgsfield_nsfw_at_submit_is_moderated(hf_project, higgsfield):
    higgsfield.write(create_blocked=True)
    write_shots(hf_project, shot())
    assert go(hf_project, "--yes") == 1
    assert jobs(hf_project)[0]["state"] == "moderated"


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        ("queued", "queued"),
        ("in_progress", "running"),
        ("completed", "succeeded"),
        ("failed", "failed"),
        ("nsfw", "moderated"),
        ("canceled", "canceled"),
        ("cancelled", "canceled"),
    ],
)
def test_higgsfield_states_are_normalised(raw, state):
    from altocue.generate.higgsfield import normalise

    assert normalise(raw) == state


def test_higgsfield_unknown_state_keeps_waiting():
    from altocue.generate.higgsfield import normalise

    assert normalise("thinking") == "running"


def test_higgsfield_not_installed(tmp_path, monkeypatch, capsys):
    project = configure(Project.create(tmp_path / "none"), provider="higgsfield")
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    write_shots(project, shot())
    assert go(project, "--yes") == 1
    err = capsys.readouterr().err
    assert "brew install" in err or "npm install" in err


def test_higgsfield_needs_login(hf_project, higgsfield, capsys):
    higgsfield.write(authed=False)
    write_shots(hf_project, shot())
    assert go(hf_project, "--yes") == 1
    assert "higgsfield auth login" in capsys.readouterr().err


def test_unpriced_shot_is_never_paid_for(hf_project, higgsfield, capsys):
    higgsfield.write(cost_broken=True)
    write_shots(hf_project, shot())
    assert go(hf_project, "--yes") == 1
    assert "could not be priced" in capsys.readouterr().err
    assert higgsfield.creates() == []


def test_higgsfield_uses_your_credit_rate(tmp_path, higgsfield):
    project = configure(Project.create(tmp_path / "rate"), provider="higgsfield")
    text = re.sub(r"usd_per_credit = [0-9.]+", "usd_per_credit = 0.05", (project.root / "altocue.toml").read_text())
    (project.root / "altocue.toml").write_text(text)
    project = Project.open(project.root)
    write_shots(project, shot())
    assert go(project, "--dry-run") == 0
    assert estimate(shots_of(shot()), get_provider("higgsfield", load_catalog(), project=project)).usd == pytest.approx(0.5)


def test_higgsfield_cancel_is_explained(hf_project, higgsfield):
    provider = HiggsfieldProvider(load_catalog())
    with pytest.raises(AltocueError) as caught:
        provider.cancel(generate.JobRef(provider="higgsfield", job_id="job-1", model="x"))
    assert caught.value.fix


def test_higgsfield_list_payload_and_nested_url():
    from altocue.generate.higgsfield import job_id_of, result_urls

    assert job_id_of([{"job_id": "a"}]) == "a"
    assert job_id_of({"job": {"id": "b"}}) == "b"
    assert job_id_of({"nothing": 1}) is None
    assert result_urls({"results": [{"url": "https://x/a.mp4"}]}) == ["https://x/a.mp4"]
    assert result_urls({"result_url": "https://x/b.mp4"}) == ["https://x/b.mp4"]
    assert result_urls({"result": {"url": "https://x/c.mp4"}}) == ["https://x/c.mp4"]


def test_lost_job_id_is_held_as_unknown(hf_project, higgsfield, monkeypatch):
    monkeypatch.setattr("altocue.generate.higgsfield.job_id_of", lambda data: None)
    write_shots(hf_project, shot())
    assert go(hf_project, "--yes") == 1
    assert jobs(hf_project)[0]["state"] == "submitting"


# Import


def imported(project: Project, source: Path, *extra: str) -> int:
    return go(project, "--import", str(source), "--shot", "s01", "--prompt", PROMPT, "--model", "seedance_2_0", "--cost", "1.25", *extra)


def test_import_registers_with_provenance_and_spend(project, tmp_path, capsys):
    source = make_clip(tmp_path / "from-mcp.mp4", seconds=1.0, audio=False)
    write_shots(project, shot())
    assert imported(project, source, "--seed", "99") == 0
    asset = generated_asset(project, "s01")
    assert asset.parent == project.work / "generated"
    assert asset.read_bytes() == source.read_bytes()
    assert source.exists()
    sidecar = json.loads(asset.with_name(asset.name + ".provenance.json").read_text())
    assert sidecar["prompt"] == PROMPT
    assert sidecar["model"] == "seedance_2_0"
    assert sidecar["seed"] == 99
    assert sidecar["cost_usd"] == 1.25
    assert sidecar["provider"] == "higgsfield"
    assert sidecar["route"] == "import"
    assert sidecar["terms_url"].startswith("https://higgsfield.ai")
    assert spend(project)[0]["basis"] == "import"
    assert spend(project)[0]["usd"] == 1.25


def test_import_needs_no_cap_because_the_money_is_already_spent(tmp_path, fal):
    project = configure(Project.create(tmp_path / "capless"), cap=0.0, fal_url=fal.url)
    source = make_clip(tmp_path / "from-mcp.mp4", seconds=1.0, audio=False)
    assert imported(project, source) == 0
    assert fal.calls == []


def test_generate_skips_an_imported_shot(project, fal, tmp_path, capsys):
    source = make_clip(tmp_path / "from-mcp.mp4", seconds=1.0, audio=False)
    write_shots(project, shot())
    assert imported(project, source) == 0
    assert go(project, "--yes") == 0
    assert fal.submits == []
    assert "already" in capsys.readouterr().out


def test_import_refuses_a_missing_file(project, tmp_path, capsys):
    assert imported(project, tmp_path / "nothing.mp4") == 1
    assert "nothing.mp4" in capsys.readouterr().err


def test_import_refuses_a_file_that_is_not_media(project, tmp_path):
    bogus = tmp_path / "bogus.mp4"
    bogus.write_text("not video")
    assert imported(project, bogus) == 1


def test_import_needs_every_provenance_field(project, tmp_path, capsys):
    source = make_clip(tmp_path / "from-mcp.mp4", seconds=1.0, audio=False)
    code = go(project, "--import", str(source), "--shot", "s01")
    assert code == 1
    err = capsys.readouterr().err
    assert "--prompt" in err and "--model" in err and "--cost" in err


def test_import_refuses_negative_cost(project, tmp_path):
    source = make_clip(tmp_path / "from-mcp.mp4", seconds=1.0, audio=False)
    assert go(project, "--import", str(source), "--shot", "s01", "--prompt", "p", "--model", "m", "--cost", "-1") == 1


def test_import_refuses_the_wrong_kind_for_the_shot(project, tmp_path, capsys):
    source = make_clip(tmp_path / "from-mcp.mp4", seconds=1.0, audio=False)
    write_shots(project, still())
    assert imported(project, source) == 1
    assert "image" in capsys.readouterr().err


def test_import_function_returns_the_asset(project, tmp_path):
    source = make_clip(tmp_path / "from-mcp.mp4", seconds=1.0, audio=False)
    asset = import_asset(project, file=source, shot="s09", prompt="p", model="m", cost=0.5)
    assert asset == generated_asset(project, "s09")


def test_generated_asset_for_an_unknown_shot(project):
    with pytest.raises(AltocueError) as caught:
        generated_asset(project, "ghost")
    assert caught.value.fix
    assert generated_asset(project, "ghost", missing_ok=True) is None


# Command plumbing


def test_stage_exposes_the_cli_hooks():
    assert callable(generate.add_arguments) and callable(generate.run)


def test_no_shots_file_is_explained(project, capsys):
    assert go(project, "--yes") == 1
    assert "shots.json" in capsys.readouterr().err
