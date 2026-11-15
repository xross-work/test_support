# Copyright 2026 XMOS LIMITED.
# This Software is subject to the terms of the XMOS Public Licence: Version 1.
"""Lifecycle tests of Session with stand-ins for xrun, xflash and xgdb; no hardware or XTC Tools."""
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from Pyxrun import Session, SessionError, SessionTimeout
from Pyxrun import run

pytestmark = pytest.mark.skipif(os.name == "nt", reason="stand-in tools are POSIX scripts")

XRUN = r'''
import os, signal, subprocess, sys, time
mode = os.environ.get("FAKE_XRUN", "exit0")
Path = __import__("pathlib").Path
Path(os.environ["FAKE_DIR"], "xrun.args").write_text("\n".join(sys.argv[1:]))
Path(os.environ["FAKE_DIR"], "xrun.pid").write_text(str(os.getpid()))
if mode == "exit0":
    print("hello"); print("PASS")
elif mode == "exit7":
    print("hello"); print("launcher failed", file=sys.stderr); sys.exit(7)
elif mode == "flood":
    sys.stdout.write("x" * 79 + "\n" and ("x" * 79 + "\n") * 52000); print("PASS")
elif mode == "serve":
    print("ready", flush=True); print("RESULT 42", flush=True); time.sleep(600)
elif mode == "silent":
    time.sleep(600)
elif mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(600)
elif mode == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    Path(os.environ["FAKE_DIR"], "child.pid").write_text(str(child.pid))
    print("ready", flush=True); time.sleep(600)
elif mode == "exception":
    print("xrun: Application exception", file=sys.stderr); sys.exit(255)
elif mode == "partial":
    sys.stdout.write("no newline"); sys.stderr.write("line\r\n")
elif mode == "cwd":
    print(os.getcwd())
elif mode == "noise":
    while True:
        print("running", flush=True); time.sleep(0.001)
elif mode == "noisy_tail":
    def stop(*args):
        print("shutdown diagnostic", flush=True); sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    print("ready\nPASS\nafter completion", flush=True); time.sleep(600)
elif mode == "stderr_marker":
    print("PASS", file=sys.stderr, flush=True); time.sleep(600)
elif mode == "late_exception":
    def stop(*args):
        print("Application exception", file=sys.stderr, flush=True); sys.exit(255)
    signal.signal(signal.SIGTERM, stop)
    print("PASS", flush=True); time.sleep(600)
elif mode == "progress":
    print("ready", flush=True); time.sleep(0.1)
    print("progress", flush=True); time.sleep(600)
elif mode == "closed_pipes":
    os.close(1); os.close(2); time.sleep(600)
'''

XGDB = r'''
import os, sys, time
Path = __import__("pathlib").Path
with Path(os.environ["FAKE_DIR"], "xgdb.calls").open("a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
mode = os.environ.get("FAKE_XGDB", "ok")
if mode == "fail":
    print("cannot connect", file=sys.stderr); sys.exit(1)
if mode == "hang":
    time.sleep(600)
'''


@pytest.fixture
def tools(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("xrun", XRUN), ("xflash", XRUN), ("xgdb", XGDB)):
        script = bin_dir / name
        script.write_text(f"#!{sys.executable}\n{body}")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    monkeypatch.setattr(Session, "stop_grace", 1)
    xe = tmp_path / "app.xe"
    xe.write_bytes(b"XE")

    class Tools:
        path = tmp_path

        def app(self, mode="exit0", attach="xscope", **options):
            monkeypatch.setenv("FAKE_XRUN", mode)
            return Session(xe, "SERIAL", attach=attach, **options)

        def args(self):
            return (tmp_path / "xrun.args").read_text().splitlines()

        def xgdb_calls(self):
            calls = tmp_path / "xgdb.calls"
            return calls.read_text().splitlines() if calls.exists() else []

        def alive(self, name="xrun"):
            try:
                os.kill(int((tmp_path / f"{name}.pid").read_text()), 0)
            except ProcessLookupError:
                return False
            return True

    return Tools()


def gone(tools, name="xrun"):
    deadline = time.monotonic() + 5
    while tools.alive(name) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not tools.alive(name)


# ---- Existing behaviour ------------------------------------------------------------------

def test_unattached_run_and_recovery(tools):
    with tools.app(attach=None) as app:
        assert app.proc is None and "PASS" in app.proc_stdout
    assert tools.args() == ["--adapter-id", "SERIAL", str(app.xe_path)]
    assert tools.xgdb_calls() == ["-ex connect --adapter-id SERIAL --batch"]


def test_unattached_failure_is_reported(tools):
    with pytest.raises(AssertionError, match="xrun failed"):
        with tools.app("exit7", attach=None):
            pass


def test_xflash_commands(tools):
    with tools.app(attach=None, xflash=True):
        pass
    assert tools.args() == ["--adapter-id", "SERIAL", "--factory", str(tools.path / "app.xe")]
    with tools.app(attach=None, xflash=True, writeall=True, target="XK-EVK-XU316"):
        pass
    assert tools.args()[2:] == ["--write-all", str(tools.path / "app.xe"), "--target", "XK-EVK-XU316"]


@pytest.mark.parametrize("attach, flags", [("io", ["--io"]), ("xscope", ["--xscope"])])
def test_attach_commands_and_terminated_app_passes(tools, attach, flags):
    with tools.app("serve", attach=attach) as app:
        app.wait_for_output("ready")
    assert tools.args() == ["--adapter-id", "SERIAL", *flags, str(app.xe_path)]
    assert gone(tools)
    assert "RESULT 42" in app.proc_stdout and app.proc_stderr == ""
    assert app.proc_returncode != 0  # Stopped here, so the status is not an error.
    assert len(tools.xgdb_calls()) == 1


def test_xscope_io_only_flag(tools):
    with tools.app(xscope_io_only=True) as app:
        app.wait()
    assert tools.args()[2:4] == ["--xscope", "--xscope-io-only"]
    with pytest.raises(AssertionError):
        tools.app(attach="io", xscope_io_only=True)


def test_application_arguments_follow_the_args_flag(tools):
    with tools.app(appargs=["12345", 6]) as app:
        app.wait()
    assert tools.args() == ["--adapter-id", "SERIAL", "--xscope", "--args", str(app.xe_path), "12345", "6"]
    with tools.app(attach=None, appargs=["x"]) as app:
        pass
    assert tools.args() == ["--adapter-id", "SERIAL", "--args", str(app.xe_path), "x"]
    with pytest.raises(AssertionError):
        tools.app(attach=None, xflash=True, appargs=["x"])


def test_target_exception_is_reported(tools):
    with pytest.raises(AssertionError, match="xrun process failed"):
        with tools.app("exception") as app:
            time.sleep(0.5)
    assert app.proc_returncode == 255


def test_xgdb_failure_and_timeout_are_reported(tools, monkeypatch):
    monkeypatch.setenv("FAKE_XGDB", "fail")
    with pytest.raises(AssertionError, match="Connecting to XTAG SERIAL failed"):
        with tools.app("serve") as app:
            app.wait_for_output("ready")
    monkeypatch.setenv("FAKE_XGDB", "hang")
    monkeypatch.setattr(Session, "xgdb_timeout", 0.5)
    with pytest.raises(AssertionError, match="timed out"):
        with tools.app("serve") as app:
            app.wait_for_output("ready")
    assert gone(tools)


# ---- Output ------------------------------------------------------------------------------

def test_large_output_does_not_block_the_application(tools):
    with tools.app("flood") as app:
        app.wait(timeout=20)  # More than 4 MiB; a pipe holds 64 KiB.
        assert app.lines[-1] == "PASS" and len(app.lines) == 52001
    assert len(app.proc_stdout) == 52000 * 80 + 5


def test_lines_from_both_streams_without_final_newline(tools):
    with tools.app("partial") as app:
        app.wait()
    assert sorted(app.lines) == ["line", "no newline"]
    assert app.proc_stdout == "no newline" and app.proc_stderr == "line\r\n"


def test_log_is_written_while_running_and_kept_on_failure(tools):
    log = tools.path / "logs" / "run.log"
    with pytest.raises(RuntimeError):
        with tools.app("serve", log_path=log) as app:
            app.wait_for_output("RESULT")
            assert b"RESULT 42" in log.read_bytes()
            raise RuntimeError("test failed")
    assert b"RESULT 42" in log.read_bytes()
    with tools.app(attach=None, log_path=log):
        pass
    assert log.read_text() == "hello\nPASS\n"


def test_cwd_is_where_xrun_runs(tools):
    work = tools.path / "work"
    work.mkdir()
    with tools.app("cwd", cwd=work) as app:
        app.wait()
    assert Path(app.lines[0]).resolve() == work.resolve()


# ---- Deadlines and completion ------------------------------------------------------------

def test_wait_returns_for_a_finished_application(tools):
    with tools.app() as app:
        app.wait()
        assert app.lines == ["hello", "PASS"]
    assert app.proc_returncode == 0
    assert app.proc.stdout.closed and app.proc.stderr.closed


def test_wait_enforces_its_deadline(tools):
    started = time.monotonic()
    with pytest.raises(SessionTimeout, match="did not finish within 0.5s"):
        with tools.app("silent") as app:
            app.wait(timeout=0.5)
    assert time.monotonic() - started < 10 and gone(tools)


def test_wait_reports_a_failing_exit_status(tools):
    with pytest.raises(SessionError, match="status 7") as failure:
        with tools.app("exit7") as app:
            app.wait()
    assert "launcher failed" in str(failure.value)


def test_unexpected_exit_fails_even_when_the_test_does_not_wait(tools):
    with pytest.raises(AssertionError, match="xrun exited with status 7"):
        with tools.app("exit7"):
            time.sleep(0.5)


def test_wait_for_output_matchers_continue_from_the_last_match(tools):
    import re
    with tools.app("serve") as app:
        assert app.wait_for_output("ready") == "ready"
        assert app.wait_for_output(re.compile(r"RESULT (\d+)")) == "RESULT 42"
        with pytest.raises(SessionTimeout):
            app.wait_for_output(lambda line: line.startswith("RESULT"), timeout=0.3)


def test_wait_for_output_fails_when_output_ends(tools):
    with pytest.raises(SessionError, match="Output ended"):
        with tools.app() as app:
            app.wait_for_output("never printed")


def test_max_runtime_stops_a_test_that_does_not_wait(tools):
    with pytest.raises(SessionTimeout, match="max_runtime of 0.5s"):
        with tools.app("silent", max_runtime=0.5):
            time.sleep(1.5)
    assert gone(tools)


# ---- Start-up and teardown ---------------------------------------------------------------

def test_startup_failure_does_not_leave_xrun_running(tools, monkeypatch):
    def fail(self, timeout=10):
        while not (tools.path / "xrun.pid").exists():
            time.sleep(0.01)
        raise RuntimeError("port never opened")
    monkeypatch.setattr(Session, "wait_for_xscope_port", fail)
    with pytest.raises(RuntimeError, match="port never opened"):
        tools.app("silent", attach="xscope_app").__enter__()
    assert gone(tools) and len(tools.xgdb_calls()) == 1


def test_xscope_port_wait_stops_when_xrun_exits(tools):
    started = time.monotonic()
    with pytest.raises(AssertionError, match="before its xscope port"):
        tools.app("exit7", attach="xscope_app").__enter__()
    assert time.monotonic() - started < 5


def test_xrun_ignoring_the_stop_request_is_killed_and_target_recovered(tools):
    with tools.app("stubborn") as app:
        app.wait_for_output("ready")
    assert gone(tools) and len(tools.xgdb_calls()) == 1


def test_helpers_started_by_xrun_are_stopped(tools):
    with tools.app("child") as app:
        app.wait_for_output("ready")
    assert gone(tools) and gone(tools, "child")


def test_teardown_failure_does_not_replace_the_test_failure(tools, monkeypatch):
    monkeypatch.setenv("FAKE_XGDB", "fail")
    with pytest.raises(ValueError, match="the real failure") as failure:
        with tools.app("serve") as app:
            app.wait_for_output("ready")
            raise ValueError("the real failure")
    assert gone(tools)
    if hasattr(failure.value, "__notes__"):
        assert "Connecting to XTAG SERIAL failed" in failure.value.__notes__[0]


def test_terminate_twice_is_harmless(tools):
    with tools.app("serve") as app:
        app.wait_for_output("ready")
        app.terminate()
    assert len(tools.xgdb_calls()) == 2


@pytest.mark.parametrize("mode, expected", [("exit0", True), ("exit7", False),
                                           ("exception", False), ("silent", False)])
def test_run_entry_point_uses_lifecycle_and_keeps_logs(tools, monkeypatch, mode, expected):
    from Pyxsim.testers import ComparisonTester

    monkeypatch.setenv("FAKE_XRUN", mode)
    log = tools.path / "run.log"
    assert run(tools.path / "app.xe", adapter_id="SERIAL", timeout=0.5,
               tester=ComparisonTester(["hello", "PASS"]), log_path=log) is expected
    assert log.exists()
    assert gone(tools)
    assert len(tools.xgdb_calls()) == 1


def test_run_entry_point_fails_when_recovery_fails(tools, monkeypatch):
    monkeypatch.setenv("FAKE_XGDB", "fail")
    assert run(tools.path / "app.xe", adapter_id="SERIAL") is False


def test_run_until_stops_and_compares_at_completion(tools, monkeypatch):
    from Pyxsim.testers import ComparisonTester

    monkeypatch.setenv("FAKE_XRUN", "noisy_tail")
    log = tools.path / "observation.log"
    assert run(tools.path / "app.xe", adapter_id="SERIAL", timeout=3,
               until=lambda line: line == "PASS",
               tester=ComparisonTester(["ready", "PASS"]), log_path=log)
    assert "after completion" in log.read_text()
    assert "shutdown diagnostic" in log.read_text()
    assert gone(tools) and len(tools.xgdb_calls()) == 1


@pytest.mark.parametrize("mode", ["exit0", "stderr_marker", "closed_pipes"])
def test_run_until_does_not_accept_missing_stdout_observation(tools, monkeypatch, mode):
    monkeypatch.setenv("FAKE_XRUN", mode)
    marker = "never printed" if mode == "exit0" else "PASS"
    assert not run(tools.path / "app.xe", adapter_id="SERIAL", timeout=0.5,
                   until=marker, log_path=tools.path / "failed-observation.log")
    assert gone(tools) and len(tools.xgdb_calls()) == 1


def test_run_until_does_not_accept_target_exception_after_marker(tools, monkeypatch):
    monkeypatch.setenv("FAKE_XRUN", "late_exception")
    assert not run(tools.path / "app.xe", adapter_id="SERIAL", until="PASS", timeout=3)
    assert gone(tools) and len(tools.xgdb_calls()) == 1


@pytest.mark.parametrize("error", [AssertionError("invalid report"), ValueError("bad report"),
                                  KeyboardInterrupt("interrupted")])
def test_run_until_callback_errors_preserve_logs_and_recover(tools, monkeypatch, error):
    monkeypatch.setenv("FAKE_XRUN", "serve")
    log = tools.path / "callback-failure.log"

    def reject(line):
        raise error

    with pytest.raises(type(error), match=str(error)):
        run(tools.path / "app.xe", adapter_id="SERIAL", until=reject, timeout=3, log_path=log)
    assert "ready" in log.read_text()
    assert gone(tools) and len(tools.xgdb_calls()) == 1


def test_continuous_output_does_not_extend_deadline(tools):
    with tools.app("noise") as app:
        started = time.monotonic()

        def never_matches(line):
            time.sleep(0.01)  # The producer outpaces the callback.
            return False

        with pytest.raises(SessionTimeout):
            app.wait_for_output(never_matches, timeout=0.3)
        assert time.monotonic() - started < 3


def test_output_keeps_draining_while_match_callback_runs(tools):
    with tools.app("progress") as app:
        def completed(line):
            if line != "ready":
                return False
            deadline = time.monotonic() + 2
            while "progress" not in app.lines and time.monotonic() < deadline:
                time.sleep(0.01)
            assert "progress" in app.lines
            return True

        assert app.wait_for_output(completed, timeout=3) == "ready"


def test_invalid_observation_options(tools):
    with tools.app("serve") as app:
        with pytest.raises(TypeError, match="matcher"):
            app.wait_for_output(False)
        with pytest.raises(ValueError, match="stream"):
            app.wait_for_output("ready", stream="invalid")
