"""Unit tests for :mod:`lib.bench_lock`.

Includes the negative control the bench-lock card asks for: while one holder
holds the lock, a *second process* must refuse with the holder's identity in the
error message.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import bench_lock as _bench_lock
from lib.bench_lock import BenchBusy, BenchLock, holder_line, main, parse_holder, read_holder

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def lock_path(tmp_path):
    return str(tmp_path / "bench-mt3000.lock")


def test_parse_holder_reads_key_value_tokens():
    holder = parse_holder(
        "profile=manager pid=2584919 task=t_a05094ad purpose=curl|bash-pre16-validation "
        "since=2026-09-25T00:19:40+02:00"
    )
    assert holder.profile == "manager"
    assert holder.pid == "2584919"
    assert holder.task == "t_a05094ad"
    assert holder.purpose == "curl|bash-pre16-validation"
    assert holder.since.startswith("2026-09-25")
    assert not holder.is_empty


def test_parse_holder_of_empty_file_is_empty():
    assert parse_holder("").is_empty


def test_holder_line_names_profile_pid_purpose_and_task():
    line = holder_line("prta-install-path-e2e", task_id="t_a05094ad", profile="manager")
    assert "profile=manager" in line
    assert f"pid={os.getpid()}" in line
    assert "task=t_a05094ad" in line
    assert "purpose=prta-install-path-e2e" in line
    assert "since=" in line


def test_acquire_hold_release_roundtrip(lock_path):
    lock = BenchLock(purpose="unit test", task_id="t_unit", path=lock_path)
    free, _ = lock.status()
    assert free

    with lock:
        assert lock.held
        assert "purpose=unit" in read_holder(lock_path).raw
        assert "task=t_unit" in read_holder(lock_path).raw
        assert lock.status()[0] is False

    assert not lock.held
    assert lock.status()[0] is True
    assert "released" in read_holder(lock_path).raw


def test_second_holder_is_refused_and_message_names_the_holder(lock_path):
    first = BenchLock(purpose="first-window", task_id="t_first", path=lock_path).acquire()
    try:
        second = BenchLock(purpose="second-window", task_id="t_second", path=lock_path)
        with pytest.raises(BenchBusy) as excinfo:
            second.acquire()
        message = str(excinfo.value)
        assert "BENCH BUSY" in message
        assert "task=t_first" in message  # the holder identity is in the error
        assert "t_first" in message
        assert not second.held
    finally:
        first.release()


def test_release_is_idempotent_and_safe_when_never_held(lock_path):
    lock = BenchLock(purpose="x", path=lock_path)
    lock.release()  # never held
    lock.acquire()
    lock.release()
    lock.release()


def test_real_second_process_is_refused(lock_path):
    """The negative control: another *process* cannot take the bench."""
    script = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from lib.bench_lock import BenchLock, BenchBusy\n"
        "try:\n"
        f"    BenchLock(purpose='other-process', task_id='t_other', path={lock_path!r}).acquire()\n"
        "except BenchBusy as exc:\n"
        "    print('REFUSED:', exc)\n"
        "    sys.exit(3)\n"
        "print('ACQUIRED'); sys.exit(0)\n"
    )
    with BenchLock(purpose="holder-window", task_id="t_holder", path=lock_path):
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
        )
    assert result.returncode == 3, result.stdout + result.stderr
    assert "REFUSED" in result.stdout
    assert "t_holder" in result.stdout

    # once released, the same process can take it
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ACQUIRED" in result.stdout


def test_lock_survives_a_killed_holder_because_flock_is_released_by_the_kernel(lock_path):
    """A dead worker must never wedge the bench (the incident's root cause)."""
    script = (
        f"import sys, time; sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from lib.bench_lock import BenchLock\n"
        f"lock = BenchLock(purpose='crashed-holder', task_id='t_crashed', path={lock_path!r})\n"
        "lock.acquire()\n"
        "print('HELD', flush=True)\n"
        "time.sleep(300)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "HELD"
        with pytest.raises(BenchBusy):
            BenchLock(purpose="probe", path=lock_path).acquire()
    finally:
        proc.kill()
        proc.wait(timeout=30)

    # the kernel dropped the flock with the process
    assert BenchLock(purpose="after-crash", path=lock_path).status()[0] is True


def test_env_override_for_the_lock_path(monkeypatch, tmp_path):
    custom = tmp_path / "custom.lock"
    monkeypatch.setenv("TOLLGATE_BENCH_LOCK", str(custom))
    lock = BenchLock(purpose="env")
    assert lock.path == str(custom)
    with lock:
        assert custom.exists()


def test_one_liner_acquire_keeps_the_lock_alive(lock_path):
    """`BenchLock(...).acquire()` must not be garbage-collected into a release."""
    BenchLock(purpose="one-liner", task_id="t_one_liner", path=lock_path).acquire()
    try:
        assert BenchLock(purpose="probe", path=lock_path).status()[0] is False
    finally:
        for live in list(_bench_lock._LIVE_LOCKS):
            if live.path == lock_path:
                live.release()
    assert BenchLock(purpose="probe", path=lock_path).status()[0] is True


def test_cli_status_check_and_acquire(lock_path, capsys):
    assert main(["status", "--path", lock_path]) == 0
    out = capsys.readouterr().out
    assert "free   : True" in out
    assert "holder : <none>" in out

    holder = BenchLock(purpose="cli-held", task_id="t_cli", path=lock_path).acquire()
    try:
        assert main(["check", "--path", lock_path]) == 1
        out = capsys.readouterr().out
        assert "free   : False" in out
        assert "t_cli" in out
    finally:
        holder.release()

    assert main(["check", "--path", lock_path]) == 0
    assert main(["acquire", "--path", lock_path, "--purpose", "cli", "--task", "t_cli2"]) == 0
