"""Supervised execution: ``tee_run``'s contract plus a *live* lease.

Why this module exists
----------------------
``scripts/run-profile.py::tee_run`` blocks on the child's stdout and then waits.
While it waits, nothing renews the authority lease, nothing looks at the fence
and nothing notices that the lane was quarantined, superseded or lost. A child
that outlives its TTL keeps mutating a DUT that the store has already taken
away — the audit's exact finding: *"CHILD FINISHED AFTER TTL / RENEW CALLS 0"*.

This module keeps everything ``tee_run`` promises — the same five arguments, the
same stdout streaming, the same ``output.log``, the same integer exit status —
and adds the supervision the runner was missing:

* **(a) Heartbeat.** Every ``heartbeat_s`` the lease is renewed through
  ``FleetAuthority.renew``, so a long run keeps the reservation it is using.
* **(b) Renewal failure is fatal.** An authority that cannot be renewed (disk
  error, store gone, lock timeout) is *not* evidence that we still hold the
  lane. The process group is terminated and ``RenewalFailed`` is raised. A run
  is never left executing while unaccounted.
* **(c) Fencing.** ``renew`` refuses a superseded generation, and the
  allocation is re-read every beat; a lost claim terminates the group and
  raises ``AuthorityLost``.
* **(d) Honest cleanup.** Teardown is *probed*, not asserted. The group must be
  gone (``killpg(pgid, 0)`` says so) before the caller is told
  ``Cleanup.CONFIRMED``; otherwise it is ``Cleanup.UNCONFIRMED`` and the caller
  must record ``CLEANUP_PENDING`` instead of claiming a clean release.

What this module deliberately does NOT do
-----------------------------------------
It does **not** acquire, release or validate a labgrid place, and it never
reserves or activates anything. Place ownership and liveness belong to the
pytest venue fixture in ``tests/conftest.py`` (``plan_venue`` + ``venue_mutex``,
``pytest.exit(rc=4)`` when the place is taken). A runner-side acquisition here
would be a second owner for one DUT — the double-ownership bug this milestone
removes — so supervision covers **host budget only**. A test asserts this
module contains no acquisition call at all.

Likewise the fail-closed gate in ``run-profile.py`` is untouched: this is
machinery plus its tests, not a licence to enable fleet execution.
"""

from __future__ import annotations

import codecs
import contextlib
import os
import selectors
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from lib.fleet_authority import AuthorityError, State

__all__ = [
    "EXIT_RENEWAL_FAILED",
    "EXIT_SUPERVISION_LOST",
    "LIVE_STATES",
    "Cleanup",
    "Outcome",
    "SupervisionError",
    "RenewalFailed",
    "AuthorityLost",
    "MissingAuthority",
    "run_supervised",
    "supervised_tee_run",
    "assert_authority_valid",
]

#: The renewal authority could not be reached or could not extend the lease.
#: Distinct from ``ADMISSION_EXIT`` (75, refused before spawn) and from any
#: pytest status, so a caller can tell "we lost the accounting" from "tests
#: failed" without parsing output.
EXIT_RENEWAL_FAILED = 76

#: The claim is no longer ours (fenced, released, expired, gone). Same
#: distinction as above, different cause: the store *answered* and said no.
EXIT_SUPERVISION_LOST = 77

#: States in which work may still run under this claim.
LIVE_STATES = frozenset({State.RESERVED, State.ACTIVE})


class Cleanup(StrEnum):
    """Whether teardown was *proved*, for the caller's release record."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True)
class Outcome:
    """What supervision actually did — evidence, not a verdict."""

    exit_code: int
    renewals: int = 0
    fence_checks: int = 0
    cleanup: Cleanup = Cleanup.CONFIRMED
    spawned: bool = False
    terminated: bool = False
    duration_s: float = 0.0
    cleanup_detail: str = ""

    @property
    def cleanup_confirmed(self) -> bool:
        return self.cleanup is Cleanup.CONFIRMED

    @property
    def clean(self) -> bool:
        """Only a confirmed teardown may be reported as clean."""
        return self.cleanup is Cleanup.CONFIRMED


class SupervisionError(RuntimeError):
    """Supervision could not continue. Always carries the teardown evidence."""

    reason = "supervision_error"
    exit_code = EXIT_SUPERVISION_LOST

    def __init__(self, detail: str = "", *, outcome: Outcome | None = None) -> None:
        self.detail = detail
        self.outcome = outcome
        super().__init__(f"{self.reason}: {detail}" if detail else self.reason)

    @property
    def cleanup(self) -> Cleanup:
        return self.outcome.cleanup if self.outcome is not None else Cleanup.UNCONFIRMED

    @property
    def cleanup_confirmed(self) -> bool:
        return self.cleanup is Cleanup.CONFIRMED


class RenewalFailed(SupervisionError):
    """The lease could not be renewed — the run is no longer accounted for."""

    reason = "renewal_failed"
    exit_code = EXIT_RENEWAL_FAILED


class AuthorityLost(SupervisionError):
    """The claim is not ours any more: fenced, released, expired or unknown."""

    reason = "authority_lost"
    exit_code = EXIT_SUPERVISION_LOST


class MissingAuthority(SupervisionError):
    """No authority was supplied, so nothing could be renewed or checked."""

    reason = "authority_missing"
    exit_code = EXIT_RENEWAL_FAILED


# --------------------------------------------------------------------------- #
# fence / liveness check
# --------------------------------------------------------------------------- #
def assert_authority_valid(
    authority: Any,
    run_id: str,
    fence: int,
    *,
    now: Callable[[], float] = time.time,
) -> Any:
    """Refuse to run under a claim the store no longer recognises.

    Read-only: this never renews, reserves or transitions anything, so it is
    safe to call before a child exists. It catches the three ways a claim dies
    silently — a superseded generation, a closed allocation and an expired
    lease — and reports each as ``AuthorityLost`` instead of running on.
    """
    try:
        allocation = authority.allocation(run_id)
    except AuthorityError as exc:
        raise AuthorityLost(f"no live claim for run {run_id!r}: {exc}") from None
    except Exception as exc:  # noqa: BLE001 - an unreadable store is not a claim
        raise RenewalFailed(f"authority unreadable while checking run {run_id!r}: {exc}") from None

    if int(allocation.fence) != int(fence):
        raise AuthorityLost(
            f"allocation {run_id!r} has been fenced (caller holds fence {fence}, "
            f"store holds {allocation.fence}) — this run may not continue"
        )
    state = allocation.state
    if state not in LIVE_STATES:
        raise AuthorityLost(f"allocation {run_id!r} is {state.value!r} — not live; supervision lost")
    remaining = float(allocation.expires_at) - float(now())
    if remaining <= 0:
        raise AuthorityLost(f"allocation {run_id!r} lease expired {abs(remaining):.3f}s ago and was not renewed")
    return allocation


# --------------------------------------------------------------------------- #
# teardown
# --------------------------------------------------------------------------- #
def _group_alive(pgid: int) -> bool:
    """Process-group liveness probe: signal 0 asks the kernel, it sends nothing."""
    if pgid <= 1:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Somebody else owns the group — it exists, which is all we asked.
        return True
    return True


def _stop_group(
    process: subprocess.Popen,
    pgid: int,
    *,
    grace_s: float,
    kill_group: Callable[[int, int], None],
    group_alive: Callable[[int], bool],
) -> tuple[bool, str]:
    """Terminate the whole group and *prove* it is gone.

    SIGTERM first so a well-behaved child can flush; SIGKILL after the grace
    window. The child is reaped before the final probe, because an unreaped
    zombie still answers ``killpg(pgid, 0)`` and would make a dead group look
    alive — the mirror-image dishonest answer to claiming clean without proof.
    """
    notes: list[str] = []
    if process.poll() is None or group_alive(pgid):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            kill_group(pgid, signal.SIGTERM)
        notes.append(f"SIGTERM to group {pgid}")

    deadline = time.monotonic() + max(0.0, float(grace_s))
    while time.monotonic() < deadline:
        if process.poll() is not None and not group_alive(pgid):
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=1.0)
            return True, "; ".join(notes + ["group gone after SIGTERM"])
        time.sleep(0.01)

    if group_alive(pgid):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            kill_group(pgid, signal.SIGKILL)
        notes.append("SIGKILL to group")

    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=max(1.0, float(grace_s)))
    if process.poll() is None:
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2.0)

    if group_alive(pgid):
        notes.append(f"group {pgid} STILL ALIVE — teardown unconfirmed")
        return False, "; ".join(notes)
    notes.append("group gone")
    return True, "; ".join(notes)


# --------------------------------------------------------------------------- #
# supervision
# --------------------------------------------------------------------------- #
class _Supervisor:
    """One child, one lease, one honest answer about teardown."""

    def __init__(
        self,
        authority: Any,
        run_id: str,
        fence: int,
        *,
        ttl_s: float,
        heartbeat_s: float,
        terminate_grace_s: float,
        kill_group: Callable[[int, int], None],
        group_alive: Callable[[int], bool],
    ) -> None:
        self.authority = authority
        self.run_id = run_id
        self.fence = fence
        self.ttl_s = float(ttl_s)
        self.heartbeat_s = float(heartbeat_s)
        self.terminate_grace_s = float(terminate_grace_s)
        self.kill_group = kill_group
        self.group_alive = group_alive
        self.renewals = 0
        self.fence_checks = 0

    # -- heartbeat ---------------------------------------------------------
    def beat(self) -> None:
        """Renew once. Any failure that is not a clean renewal is fatal."""
        self.fence_checks += 1
        try:
            allocation = self.authority.renew(self.run_id, self.fence, ttl_s=self.ttl_s)
        except AuthorityError as exc:
            # The store answered and refused: the claim is not ours. Fencing,
            # release, expiry and unknown-run all arrive here.
            raise AuthorityLost(f"authority refused renewal for {self.run_id!r}: {exc}") from None
        except Exception as exc:  # noqa: BLE001 - disk/store failure is fatal, not ignorable
            raise RenewalFailed(
                f"could not renew run {self.run_id!r} (fence {self.fence}): {type(exc).__name__}: {exc}"
            ) from None
        state = getattr(allocation, "state", None)
        if state is not None and state not in LIVE_STATES:
            raise AuthorityLost(f"allocation {self.run_id!r} is {state.value!r} after renewal")
        self.renewals += 1


def _heartbeat_default(ttl_s: float) -> float:
    """Three beats per TTL: two missed beats still leave margin before expiry."""
    return max(0.01, float(ttl_s) / 3.0)


def run_supervised(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path,
    dry_run: bool,
    authority: Any = None,
    run_id: str = "",
    fence: int = 0,
    ttl_s: float = 900.0,
    heartbeat_s: float | None = None,
    terminate_grace_s: float = 5.0,
    drain_after_exit_s: float = 1.0,
    kill_group: Callable[[int, int], None] = os.killpg,
    group_alive: Callable[[int], bool] = _group_alive,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Run ``command`` while keeping the lease alive, or kill it and say why.

    Returns an :class:`Outcome` describing exit status, how many renewals and
    fence checks happened, and whether teardown was **confirmed**. Raises
    :class:`RenewalFailed` / :class:`AuthorityLost` (each carrying its own
    ``Outcome``) when supervision cannot continue — in both cases after the
    process group has been terminated.

    ``drain_after_exit_s`` bounds how long the output pipe is drained after the
    child itself exits, so a surviving grandchild cannot keep the runner (and
    the lane) busy forever.

    ``dry_run`` executes nothing and touches neither the log nor the authority.
    """
    cmd = [str(part) for part in command]
    rendered = " ".join(shlex.quote(part) for part in cmd)
    print(f"==> Command: {rendered}")
    print(f"==> CWD:     {cwd}")
    print(f"==> Log:     {log_path}")

    heartbeat = _heartbeat_default(ttl_s) if heartbeat_s is None else float(heartbeat_s)
    if heartbeat <= 0:
        raise ValueError("heartbeat_s must be positive")
    if float(ttl_s) <= 0:
        raise ValueError("ttl_s must be positive")
    if heartbeat >= float(ttl_s):
        raise ValueError(
            f"heartbeat_s ({heartbeat}) must be shorter than ttl_s ({ttl_s}): "
            "a beat that cannot land before expiry does not keep the lease alive"
        )

    if dry_run:
        # Nothing spawns, so nothing can be unaccounted for. Deliberately no
        # authority call and no log file: a dry run must not mint store state.
        return Outcome(exit_code=0, cleanup=Cleanup.CONFIRMED, cleanup_detail="dry run: no child spawned")

    if authority is None or not run_id:
        raise MissingAuthority("supervision requires an authority and a run_id")

    started = clock()
    assert_authority_valid(authority, run_id, fence)  # before the child exists
    supervisor = _Supervisor(
        authority,
        run_id,
        fence,
        ttl_s=ttl_s,
        heartbeat_s=heartbeat,
        terminate_grace_s=terminate_grace_s,
        kill_group=kill_group,
        group_alive=group_alive,
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            start_new_session=True,  # own session ⇒ pgid == pid ⇒ a group to kill
        )
        assert process.stdout is not None
        pgid = process.pid
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)

        next_beat = clock() + heartbeat
        failure: SupervisionError | None = None
        confirmed = True
        detail = ""
        exited_at: float | None = None
        try:
            while True:
                if clock() >= next_beat:
                    try:
                        supervisor.beat()
                    except SupervisionError as exc:
                        failure = exc
                        break
                    next_beat = clock() + heartbeat

                wait = max(0.0, min(next_beat - clock(), 0.25))
                ready = selector.select(wait)
                if ready:
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if chunk:
                        text = decoder.decode(chunk)
                        if text:
                            sys.stdout.write(text)
                            sys.stdout.flush()
                            log_file.write(text)
                            log_file.flush()
                    else:
                        selector.unregister(process.stdout)
                        break

                if process.poll() is not None:
                    # The child is gone. Its pipe may still be held open by a
                    # grandchild: keep draining (and keep the lease alive) but
                    # only up to the deadline, otherwise a leftover descendant
                    # could hold this runner — and its lane — open forever.
                    if exited_at is None:
                        exited_at = clock()
                    elif clock() - exited_at >= drain_after_exit_s:
                        with contextlib.suppress(KeyError, ValueError):
                            selector.unregister(process.stdout)
                        break
        finally:
            selector.close()
            tail = decoder.decode(b"", final=True)
            if tail:
                sys.stdout.write(tail)
                log_file.write(tail)

        if failure is not None:
            # Requirement (b)/(c): a run whose accounting is gone must not keep
            # mutating anything. Kill the group, learn whether it died, and
            # only then report — carrying the teardown evidence with the error.
            confirmed, detail = _stop_group(
                process,
                pgid,
                grace_s=terminate_grace_s,
                kill_group=kill_group,
                group_alive=group_alive,
            )
            outcome = Outcome(
                exit_code=failure.exit_code,
                renewals=supervisor.renewals,
                fence_checks=supervisor.fence_checks,
                cleanup=Cleanup.CONFIRMED if confirmed else Cleanup.UNCONFIRMED,
                spawned=True,
                terminated=True,
                duration_s=clock() - started,
                cleanup_detail=detail,
            )
            raise type(failure)(
                f"{failure.detail} — child group {pgid} terminated ({detail})",
                outcome=outcome,
            ) from None

        exit_code = process.wait()
        # A child that leaves the group alive (a daemonised grandchild) means
        # teardown is NOT confirmed; report that instead of claiming clean.
        if group_alive(pgid):
            confirmed, detail = _stop_group(
                process,
                pgid,
                grace_s=terminate_grace_s,
                kill_group=kill_group,
                group_alive=group_alive,
            )
            detail = f"child exited but left group {pgid} alive; {detail}"
        else:
            detail = "child exited and its process group is gone"

    return Outcome(
        exit_code=exit_code,
        renewals=supervisor.renewals,
        fence_checks=supervisor.fence_checks,
        cleanup=Cleanup.CONFIRMED if confirmed else Cleanup.UNCONFIRMED,
        spawned=True,
        terminated=False,
        duration_s=clock() - started,
        cleanup_detail=detail,
    )


def supervised_tee_run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path,
    dry_run: bool,
    authority: Any = None,
    run_id: str = "",
    fence: int = 0,
    ttl_s: float = 900.0,
    heartbeat_s: float | None = None,
    terminate_grace_s: float = 5.0,
    drain_after_exit_s: float = 1.0,
    kill_group: Callable[[int, int], None] = os.killpg,
    group_alive: Callable[[int], bool] = _group_alive,
) -> int:
    """``tee_run``'s contract, supervised: streams, tees, returns the status.

    Drop-in for the plain runner helper — same five arguments and the same
    integer return — plus the lease it needs to stay honest. Use
    :func:`run_supervised` when the caller also wants the teardown evidence.
    """
    return run_supervised(
        command,
        cwd=cwd,
        env=env,
        log_path=log_path,
        dry_run=dry_run,
        authority=authority,
        run_id=run_id,
        fence=fence,
        ttl_s=ttl_s,
        heartbeat_s=heartbeat_s,
        terminate_grace_s=terminate_grace_s,
        drain_after_exit_s=drain_after_exit_s,
        kill_group=kill_group,
        group_alive=group_alive,
    ).exit_code
