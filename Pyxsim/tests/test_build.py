# Copyright 2026 XMOS LIMITED.
# This Software is subject to the terms of the XMOS Public Licence: Version 1.
"""build() with a stand-in for cmake; no XTC Tools or real build."""
import os
import stat
import sys

import pytest

import Pyxsim
from Pyxsim import BuildError

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the stand-in tool is a POSIX script")

CMAKE = r"""
import os, sys, time
from pathlib import Path
Path(os.environ["FAKE_DIR"], "cmake.args").write_text("\n".join(sys.argv[1:]))
mode = os.environ.get("FAKE_CMAKE", "ok")
if mode == "fail":
    print("main.c:3: error: expected ';'"); sys.exit(2)
if mode == "hang":
    time.sleep(600)
if mode == "ok":
    xe = Path(os.environ["FAKE_XE"]); xe.parent.mkdir(parents=True, exist_ok=True); xe.write_bytes(b"XE")
print("built")
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "cmake"
    script.write_text(f"#!{sys.executable}\n{CMAKE}")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    tests = tmp_path / "tests"
    (tests / "build").mkdir(parents=True)
    (tests / "build" / "CMakeCache.txt").write_text("")
    xe = tests / "app" / "bin" / "fast" / "app_fast.xe"
    monkeypatch.setenv("FAKE_XE", str(xe))
    return tmp_path, xe


def args(root):
    return (root / "cmake.args").read_text().splitlines()


def test_builds_the_target_in_the_nearest_build_directory(project):
    root, xe = project
    assert Pyxsim.build(xe) is None
    assert args(root) == ["--build", str((root / "tests" / "build").resolve()), "--target", "app_fast"]
    assert xe.is_file()


def test_explicit_build_directory(project):
    root, xe = project
    other = root / "build-other"
    other.mkdir()
    (other / "CMakeCache.txt").write_text("")
    Pyxsim.build(xe, build_dir=other)
    assert args(root)[1] == str(other.resolve())


def test_unconfigured_build_directory(project):
    root, xe = project
    (root / "tests" / "build" / "CMakeCache.txt").unlink()
    with pytest.raises(BuildError, match="run cmake first"):
        Pyxsim.build(xe)
    with pytest.raises(BuildError, match="not a configured CMake build directory"):
        Pyxsim.build(xe, build_dir=root / "tests" / "build")
    assert not (root / "cmake.args").exists()


def test_failure_carries_the_build_output(project, monkeypatch):
    _, xe = project
    monkeypatch.setenv("FAKE_CMAKE", "fail")
    with pytest.raises(BuildError, match=r"status 2\n.*expected ';'"):
        Pyxsim.build(xe)


def test_success_without_the_executable_is_a_failure(project, monkeypatch):
    _, xe = project
    monkeypatch.setenv("FAKE_CMAKE", "no-output")
    with pytest.raises(BuildError, match="did not produce"):
        Pyxsim.build(xe)


def test_deadline(project, monkeypatch):
    _, xe = project
    monkeypatch.setenv("FAKE_CMAKE", "hang")
    with pytest.raises(BuildError, match="exceeded 0.5 seconds"):
        Pyxsim.build(xe, timeout=0.5)
    with pytest.raises(ValueError):
        Pyxsim.build(xe, timeout=0)
