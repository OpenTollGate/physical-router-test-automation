"""The router REMOVES itself mid-install: the wired LAN port moves OFF br-lan
onto br-private, so the LAN IP (192.168.1.1) stops answering. A run that does
not notice spins on a dead address — measured 2026-10-13, the rc4 live verify
hung for minutes while every later ``ssh()`` retried the vanished LAN IP.

These tests pin the fail-fast guard in ``Router.ssh``: a RUN of connect-level
failures raises ``RouterUnreachable``; a single miss, or a run broken by a
success, does not; and the documented escape hatch restores the old behaviour.
"""

import subprocess

import pytest

from lib.router import _MAX_CONSECUTIVE_UNREACHABLE, Router, RouterUnreachable

pytestmark = [pytest.mark.story("P11")]


class _Completed:
    def __init__(self, returncode, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def _router():
    return Router("192.168.1.1", "192.168.1.100", "aa:bb:cc:dd:ee:ff", "tg.lan")


def _patch(monkeypatch, results):
    it = iter(results)

    def fake_run(*args, **kwargs):
        return next(it)

    monkeypatch.setattr(subprocess, "run", fake_run)


_TIMEOUT = _Completed(255, "ssh: connect to host 192.168.1.1 port 22: Connection timed out")
_OK = _Completed(0, "", "ok")


def test_a_run_of_unreachable_failures_fails_fast(monkeypatch):
    _patch(monkeypatch, [_TIMEOUT] * _MAX_CONSECUTIVE_UNREACHABLE)
    r = _router()
    with pytest.raises(RouterUnreachable):
        for _ in range(_MAX_CONSECUTIVE_UNREACHABLE):
            r.ssh("true", timeout=1)


def test_a_single_miss_does_not_raise(monkeypatch):
    _patch(monkeypatch, [_TIMEOUT])
    assert _router().ssh("true", timeout=1) == ""


def test_a_success_resets_the_counter(monkeypatch):
    # (N-1) misses, a success, then (N-1) more misses — never N in a ROW.
    seq = (
        [_TIMEOUT] * (_MAX_CONSECUTIVE_UNREACHABLE - 1)
        + [_OK]
        + [_TIMEOUT] * (_MAX_CONSECUTIVE_UNREACHABLE - 1)
    )
    _patch(monkeypatch, seq)
    r = _router()
    for _ in range(len(seq)):
        r.ssh("true", timeout=1)  # must not raise


def test_the_escape_hatch_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("TOLLGATE_ALLOW_ROUTER_MOVE", "1")
    _patch(monkeypatch, [_TIMEOUT] * (_MAX_CONSECUTIVE_UNREACHABLE + 3))
    r = _router()
    for _ in range(_MAX_CONSECUTIVE_UNREACHABLE + 3):
        r.ssh("true", timeout=1)  # must not raise
