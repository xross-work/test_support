# Copyright 2026 XMOS LIMITED.
# This Software is subject to the terms of the XMOS Public Licence: Version 1.
"""Exercise the run-only entry point without loading XSI or invoking a build."""
from types import SimpleNamespace

import pytest

import Pyxsim
from Pyxsim.testers import AssertiveComparisonTester, ComparisonTester


@pytest.fixture
def simulator(monkeypatch):
    state = SimpleNamespace(calls=[], success=True)

    def execute(xe, threads, **options):
        state.calls.append((xe, threads, options))
        print("  ready  \n\nPASS")
        return state.success

    def build(*args, **kwargs):
        pytest.fail("run() must not build")

    monkeypatch.setattr(Pyxsim, "run_with_pyxsim", execute)
    monkeypatch.setattr(Pyxsim, "_build", build)
    return state


@pytest.mark.parametrize("assertive", [False, True])
@pytest.mark.filterwarnings("error::DeprecationWarning")
def test_run_checks_output_and_returns_bool(simulator, capfd, assertive):
    checker = (AssertiveComparisonTester(["ready", "PASS"], False, True) if assertive
               else ComparisonTester(["ready", "PASS"]))
    assert Pyxsim.run("test.xe", tester=checker, capfd=capfd, timeout=4) is True
    assert simulator.calls == [("test.xe", [], {"timeout": 4})]


def test_run_forwards_backend_options(simulator):
    threads = [object()]
    assert Pyxsim.run("test.xe", simthreads=threads, simargs=["--max-cycles", "100"],
                      appargs=["input"], plugins=[]) is True
    assert simulator.calls == [("test.xe", threads,
                                dict(timeout=600, simargs=["--max-cycles", "100"],
                                     appargs=["input"], plugins=[]))]


def test_run_boolean_comparison_failure(simulator, capfd):
    assert Pyxsim.run("test.xe", tester=ComparisonTester(["wrong"]), capfd=capfd) is False


def test_run_assertive_comparison_failure(simulator, capfd):
    with pytest.raises(AssertionError):
        Pyxsim.run("test.xe", tester=AssertiveComparisonTester(["wrong"], False, True),
                   capfd=capfd)


def test_failed_execution_is_not_compared(simulator, capfd):
    simulator.success = False

    def check(lines):
        pytest.fail("must not compare failed execution")

    assert Pyxsim.run("test.xe", tester=SimpleNamespace(run=check), capfd=capfd) is False


def test_checker_cannot_be_silently_skipped(simulator):
    with pytest.raises(ValueError, match="capfd is required"):
        Pyxsim.run("test.xe", tester=ComparisonTester(["wrong"]))
    assert not simulator.calls


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_deadline_is_finite_and_positive(simulator, timeout):
    with pytest.raises(ValueError, match="timeout"):
        Pyxsim.run("test.xe", timeout=timeout)
    assert not simulator.calls


def test_run_rejects_implicit_build(simulator):
    with pytest.raises(ValueError, match="does not build"):
        Pyxsim.run("test.xe", do_xe_prebuild=True)
    assert not simulator.calls


def test_invalid_checker(simulator, capfd):
    with pytest.raises(TypeError, match="tester"):
        Pyxsim.run("test.xe", tester=object(), capfd=capfd)
    assert not simulator.calls


def test_legacy_run_only_return_value_is_unchanged(simulator, capfd):
    checker = AssertiveComparisonTester(["ready", "PASS"], False, True)
    with pytest.warns(DeprecationWarning, match="run_on_simulator_.*deprecated"):
        assert Pyxsim.run_on_simulator_("test.xe", tester=checker, capfd=capfd) is None


def test_build_and_run_requests_build_without_warning(monkeypatch, recwarn):
    calls = []

    def execute(*args, **kwargs):
        calls.append((args, kwargs))
        return False

    monkeypatch.setattr(Pyxsim, "_run_on_simulator", execute)
    assert Pyxsim.run_on_simulator("test.xe") is False
    assert not recwarn.list
    assert calls == [(("test.xe",), {"do_xe_prebuild": True})]
