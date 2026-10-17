"""Unit tests for scripts/hw-bench-lease — the ONE machine-global bench lease.

PRTA-REVIVE: the lease is lib.bench_lock.BenchLock (flock authority + holder
line, interops with scripts/mt3000-bench/bench-lock.sh) surfaced as a CLI that
the Makefile, the hw-smoke lane and the queue poller all share. The idle-gate
is lib.session_verify.check_balance_api wired in via an HTTP-only router shim.

All tests run offline against a temporary lock path (TOLLGATE_BENCH_LOCK) and
a loopback HTTP server standing in for the router's :2121 API.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LEASE = REPO_ROOT / "scripts" / "hw-bench-lease"

BUSY_RC = 2
STALE_RC = 3
IDLE_REFUSED_RC = 4
UNREACHABLE_RC = 5


class _BalanceHandler(BaseHTTPRequestHandler):
    """Serves /balance with a fixed JSON body; records the request path."""

    server_version = "fake-tollgate/1"

    def do_GET(self):  # noqa: N802 - http.server API
        body = json.dumps(self.server.balance_body).encode()
        self.send_response(self.server.balance_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - http.server API
        pass


class _FakeBalanceServer(HTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.balance_body: dict = {"session_active": False}
        self.balance_status: int = 200


@pytest.fixture()
def fake_balance_api():
    """Loopback HTTP server that answers GET /balance with a controllable body."""
    server = _FakeBalanceServer(("127.0.0.1", 0), _BalanceHandler)
    server.balance_body = {"session_active": False}
    server.balance_status = 200
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture()
def lease_env(tmp_path):
    """Environment with the lock pointed at a temp file (isolated per test)."""
    env = {**os.environ, "TOLLGATE_BENCH_LOCK": str(tmp_path / "bench-test.lock")}
    yield env


def run_lease(args, env, timeout=30):
    return subprocess.run(
        [sys.executable, str(LEASE), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _load_lease_module():
    """Import scripts/hw-bench-lease as a module (it has no .py suffix)."""
    import importlib.machinery
    import importlib.util

    loader = importlib.machinery.SourceFileLoader("hw_bench_lease", str(LEASE))
    spec = importlib.util.spec_from_loader("hw_bench_lease", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hw_bench_lease"] = module
    spec.loader.exec_module(module)
    return module


def lock_path(env):
    return Path(env["TOLLGATE_BENCH_LOCK"])


# --------------------------------------------------------------------------- #
# status / require — the state machine the Makefile guard consumes
# --------------------------------------------------------------------------- #


class TestStatusRequire:
    def test_status_free_by_default(self, lease_env):
        result = run_lease(["status"], lease_env)
        assert result.returncode == 0
        assert "free" in result.stdout
        assert "stale" not in result.stdout

    def test_status_json_reports_state_and_holder(self, lease_env):
        result = run_lease(["status", "--json"], lease_env)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert payload["state"] == "free"
        assert "path" in payload

    def test_require_refuses_when_free(self, lease_env):
        result = run_lease(["require"], lease_env)
        assert result.returncode == 1
        assert "make lock" in result.stdout

    def test_require_accepts_while_held_and_names_holder(self, lease_env):
        holder = subprocess.Popen(
            [sys.executable, str(LEASE), "hold", "--purpose", "unit-test"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=lease_env,
            cwd=str(REPO_ROOT),
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if lock_path(lease_env).exists():
                    line = lock_path(lease_env).read_text()
                    if "purpose=unit-test" in line:
                        break
                time.sleep(0.1)
            result = run_lease(["require"], lease_env)
            assert result.returncode == 0
            assert "purpose=unit-test" in result.stdout
        finally:
            holder.send_signal(signal.SIGINT)
            holder.wait(timeout=10)

    def test_require_distinct_rc_for_stale(self, lease_env):
        lock_path(lease_env).write_text(
            "worker-x pid=999999 purpose=dead-run since=2026-09-01T00:00:00+00:00 "
            "task=- host=bench\n"
        )
        result = run_lease(["require"], lease_env)
        assert result.returncode == STALE_RC
        assert "worker-x" in result.stdout


# --------------------------------------------------------------------------- #
# exec — the lane entry point
# --------------------------------------------------------------------------- #


class TestExec:
    def test_exec_runs_command_and_releases(self, lease_env):
        result = run_lease(
            ["exec", "--purpose", "pytest", "--", "echo", "hello-bench"], lease_env
        )
        assert result.returncode == 0, result.stderr
        assert "hello-bench" in result.stdout
        # released: holder line cleared, state back to free
        assert run_lease(["status"], lease_env).stdout.count("free")

    def test_exec_writes_holder_line_while_running(self, lease_env):
        script = (
            "import pathlib,sys;"
            f"print(pathlib.Path({str(lock_path(lease_env))!r}).read_text(), end='')"
        )
        result = run_lease(
            [
                "exec",
                "--purpose",
                "holder-proof",
                "--task",
                "t_unit",
                "--",
                sys.executable,
                "-c",
                script,
            ],
            lease_env,
        )
        assert result.returncode == 0, result.stderr
        assert "purpose=holder-proof" in result.stdout
        assert "task=t_unit" in result.stdout

    def test_exec_child_gets_bench_lock_env(self, lease_env):
        script = (
            "import os;print('HELD=' + os.environ.get('BENCH_LOCK_HELD', 'unset'));"
            "print('PID=' + os.environ.get('BENCH_LOCK_HOLDER_PID', 'unset'))"
        )
        result = run_lease(
            ["exec", "--purpose", "env-proof", "--", sys.executable, "-c", script],
            lease_env,
        )
        assert result.returncode == 0, result.stderr
        assert "HELD=1" in result.stdout
        assert "PID=" in result.stdout and "PID=unset" not in result.stdout

    def test_exec_propagates_child_exit_code(self, lease_env):
        result = run_lease(
            ["exec", "--purpose", "rc-proof", "--", "sh", "-c", "exit 42"], lease_env
        )
        assert result.returncode == 42
        # even after a failure, the lease is released
        assert "free" in run_lease(["status"], lease_env).stdout

    def test_second_agent_is_blocked_while_first_holds(self, lease_env):
        """THE concurrency proof: agent B must fail fast while agent A holds."""
        first = subprocess.Popen(
            [
                sys.executable,
                str(LEASE),
                "exec",
                "--purpose",
                "agent-A",
                "--",
                "sleep",
                "3",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=lease_env,
            cwd=str(REPO_ROOT),
        )
        try:
            # wait until A's holder line is visible
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if lock_path(lease_env).exists() and "agent-A" in lock_path(
                    lease_env
                ).read_text():
                    break
                time.sleep(0.05)

            second = run_lease(
                ["exec", "--purpose", "agent-B", "--", "echo", "should-not-run"],
                lease_env,
            )
            assert second.returncode == BUSY_RC
            assert "BENCH BUSY" in second.stderr
            assert "agent-A" in second.stderr  # names the holder
            assert "should-not-run" not in second.stdout
        finally:
            first.wait(timeout=15)

    def test_exec_missing_purpose_is_usage_error(self, lease_env):
        result = run_lease(["exec", "--", "echo", "x"], lease_env)
        assert result.returncode != 0
        assert "purpose" in result.stderr


# --------------------------------------------------------------------------- #
# stale recovery
# --------------------------------------------------------------------------- #


class TestStale:
    def _seed_stale(self, env):
        lock_path(env).write_text(
            "ghost pid=4128000 purpose=crashed since=2026-06-01T00:00:00+00:00 "
            "task=- host=bench\n"
        )

    def test_exec_refuses_stale_without_reclaim(self, lease_env):
        self._seed_stale(lease_env)
        result = run_lease(
            ["exec", "--purpose", "next-run", "--", "echo", "ran"], lease_env
        )
        assert result.returncode == STALE_RC
        assert "BENCH STALE" in result.stderr
        assert "ghost" in result.stderr

    def test_exec_reclaims_stale_with_flag(self, lease_env):
        self._seed_stale(lease_env)
        result = run_lease(
            ["exec", "--purpose", "next-run", "--reclaim-stale", "--", "echo", "ran"],
            lease_env,
        )
        assert result.returncode == 0, result.stderr
        assert "ran" in result.stdout

    def test_release_clears_stale_line(self, lease_env):
        self._seed_stale(lease_env)
        result = run_lease(["release"], lease_env)
        assert result.returncode == 0
        assert lock_path(lease_env).read_text().strip() == ""


# --------------------------------------------------------------------------- #
# idle gate — session_verify.check_balance_api wired into the lease
# --------------------------------------------------------------------------- #


class TestIdleGate:
    def _exec_against(self, env, server, body=None, status=None):
        if body is not None:
            server.balance_body = body
        if status is not None:
            server.balance_status = status
        return run_lease(
            [
                "exec",
                "--purpose",
                "idle-gate-test",
                "--check-idle",
                "--host",
                "127.0.0.1",
                "--api-port",
                str(server.server_address[1]),
                "--",
                "echo",
                "bench-was-idle",
            ],
            env,
        )

    def test_refuses_when_session_active(self, lease_env, fake_balance_api):
        result = self._exec_against(
            lease_env, fake_balance_api, body={"session_active": True}
        )
        assert result.returncode == IDLE_REFUSED_RC
        assert "session" in result.stderr.lower()
        assert "bench-was-idle" not in result.stdout
        # refused => lease released again
        assert "free" in run_lease(["status"], lease_env).stdout

    def test_allotment_remaining_counts_as_active(self, lease_env, fake_balance_api):
        result = self._exec_against(
            lease_env,
            fake_balance_api,
            body={"session_active": False, "remaining": 5000},
        )
        assert result.returncode == IDLE_REFUSED_RC

    def test_proceeds_when_idle(self, lease_env, fake_balance_api):
        result = self._exec_against(
            lease_env, fake_balance_api, body={"session_active": False}
        )
        assert result.returncode == 0, result.stderr
        assert "bench-was-idle" in result.stdout

    def test_fails_closed_when_api_unreachable(self, lease_env):
        # find a definitely-closed port on loopback
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
        sock.close()
        result = run_lease(
            [
                "exec",
                "--purpose",
                "idle-gate-closed",
                "--check-idle",
                "--host",
                "127.0.0.1",
                "--api-port",
                str(closed_port),
                "--",
                "echo",
                "bench-was-idle",
            ],
            lease_env,
            timeout=30,
        )
        assert result.returncode == UNREACHABLE_RC
        assert "bench-was-idle" not in result.stdout

    def test_fails_closed_when_balance_not_parseable(self, lease_env, fake_balance_api):
        result = self._exec_against(lease_env, fake_balance_api, status=502)
        assert result.returncode == UNREACHABLE_RC

    def test_fails_closed_when_the_probe_raises(self, monkeypatch):
        """A raising probe must REFUSE, not abort with a traceback.

        check_balance_api is documented to degrade to a sentinel rather than
        raise; this pins the lease's own belt-and-braces path so a future
        change there cannot turn the gate into a fault-OPEN one. Exercised
        in-process: probe_idle imports check_balance_api at call time, so the
        monkeypatch reaches it (a subprocess would not see it and the test
        would pass for the wrong reason).
        """
        mod = _load_lease_module()
        import lib.session_verify as sv

        def boom(*_a, **_k):
            raise RuntimeError("probe exploded")

        monkeypatch.setattr(sv, "check_balance_api", boom)
        refused, reason, evidence = mod.probe_idle("127.0.0.1", 1, 1.0)
        assert refused is True
        assert "not probeable" in reason
        assert "probe exploded" in evidence


# --------------------------------------------------------------------------- #
# retired lock files — the 93-day-stale class
# --------------------------------------------------------------------------- #


def _seed_legacy(
    root: Path, name: str = "routers.lock", *, age_days: float = 93.0, subdir: str = ""
) -> Path:
    """Write a leftover lock file with a controlled mtime."""
    path = (root / subdir / name) if subdir else (root / name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "locked: true\nsession: dead-agent@bench\ntimestamp: 2026-01-01T00:00:00Z\n"
    )
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


class TestLegacyLocks:
    def test_status_reports_a_stale_retired_lock_file(self, lease_env, tmp_path):
        leftover = _seed_legacy(tmp_path, age_days=93)
        result = run_lease(["status", "--legacy-root", str(tmp_path)], lease_env)
        assert result.returncode == 0
        assert str(leftover) in result.stdout
        assert "[STALE]" in result.stdout
        assert "NOT authoritative" in result.stdout
        # the retired file must not change the lease verdict
        assert "bench lease : free" in result.stdout

    def test_status_json_lists_legacy_locks_with_age(self, lease_env, tmp_path):
        _seed_legacy(tmp_path, age_days=93)
        result = run_lease(
            ["status", "--json", "--legacy-root", str(tmp_path)], lease_env
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert len(payload["legacy_locks"]) == 1
        entry = payload["legacy_locks"][0]
        assert entry["stale"] is True
        assert entry["age_days"] >= 92

    def test_fresh_legacy_lock_is_not_flagged_stale(self, lease_env, tmp_path):
        _seed_legacy(tmp_path, age_days=0.1)
        result = run_lease(["status", "--legacy-root", str(tmp_path)], lease_env)
        assert "[fresh]" in result.stdout
        assert "[STALE]" not in result.stdout

    def test_legacy_scan_finds_all_three_retired_names(self, lease_env, tmp_path):
        for name in ("routers.lock", "hardware.lock", "tollgate_hardware.lock"):
            _seed_legacy(tmp_path, name=name)
        result = run_lease(
            ["status", "--json", "--legacy-root", str(tmp_path)], lease_env
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        names = {Path(item["path"]).name for item in payload["legacy_locks"]}
        assert names == {"routers.lock", "hardware.lock", "tollgate_hardware.lock"}

    def test_legacy_scan_walks_subdirectories(self, lease_env, tmp_path):
        leftover = _seed_legacy(tmp_path, subdir="mint-health")
        result = run_lease(
            ["status", "--json", "--legacy-root", str(tmp_path)], lease_env
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        assert [item["path"] for item in payload["legacy_locks"]] == [str(leftover)]

    def test_prune_legacy_removes_stale_files_and_leaves_fresh_ones(
        self, lease_env, tmp_path
    ):
        stale = _seed_legacy(tmp_path, age_days=93)
        fresh = _seed_legacy(tmp_path, name="hardware.lock", age_days=0.1)
        result = run_lease(["prune-legacy", "--legacy-root", str(tmp_path)], lease_env)
        assert result.returncode == 0, result.stderr
        assert not stale.exists()
        assert fresh.exists()  # below the stale threshold; --all is required
        assert "removed" in result.stdout

    def test_prune_legacy_all_removes_everything(self, lease_env, tmp_path):
        stale = _seed_legacy(tmp_path, age_days=93)
        fresh = _seed_legacy(tmp_path, name="hardware.lock", age_days=0.1)
        result = run_lease(
            ["prune-legacy", "--all", "--legacy-root", str(tmp_path)], lease_env
        )
        assert result.returncode == 0, result.stderr
        assert not stale.exists() and not fresh.exists()

    def test_prune_legacy_dry_run_touches_nothing(self, lease_env, tmp_path):
        stale = _seed_legacy(tmp_path, age_days=93)
        result = run_lease(
            ["prune-legacy", "--dry-run", "--legacy-root", str(tmp_path)], lease_env
        )
        assert stale.exists()
        assert "would remove" in result.stdout

    def test_prune_legacy_never_removes_the_lease(self, lease_env, tmp_path):
        """The authoritative lease must survive a prune, held or not."""
        holder = subprocess.Popen(
            [sys.executable, str(LEASE), "hold", "--purpose", "prune-safety"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=lease_env,
            cwd=str(REPO_ROOT),
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if lock_path(lease_env).exists() and "prune-safety" in lock_path(
                    lease_env
                ).read_text():
                    break
                time.sleep(0.05)
            _seed_legacy(tmp_path, age_days=93)
            result = run_lease(
                ["prune-legacy", "--all", "--legacy-root", str(tmp_path)], lease_env
            )
            assert result.returncode == 0, result.stderr
            # the lease is still held and still names its holder
            held = run_lease(["require"], lease_env)
            assert held.returncode == 0
            assert "prune-safety" in held.stdout
        finally:
            holder.send_signal(signal.SIGINT)
            holder.wait(timeout=10)

    def test_exec_warns_about_a_retired_lock_but_still_runs(self, lease_env, tmp_path):
        """A 93-day routers.lock must not block a lease-holding run."""
        leftover = _seed_legacy(tmp_path, age_days=93)
        result = run_lease(
            [
                "exec",
                "--purpose",
                "not-blocked-by-legacy",
                "--legacy-root",
                str(tmp_path),
                "--",
                "echo",
                "ran-anyway",
            ],
            lease_env,
        )
        assert result.returncode == 0, result.stderr
        assert "ran-anyway" in result.stdout
        assert "retired lock file" in result.stderr
        assert str(leftover) in result.stderr

    def test_exec_no_legacy_scan_is_quiet(self, lease_env, tmp_path):
        _seed_legacy(tmp_path, age_days=93)
        result = run_lease(
            [
                "exec",
                "--purpose",
                "quiet",
                "--no-legacy-scan",
                "--legacy-root",
                str(tmp_path),
                "--",
                "echo",
                "ran",
            ],
            lease_env,
        )
        assert result.returncode == 0, result.stderr
        assert "retired lock file" not in result.stderr
