from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from altocue import swift

HELLO = 'print("hello")\n'


def test_there_is_no_helper_off_a_mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    assert swift.compiled(tmp_path / "x.swift", "x") is None


def test_there_is_no_helper_without_a_compiler(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(swift.shutil, "which", lambda name: None)
    assert swift.compiled(tmp_path / "x.swift", "x") is None


def test_a_failed_compile_leaves_nothing_behind(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(swift.shutil, "which", lambda name: "/usr/bin/false")
    source = tmp_path / "x.swift"
    source.write_text(HELLO)
    monkeypatch.setattr(swift, "COMPILER", "/usr/bin/false")
    assert swift.compiled(source, "x") is None
    assert not list((tmp_path / "altocue").glob("x-*"))


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("swiftc"), reason="needs swiftc")
def test_a_helper_compiles_once_per_version_of_its_source(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    source = tmp_path / "hello.swift"
    source.write_text(HELLO)
    first = swift.compiled(source, "hello")
    assert first is not None and first.exists() and first.parent == Path(tmp_path) / "altocue"
    stamp = first.stat().st_mtime_ns
    assert swift.compiled(source, "hello") == first and first.stat().st_mtime_ns == stamp
    source.write_text(HELLO + 'print("again")\n')
    assert swift.compiled(source, "hello") != first
