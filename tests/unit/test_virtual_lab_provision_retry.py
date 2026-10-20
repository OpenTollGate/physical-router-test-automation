"""Offline unit tests for the virtual-lab serial-provisioning timeout
hardening (tollgate-module-basic-go#1005, class A).

The N=39 vlab soak (issue comments
https://github.com/OpenTollGate/tollgate-module-basic-go/issues/1005#issuecomment-6268392132
and #issuecomment-6276234775) hit the fixed 120s cap on
``run_python_on_host(host, provision_script, timeout=120)`` under host load
~36 / swap 94%: ``subprocess.TimeoutExpired: Command '['ssh', 'ai-legion',
'python3']' timed out after 120 seconds`` — while the DUT was booted and
fine; the next soak iteration was green.  Pure tooling timeout.

These tests pin the replacement, everything offline (no SSH, no qemu, no
router):

* the ssh branch of ``run_python_on_host`` converts a host-side cap hit
  into the TIMEOUT sentinel CommandResult instead of crashing start_poc
  with a raw ``subprocess.TimeoutExpired`` (the observed traceback class);
* the per-attempt cap for serial provisioning is raised (>= 300s, env
  overridable) and provisioning retries exactly once on the TIMEOUT
  sentinel with backoff — safe because the provisioning scripts are
  idempotent (an already-provisioned boot exits 0 early);
* genuine script failures (dead serial socket, boot prompt never seen)
  do NOT retry — dead DUTs fail fast;
* ``start_poc`` wires step 2 through the raised cap + retry end to end.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "virtual-lab.py"

_spec = importlib.util.spec_from_file_location("virtual_lab_retry_test", SCRIPT)
vl = importlib.util.module_from_spec(_spec)
sys.modules["virtual_lab_retry_test"] = vl
_spec.loader.exec_module(vl)


class Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _no_sleep(_seconds: float) -> None:
    pass  # keep the retry backoff out of the test wall clock


# ------------------------------------------------------- run_python_on_host ssh

def test_run_python_on_host_ssh_timeout_is_sentinel_not_crash(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The #1005 traceback: TimeoutExpired escaped the ssh branch and crashed
    start_poc.  It must return the TIMEOUT sentinel like the local branch."""
    def fake_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd=["ssh", "ai-legion", "python3"], timeout=120)

    monkeypatch.setattr(vl.subprocess, "run", fake_run)
    result = vl.run_python_on_host("ai-legion", "print('hi')", timeout=120)
    assert result.returncode == 1
    assert result.stderr == "TIMEOUT"


def test_run_python_on_host_local_timeout_still_sentinel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(cmd=["python3"], timeout=120)

    monkeypatch.setattr(vl.subprocess, "run", fake_run)
    result = vl.run_python_on_host("local", "print('hi')", timeout=120)
    assert result.returncode == 1
    assert result.stderr == "TIMEOUT"


# ------------------------------------------------------------- the raised bound

def test_serial_provision_cap_is_raised() -> None:
    """The flake fired at a fixed 120s; the new default cap must be
    comfortably above it (300s covers >2x the soak's worst healthy cycle)."""
    assert vl.SERIAL_PROVISION_TIMEOUT_S >= 300


def test_serial_provision_attempts_are_bounded() -> None:
    """Exactly one retry — bounded wall clock, not an unbounded poll."""
    assert vl.SERIAL_PROVISION_ATTEMPTS == 2


# ------------------------------------------------------------- retry semantics

def test_retry_recovers_after_host_side_timeout(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []

    def fake_rph(host: str, code: str, timeout: int = 120) -> Result:  # type: ignore[no-untyped-def]
        calls.append(timeout)
        if len(calls) == 1:
            return Result(1, "", "TIMEOUT")  # host-side cap hit (load spike)
        return Result(0, "PROVISIONED OK (existing)", "")  # retry: DUT fine

    monkeypatch.setattr(vl, "run_python_on_host", fake_rph)
    monkeypatch.setattr(vl.time, "sleep", _no_sleep)
    result = vl.run_python_on_host_with_retry("ai-legion", "pass", timeout=vl.SERIAL_PROVISION_TIMEOUT_S)

    assert result.returncode == 0
    assert "PROVISIONED OK" in result.stdout
    assert calls == [vl.SERIAL_PROVISION_TIMEOUT_S, vl.SERIAL_PROVISION_TIMEOUT_S]


def test_genuine_failure_fails_fast_without_retry(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A DUT that never reaches its boot prompt is dead — no second
    multi-minute attempt; the real stderr must surface unchanged."""
    calls: list[int] = []

    def fake_rph(host: str, code: str, timeout: int = 120) -> Result:  # type: ignore[no-untyped-def]
        calls.append(timeout)
        return Result(1, "", "TIMEOUT: VM did not reach boot prompt within 60s")

    monkeypatch.setattr(vl, "run_python_on_host", fake_rph)
    monkeypatch.setattr(vl.time, "sleep", _no_sleep)
    result = vl.run_python_on_host_with_retry("ai-legion", "pass", timeout=300)

    assert result.returncode == 1
    assert "did not reach boot prompt" in result.stderr
    assert calls == [300]  # exactly one attempt


def test_retry_gives_up_after_bounded_attempts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[int] = []

    def fake_rph(host: str, code: str, timeout: int = 120) -> Result:  # type: ignore[no-untyped-def]
        calls.append(timeout)
        return Result(1, "", "TIMEOUT")

    monkeypatch.setattr(vl, "run_python_on_host", fake_rph)
    monkeypatch.setattr(vl.time, "sleep", _no_sleep)
    result = vl.run_python_on_host_with_retry("ai-legion", "pass", timeout=300)

    assert result.returncode == 1
    assert result.stderr == "TIMEOUT"
    assert len(calls) == vl.SERIAL_PROVISION_ATTEMPTS  # bounded, then fail


def test_retry_backoff_is_applied_between_attempts(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    state = {"n": 0}

    def fake_rph(host: str, code: str, timeout: int = 120) -> Result:  # type: ignore[no-untyped-def]
        state["n"] += 1
        return Result(0, "ok", "") if state["n"] > 1 else Result(1, "", "TIMEOUT")

    monkeypatch.setattr(vl, "run_python_on_host", fake_rph)
    monkeypatch.setattr(vl.time, "sleep", lambda s: sleeps.append(s))
    vl.run_python_on_host_with_retry("ai-legion", "pass", timeout=300)

    assert sleeps == [vl.SERIAL_PROVISION_RETRY_BACKOFF_S]


# -------------------------------------------------------------- start_poc wiring

def test_start_poc_step2_uses_raised_cap_and_survives_one_timeout(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """Full offline start_poc walkthrough: one host-side timeout in step 2
    must be retried (not crash), with the raised cap actually passed down."""
    python_calls: list[dict] = []

    def fake_run_remote(host: str, script: str, timeout: int = 60) -> Result:  # type: ignore[no-untyped-def]
        return Result(0, "ok", "")

    def fake_rph(host: str, code: str, timeout: int = 120) -> Result:  # type: ignore[no-untyped-def]
        python_calls.append({"timeout": timeout})
        if len(python_calls) == 1:
            return Result(1, "", "TIMEOUT")  # the #1005 flake, first attempt
        return Result(0, "PROVISIONED OK (existing)", "")

    monkeypatch.setattr(vl, "run_remote", fake_run_remote)
    monkeypatch.setattr(vl, "run_python_on_host", fake_rph)
    monkeypatch.setattr(vl.time, "sleep", _no_sleep)
    args = vl.build_parser().parse_args(["start-poc", "--workdir", str(tmp_path / "lab")])

    rc = vl.start_poc(args)

    assert rc == 0
    assert [c["timeout"] for c in python_calls] == [
        vl.SERIAL_PROVISION_TIMEOUT_S,
        vl.SERIAL_PROVISION_TIMEOUT_S,
    ]
    assert vl.SERIAL_PROVISION_TIMEOUT_S > 120  # the old flake bound is gone
