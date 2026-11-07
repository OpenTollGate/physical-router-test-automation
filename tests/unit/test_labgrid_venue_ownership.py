"""Ownership/lease semantics for ``lib/labgrid_venue.LabgridBench``.

Regression rail for the audit's "Critical existing race" (2026-10-30):

    ``acquire()`` attempted an unconditional best-effort ``release`` on ANY
    acquire error — including an ambiguous timeout or contention. With a shared
    labgrid client identity that release can affect ANOTHER run's acquisition,
    and ``release()`` also cleared local state even when the remote release
    failed.

The fake coordinator below reproduces the *real* semantics of the pinned
labgrid client (26.0.1) closely enough to catch each half of that bug, because
the numbers came from a live coordinator, not from reading our wrapper:

* ownership is a single string ``<host>/<user>`` (``LG_HOSTNAME`` /
  ``LG_USERNAME``, ``labgrid/remote/client.py:102-106``);
* ``acquire`` refuses when the place is already held, whoever holds it;
* ``release`` on a free place fails, and on another user's place refuses
  without ``--kick``;
* ``release-from HOST/USER`` is conditional *server side*: the coordinator
  returns success WITHOUT releasing when the owner differs
  (``labgrid/remote/coordinator.py:913``). Success therefore does not prove a
  release happened — the caller must re-read ownership. These tests assert we
  do exactly that.
"""
from __future__ import annotations

import subprocess

import pytest

from lib.labgrid_venue import ClientIdentity, LabgridBench, LabgridBenchError

PLACE = "x1860-1"
COORD = "coord.example:20408"


class FakeCoordinator:
    """One place, real ownership rules, injectable faults."""

    def __init__(self, owner: str | None = None):
        self.owner = owner
        self.calls: list[list[str]] = []
        #: fault injection: verb -> behaviour
        self.acquire_error = ""          # rc=1 with this stderr
        self.acquire_grant_then_fail = False  # grant, then report a timeout
        self.release_error = ""          # rc=1 with this stderr
        self.release_silently_ignored = False  # rc=0 but nothing released
        self.show_error = ""

    # -- helpers -----------------------------------------------------------
    @property
    def verbs(self) -> list[str]:
        return [c[5] if len(c) > 5 else "" for c in self.calls]

    def _identity(self, kwargs) -> str:
        env = kwargs.get("env") or {}
        return f"{env.get('LG_HOSTNAME', 'fallback-host')}/{env.get('LG_USERNAME', 'fallback-user')}"

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        args = cmd[5:]
        verb = args[0] if args else ""
        who = self._identity(kwargs)

        if verb == "acquire":
            if self.acquire_grant_then_fail:
                self.owner = who
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="Error: timed out waiting for the coordinator"
                )
            if self.acquire_error:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=self.acquire_error)
            if self.owner:
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    stdout="",
                    stderr=f"Error: Place {PLACE} is already acquired by {self.owner}.",
                )
            self.owner = who
            return subprocess.CompletedProcess(cmd, 0, stdout=f"acquired place {PLACE}\n", stderr="")

        if verb == "release":
            if self.release_error:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=self.release_error)
            if not self.owner:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"Error: place {PLACE} is not acquired")
            self.owner = None
            return subprocess.CompletedProcess(cmd, 0, stdout=f"released place {PLACE}\n", stderr="")

        if verb == "release-from":
            target = args[1] if len(args) > 1 else ""
            if self.release_error:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=self.release_error)
            if self.release_silently_ignored:
                # Real behaviour: rc=0, place still held.
                return subprocess.CompletedProcess(cmd, 0, stdout=f"{target} has released place {PLACE}\n", stderr="")
            if self.owner and self.owner == target:
                self.owner = None
            # rc=0 no matter what — success does NOT prove a release.
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{target} has released place {PLACE}\n", stderr="")

        if verb == "show":
            if self.show_error:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=self.show_error)
            body = (
                f"Place '{PLACE}':\n"
                f"  matches:\n    exp1/lane-a/NetworkService/*\n"
                f"  acquired: {self.owner if self.owner else 'None'}\n"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=body, stderr="")

        if verb == "who":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="User      Host  Place  Changed\n" + (f"{self.owner.split('/')[1]}  {self.owner.split('/')[0]}  {PLACE}  now\n" if self.owner else ""), stderr=""
            )

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def _bench(coord: FakeCoordinator, identity: ClientIdentity | None = None) -> LabgridBench:
    bench = LabgridBench(COORD, PLACE, runner=coord, identity=identity)
    bench._bin = "labgrid-client"
    return bench


IDENT = ClientIdentity(host="bench-host", user="prta-run-a")


# --------------------------------------------------------------------------- #
# identity is per-run, and reaches the client process
# --------------------------------------------------------------------------- #
class TestIdentity:
    def test_identity_defaults_to_this_host_and_user(self):
        ident = ClientIdentity.current()
        assert "/" in ident.owner
        assert ident.owner == f"{ident.host}/{ident.user}"

    def test_identity_env_is_passed_to_every_client_call(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        seen = coord.calls[-1]
        assert seen[5] == "acquire"
        # env is what makes the identity unique; without it two runs collapse
        # onto one owner string and can release each other.
        assert coord.owner == "bench-host/prta-run-a"

    def test_two_runs_have_distinct_identities_by_default(self):
        a = ClientIdentity.for_run("run-a")
        b = ClientIdentity.for_run("run-b")
        assert a.owner != b.owner
        assert "run-a" in a.owner and "run-b" in b.owner

    def test_run_identity_is_derived_from_the_run_id(self):
        ident = ClientIdentity.for_run("20261030T120000Z-smoke-abc1234")
        assert ident.user.startswith("prta-")
        assert ident.host  # host stays real: the coordinator keys on host/user


# --------------------------------------------------------------------------- #
# THE BUG: acquire error must never release somebody else's place
# --------------------------------------------------------------------------- #
class TestAcquireDoesNotReleaseOthers:
    def test_acquire_error_never_issues_a_release(self):
        coord = FakeCoordinator(owner="other-host/other-run")
        with pytest.raises(LabgridBenchError, match="already acquired"):
            _bench(coord, IDENT).acquire()
        assert "release" not in coord.verbs
        assert "release-from" not in coord.verbs
        assert coord.owner == "other-host/other-run"

    def test_contention_leaves_the_other_runs_claim_intact(self):
        coord = FakeCoordinator(owner="bench-host/prta-run-a")
        # Same host, different user: a second run on a shared identity.
        other = ClientIdentity(host="bench-host", user="prta-run-b")
        with pytest.raises(LabgridBenchError, match="already acquired"):
            _bench(coord, other).acquire()
        assert coord.owner == "bench-host/prta-run-a"
        assert "release" not in coord.verbs

    def test_ambiguous_timeout_after_grant_is_reconciled_not_released(self):
        """The exact audit scenario: grant landed, the reply did not."""
        coord = FakeCoordinator()
        coord.acquire_grant_then_fail = True
        bench = _bench(coord, IDENT)
        bench.acquire()  # must NOT raise: reconciliation proves we own it
        assert bench.acquired is True
        assert coord.owner == IDENT.owner
        assert "release" not in coord.verbs
        assert "release-from" not in coord.verbs

    def test_acquire_error_on_a_free_place_propagates_without_release(self):
        coord = FakeCoordinator()
        coord.acquire_error = "Error: Match */NetworkService/lan1 has no matching remote resource"
        with pytest.raises(LabgridBenchError, match="no matching remote resource"):
            _bench(coord, IDENT).acquire()
        assert coord.owner is None
        assert "release" not in coord.verbs

    def test_unreadable_ownership_refuses_to_guess(self):
        """If we cannot tell who holds the place, we must not assume we do."""
        coord = FakeCoordinator()
        coord.acquire_error = "Error: timed out"
        coord.show_error = "Error: coordinator is unavailable"
        with pytest.raises(LabgridBenchError, match="timed out"):
            _bench(coord, IDENT).acquire()
        assert "release" not in coord.verbs
        assert "release-from" not in coord.verbs


# --------------------------------------------------------------------------- #
# release is conditional and never clears local state on a failed remote
# --------------------------------------------------------------------------- #
class TestReleaseIsConditional:
    def test_release_uses_release_from_with_our_own_identity(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        bench.release()
        assert "release-from" in coord.verbs
        assert "release" not in coord.verbs
        release_call = coord.calls[coord.verbs.index("release-from")]
        assert release_call[6] == IDENT.owner

    def test_failed_release_keeps_local_state_and_marks_cleanup_pending(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        coord.release_error = "Error: coordinator gone"
        with pytest.raises(LabgridBenchError, match="coordinator gone"):
            bench.release()
        # The old wrapper cleared _acquired here, which is how a place leaks
        # while everyone believes it was returned.
        assert bench.acquired is True
        assert bench.cleanup_pending is True
        assert coord.owner == IDENT.owner

    def test_release_is_retried_after_a_failure_not_silently_dropped(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        coord.release_error = "Error: coordinator gone"
        with pytest.raises(LabgridBenchError):
            bench.release()
        coord.release_error = ""
        bench.release()
        assert bench.acquired is False
        assert bench.cleanup_pending is False
        assert coord.owner is None

    def test_release_that_silently_did_nothing_is_detected(self):
        """rc=0 from release-from proves nothing; ownership must be re-read."""
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        coord.release_silently_ignored = True
        with pytest.raises(LabgridBenchError, match="reported success but the lease is still held"):
            bench.release()
        assert bench.acquired is True
        assert bench.cleanup_pending is True
        assert coord.owner == IDENT.owner

    def test_release_does_not_destroy_a_successors_claim(self):
        """We thought we held it; another run holds it now. Never kick."""
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        coord.owner = "other-host/other-run"  # taken over while we were idle
        bench.release()
        assert coord.owner == "other-host/other-run"
        assert "release" not in coord.verbs
        assert bench.acquired is False

    def test_release_without_acquire_is_a_noop(self):
        coord = FakeCoordinator()
        _bench(coord, IDENT).release()
        assert coord.calls == []

    def test_double_release_only_touches_the_coordinator_once(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        bench.release()
        bench.release()
        assert coord.verbs.count("release-from") == 1

    def test_acquire_after_release_works(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        bench.release()
        bench.acquire()
        assert coord.owner == IDENT.owner


# --------------------------------------------------------------------------- #
# ownership reporting / lease verification
# --------------------------------------------------------------------------- #
class TestOwnership:
    def test_owner_reports_the_coordinator_string(self):
        coord = FakeCoordinator(owner="x/y")
        assert _bench(coord, IDENT).owner() == "x/y"

    def test_owner_reports_empty_when_free(self):
        assert _bench(FakeCoordinator(), IDENT).owner() == ""

    def test_owns_reflects_our_identity(self):
        coord = FakeCoordinator(owner=IDENT.owner)
        assert _bench(coord, IDENT).owns() is True
        coord.owner = "other/run"
        assert _bench(coord, IDENT).owns() is False

    def test_verify_reports_a_lost_lease(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        coord.owner = "other/run"
        assert bench.verify() is False

    def test_verify_reports_a_held_lease(self):
        coord = FakeCoordinator()
        bench = _bench(coord, IDENT)
        bench.acquire()
        assert bench.verify() is True
