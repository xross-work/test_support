# Copyright 2026 XMOS LIMITED.
# This Software is subject to the terms of the XMOS Public Licence: Version 1.
"""The public run helper delegates execution and recovery to Session."""
import re
from types import SimpleNamespace

import pytest

import Pyxrun
from Pyxsim.testers import AssertiveComparisonTester, ComparisonTester


@pytest.fixture
def hardware(tmp_path, monkeypatch):
    xe = tmp_path / "firmware with spaces.xe"
    xe.write_bytes(b"XE")
    state = SimpleNamespace(options=None, closed=False, failure=None, until_calls=[],
                            output="  ready  \n\nPASS\n", diagnostic="host diagnostic\n")

    class App:
        def __init__(self, executable, adapter, **options):
            assert executable == xe
            assert adapter == "SERIAL"
            state.options = options

        def __enter__(self):
            return self

        def wait(self, timeout=None):
            assert 0 < timeout <= state.options["timeout"]
            if state.failure:
                raise state.failure

        def wait_for_output(self, match, timeout=None, *, stream=None):
            state.until_calls.append((timeout, stream))
            self.wait(timeout)
            for line in state.output.splitlines():
                if match(line):
                    return line
            raise Pyxrun.SessionError("Output ended before the awaited line")

        def __exit__(self, *args):
            self.proc_stdout = state.output
            self.proc_stderr = state.diagnostic
            state.closed = True

    monkeypatch.setattr(Pyxrun, "Session", App)
    state.run = lambda **options: Pyxrun.run(xe, adapter_id="SERIAL", **options)
    state.xe = xe
    return state


@pytest.mark.parametrize("assertive", [False, True])
def test_run_uses_existing_checkers_and_returns_bool(hardware, assertive, capsys):
    checker = (AssertiveComparisonTester(["ready", "PASS"], False, True) if assertive
               else ComparisonTester(["ready", "PASS"]))
    assert hardware.run(tester=checker) is True
    assert hardware.closed
    assert hardware.options["attach"] == "xscope"
    assert hardware.options["xscope_io_only"] is False
    assert hardware.options["timeout"] == 600
    assert "host diagnostic" in capsys.readouterr().err


def test_run_checks_only_after_teardown(hardware):
    def check(lines):
        assert hardware.closed
        assert lines == ["ready", "PASS"]

    assert hardware.run(tester=SimpleNamespace(run=check)) is True


def test_run_without_checker(hardware):
    assert hardware.run() is True


def test_run_forwards_hardware_options(hardware, tmp_path):
    work = tmp_path / "work"
    log = tmp_path / "run.log"
    assert hardware.run(timeout=4, cwd=work, log_path=log, xscope_io_only=True)
    assert hardware.options == dict(attach="xscope", timeout=4, cwd=work,
                                   log_path=log, xscope_io_only=True, appargs=None)
    assert hardware.run(appargs=["12345"])
    assert hardware.options["appargs"] == ["12345"]


def test_run_legacy_jtag_is_explicit(hardware):
    assert hardware.run(attach="io")
    assert hardware.options["attach"] == "io"


def test_boolean_comparison_failure(hardware):
    assert hardware.run(tester=ComparisonTester(["wrong"])) is False


def test_assertive_comparison_failure_propagates_after_teardown(hardware):
    with pytest.raises(AssertionError):
        hardware.run(tester=AssertiveComparisonTester(["wrong"], False, True))
    assert hardware.closed


@pytest.mark.parametrize("error", [Pyxrun.SessionTimeout("deadline"),
                                  Pyxrun.SessionError("exit 7"),
                                  AssertionError("target exception")])
def test_execution_failure_returns_false_without_checking(hardware, capsys, error):
    hardware.failure = error

    def check(lines):
        pytest.fail("must not compare failed execution")

    assert hardware.run(tester=SimpleNamespace(run=check)) is False
    assert hardware.closed
    assert str(error) in capsys.readouterr().err


def test_launch_errors_propagate(hardware):
    hardware.failure = FileNotFoundError("xrun not found")
    with pytest.raises(FileNotFoundError, match="xrun not found"):
        hardware.run()


@pytest.mark.parametrize("options", [
    {"timeout": 0}, {"timeout": -1}, {"timeout": float("nan")},
    {"timeout": float("inf")}, {"attach": None}, {"attach": "xscope_app"},
    {"attach": "io", "xscope_io_only": True}, {"tester": object()}, {"until": False},
])
def test_invalid_options_fail_before_execution(hardware, options):
    with pytest.raises((ValueError, TypeError)):
        hardware.run(**options)
    assert hardware.options is None


@pytest.mark.parametrize("adapter", [None, "", " ", 0])
def test_adapter_required(hardware, adapter):
    with pytest.raises(ValueError, match="adapter_id"):
        Pyxrun.run(hardware.xe, adapter_id=adapter)
    assert hardware.options is None


def test_missing_executable(hardware):
    hardware.xe.unlink()
    with pytest.raises(FileNotFoundError):
        hardware.run()
    assert hardware.options is None


def test_log_cannot_overwrite_executable(hardware):
    with pytest.raises(ValueError, match="overwrite"):
        hardware.run(log_path=hardware.xe)
    assert hardware.xe.read_bytes() == b"XE"
    assert hardware.options is None


@pytest.mark.parametrize("match", ["PASS", re.compile(r"^PASS$"),
                                 lambda line: line == "PASS"])
def test_until_compares_only_through_completion(hardware, match):
    hardware.output += "not part of comparison\n"

    def check(lines):
        assert hardware.closed
        assert lines == ["ready", "PASS"]

    assert hardware.run(until=match, tester=SimpleNamespace(run=check)) is True
    assert hardware.until_calls[0][1] == "stdout"


def test_until_requires_observation_even_on_clean_exit(hardware):
    assert hardware.run(until="never printed") is False
    assert hardware.closed


@pytest.mark.parametrize("error", [AssertionError("invalid report"), ValueError("bad report"),
                                  KeyboardInterrupt("interrupted")])
def test_until_callback_errors_propagate_after_cleanup(hardware, error):
    def reject(line):
        raise error

    with pytest.raises(type(error), match=str(error)):
        hardware.run(until=reject)
    assert hardware.closed
