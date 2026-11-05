"""The plugin every agent installs, and the MCP server inside it.

The server has to start from the plugin's own folder on a plain system Python before Altocue is installed, so it is held
to the standard library and to Python 3.8 syntax here. Every manifest has to name the version that ships.
"""

from __future__ import annotations

import ast
import io
import json
import re
import struct
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from altocue import __version__, cli, mcp

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp-server" / "server.py"
MANIFESTS = {
    "Claude Code": ROOT / ".claude-plugin" / "plugin.json",
    "Codex": ROOT / "plugin.json",
    "Cursor": ROOT / ".cursor-plugin" / "plugin.json",
    "Gemini CLI": ROOT / "gemini-extension.json",
}
# Each client names the plugin's folder its own way.
SERVER_CONFIGS = {
    ROOT / ".mcp.json": "${CLAUDE_PLUGIN_ROOT}/",
    ROOT / "mcp.json": "${PLUGIN_ROOT}/",
    ROOT / "gemini-extension.json": "${extensionPath}${/}",
}


def read(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.mark.parametrize("agent", MANIFESTS)
def test_every_manifest_names_the_version_that_ships(agent):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == __version__
    assert read(MANIFESTS[agent]).get("version") == __version__, f"{MANIFESTS[agent].name} must say {__version__}"


@pytest.mark.parametrize("agent", MANIFESTS)
def test_every_manifest_is_branded_altocue(agent):
    manifest = read(MANIFESTS[agent])
    assert manifest["name"] == "altocue"
    assert chr(0x2014) not in json.dumps(manifest, ensure_ascii=False), "no em dashes in public copy"


def test_the_mark_every_manifest_points_at_is_a_square_png():
    named = {
        read(MANIFESTS["Claude Code"])["icon"],
        read(MANIFESTS["Cursor"])["logo"],
        read(MANIFESTS["Codex"])["extensions"]["com.openai"]["interface"]["logo"],
        read(MANIFESTS["Codex"])["extensions"]["com.openai"]["interface"]["composerIcon"],
    }
    assert {Path(name).as_posix().removeprefix("./") for name in named} == {"assets/icon.png"}
    head = (ROOT / "assets" / "icon.png").read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", head[16:24])
    assert width == height >= 512


def test_both_marketplaces_list_the_plugin_at_the_root():
    claude = read(ROOT / ".claude-plugin" / "marketplace.json")
    codex = read(ROOT / ".agents" / "plugins" / "marketplace.json")
    assert [(entry["name"], entry["source"]) for entry in claude["plugins"]] == [("altocue", "./")]
    assert [(entry["name"], entry["source"]["path"]) for entry in codex["plugins"]] == [("altocue", "./")]


@pytest.mark.parametrize("config", SERVER_CONFIGS, ids=lambda path: path.name)
def test_every_client_starts_the_server_from_the_plugin_folder(config):
    servers = read(config)["mcpServers"]
    assert list(servers) == ["altocue"]
    server = servers["altocue"]
    assert server["command"] == "python3"
    root = SERVER_CONFIGS[config]
    assert server["args"] == [f"{root}mcp-server{'${/}' if '${/}' in root else '/'}server.py"]
    assert SERVER.is_file()


def test_the_cursor_manifest_points_at_folders_that_exist():
    manifest = read(MANIFESTS["Cursor"])
    for key in ("skills", "agents", "commands", "mcpServers"):
        assert (ROOT / manifest[key]).exists(), f"{key}: {manifest[key]}"


def test_no_plugin_ships_a_top_level_bin():
    """A top-level bin/ keeps a plugin out of claude.ai and Cowork."""
    assert not (ROOT / "bin").exists()


def test_the_wheel_carries_the_skills_the_server_reads():
    build = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert build["force-include"]["skills"] == "altocue/data/skills"


@pytest.mark.parametrize("path", [ROOT / "src" / "altocue" / "mcp.py", SERVER], ids=["mcp.py", "server.py"])
def test_the_server_needs_only_the_standard_library_and_python_3_8(path):
    tree = ast.parse(path.read_text(), feature_version=(3, 8))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                assert [alias.name for alias in node.names] == ["__version__"], "mcp.py may take only the version from the package"
                continue
            names = [node.module or ""]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or name in ("__future__", "altocue.mcp"), f"{path.name} imports {name}"


def test_the_package_init_stays_light_enough_for_the_plugin_to_import():
    """server.py imports altocue/__init__.py on the system Python. It must hold the version and nothing that imports."""
    tree = ast.parse((ROOT / "src" / "altocue" / "__init__.py").read_text(), feature_version=(3, 8))
    assert not [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]


def test_altocue_mcp_is_a_stage():
    assert cli.STAGES["mcp"][0] == "altocue.mcp"


def test_a_retention_note_never_lands_on_the_protocol_stream(monkeypatch, capsys):
    from altocue import retention

    monkeypatch.setattr(retention, "purge_everywhere", lambda *_: print("Deleted stats.json: YouTube statistics may not be kept."))
    monkeypatch.setattr(mcp, "serve", lambda: 0)
    assert cli.main(["mcp"]) == 0
    out, err = capsys.readouterr()
    assert out == "" and "Deleted stats.json" in err


def talk(*messages: dict) -> list[dict]:
    """Messages through serve() in this process, and the replies in the order they came back."""
    source = io.BytesIO(b"".join(json.dumps(message).encode() + b"\n" for message in messages))
    sink = io.BytesIO()
    mcp.serve(source, sink)
    deadline = time.time() + 30
    replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    wanted = sum("id" in message for message in messages)
    while len(replies) < wanted and time.time() < deadline:  # tools/call answers from its own thread
        time.sleep(0.05)
        replies = [json.loads(line) for line in sink.getvalue().splitlines()]
    return sorted(replies, key=lambda reply: reply["id"])


def request(ident: int, method: str, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}


def test_the_handshake_names_altocue_and_its_tools():
    replies = talk(
        request(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        request(2, "tools/list"),
        request(3, "prompts/list"),
        request(4, "initialize", {"protocolVersion": "1999-01-01"}),
    )
    first = replies[0]["result"]
    assert first["protocolVersion"] == "2025-06-18"
    assert first["serverInfo"]["name"] == "altocue" and first["serverInfo"]["version"] == __version__
    assert "altocue_doctor" in first["instructions"]
    tools = {tool["name"] for tool in replies[1]["result"]["tools"]}
    assert tools == {"altocue_doctor", "altocue_install", "altocue_run", "altocue_job", "altocue_stop", "altocue_guide"}
    skills = {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}
    assert {prompt["name"] for prompt in replies[2]["result"]["prompts"]} == skills
    assert replies[3]["result"]["protocolVersion"] == mcp.PROTOCOLS[0]


def test_the_server_says_what_went_wrong():
    replies = talk(
        request(1, "tools/call", {"name": "no_such_tool", "arguments": {}}),
        request(2, "no/such/method"),
        request(3, "tools/call", {"name": "altocue_install", "arguments": {}}),
        request(4, "tools/call", {"name": "altocue_run", "arguments": {"args": "status"}}),
        request(5, "tools/call", {"name": "altocue_guide", "arguments": {"topic": "../../pyproject.toml"}}),
    )
    assert replies[0]["error"]["code"] == -32602
    assert replies[1]["error"]["code"] == -32601
    install = replies[2]["result"]
    assert install["isError"] and "Ask the user first" in install["structuredContent"]["error"]
    assert replies[3]["result"]["isError"]
    assert replies[4]["result"]["isError"]


def test_a_guide_is_the_skill_with_links_that_open_on_github():
    found = mcp.tool_guide({"topic": "altocue"})
    assert "altocue doctor" in found["guide"] and "altocue_doctor" in found["guide"]
    links = re.findall(r"\]\(([^)\s]+)\)", found["guide"])
    assert links and all(link.startswith(("https://", "#", "mailto:")) for link in links)
    for link in links:
        if link.startswith(mcp.REPO + "/blob/main/"):
            target = link.removeprefix(mcp.REPO + "/blob/main/").split("#")[0]
            assert (ROOT / target).exists(), f"{link} points at a file this repo does not have"
    listed = mcp.tool_guide({"topic": "index"})["guides"]
    assert {entry["topic"] for entry in listed} == {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}


@pytest.fixture
def fake_altocue(tmp_path, monkeypatch):
    """A stand-in altocue that prints its arguments, and sleeps when asked to render."""
    script = tmp_path / "altocue"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "altocue 0.0.1"; exit 0; fi\n'
        'if [ "$1" = "render" ]; then echo started; sleep 30; fi\n'
        'echo "ran $*"\n'
        'if [ "$1" = "qa" ]; then exit 3; fi\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("ALTOCUE_BIN", str(script))
    monkeypatch.setenv("ALTOCUE_MCP_HOME", str(tmp_path / "state"))
    yield script
    mcp.stop_all()


def test_a_command_runs_and_reports_its_output(fake_altocue, tmp_path):
    found = mcp.tool_run({"args": ["status", "-p", str(tmp_path)], "cwd": str(tmp_path), "wait_seconds": 20})
    assert found["state"] == "finished" and found["exit_code"] == 0
    assert f"ran status -p {tmp_path}" in found["output"]
    assert found["command"] == f"altocue status -p {tmp_path}"


def test_a_failed_command_is_an_error_result(fake_altocue, tmp_path):
    answer = mcp.call({"name": "altocue_run", "arguments": {"args": ["qa"], "cwd": str(tmp_path), "wait_seconds": 20}})
    assert answer["isError"] and answer["structuredContent"]["exit_code"] == 3


def test_heavy_jobs_run_one_at_a_time_and_stop_cleanly(fake_altocue, tmp_path):
    render = mcp.tool_run({"args": ["render"], "cwd": str(tmp_path), "wait_seconds": 0})
    assert render["state"] == "running" and "altocue_job" in render["next"]
    with pytest.raises(mcp.ToolError, match="one at a time"):
        mcp.tool_run({"args": ["clean"], "cwd": str(tmp_path), "wait_seconds": 0})
    light = mcp.tool_run({"args": ["status"], "cwd": str(tmp_path), "wait_seconds": 20})
    assert light["state"] == "finished", "a light command runs beside a render"
    stopped = mcp.tool_stop({"job": render["job"]})
    assert stopped["state"] == "stopped"
    assert mcp.tool_run({"args": ["clean"], "cwd": str(tmp_path), "wait_seconds": 20})["state"] == "finished"


def test_the_server_never_runs_itself_or_a_missing_folder(fake_altocue, tmp_path):
    with pytest.raises(mcp.ToolError):
        mcp.tool_run({"args": ["mcp"]})
    with pytest.raises(mcp.ToolError, match="not a folder"):
        mcp.tool_run({"args": ["status"], "cwd": str(tmp_path / "missing")})


def test_doctor_without_altocue_says_how_to_install(monkeypatch, tmp_path):
    monkeypatch.delenv("ALTOCUE_BIN", raising=False)
    monkeypatch.setattr(mcp, "find", lambda name: None)
    found = mcp.tool_doctor({})
    assert found["installed"] is False
    assert "altocue_install" in found["next"] and "Astral" in found["next"]


def test_the_plugin_server_starts_on_a_bare_path(tmp_path):
    """The way a plugin starts it: the system Python, the plugin folder and no altocue anywhere on PATH."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ALTOCUE_MCP_HOME": str(tmp_path / "state")}
    message = json.dumps(request(1, "initialize", {"protocolVersion": "2025-06-18"})) + "\n"
    done = subprocess.run([sys.executable, str(SERVER)], input=message, capture_output=True, text=True, timeout=60, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.splitlines()[0])["result"]["serverInfo"]["version"] == __version__
