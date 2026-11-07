"""Unit + multi-process tests for lib/fleet_authority.py.

The gate that matters here is acceptance gate 2 of the audit: two SEPARATE
scheduler PROCESSES (not threads, no mock lock) read the same one-slot
snapshot and both try to submit. Exactly one atomic reservation may result and
the loser must stay queued. That is why the contention tests below fork real
``python -c`` children that speak to the same SQLite authority: a thread-based
test with a fake lock would pass while the real race stayed open.

Everything in this module is host-local: a tmp_path store, no coordinator, no
hardware, no network.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from lib.fleet_authority import (
    ENV_AUTHORITY_PATH,
    Allocation,
    AuthorityError,
    Demand,
    Fenced,
    FleetAuthority,
    LaneSpec,
    NotGranted,
    State,
    UnknownRun,
    default_authority_path,
)

ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.story("Fleet capacity")]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _spec(lane_id: str = "lane-a", **overrides) -> LaneSpec:
    base = dict(
        lane_id=lane_id,
        fleet_id="fleet-a",
        resource_id=f"place-{lane_id}",
        coordinator="coord.example:20408",
        vcpus_total=4,
        ram_mib_total=4096,
        ram_mib_reserve=512,
        disk_mib_total=8192,
        inodes_total=4096,
        domains=("rf-band-a",),
        max_concurrent=1,
    )
    base.update(overrides)
    return LaneSpec(**base)


@pytest.fixture()
def store(tmp_path) -> Path:
    return tmp_path / "authority.sqlite3"


@pytest.fixture()
def authority(store) -> FleetAuthority:
    auth = FleetAuthority(store)
    auth.register_lane(_spec("lane-a"))
    # A fresh store starts with needs_reconcile=1: nothing is scheduled until a
    # restart has been reconciled. Every test therefore starts from the same
    # state a real host is in after boot.
    auth.reconcile()
    return auth


DEMAND = Demand(vcpus=2, ram_mib=1024, disk_mib=2048, inodes=100)


# --------------------------------------------------------------------------- #
# Demand / LaneSpec validation — unknown or nonsense dimensions never default
# --------------------------------------------------------------------------- #
class TestVectors:
    def test_demand_requires_every_dimension(self):
        with pytest.raises(ValueError, match="missing dimension"):
            Demand.from_mapping({"vcpus": 1, "ram_mib": 512})

    @pytest.mark.parametrize("field", ["vcpus", "ram_mib", "disk_mib", "inodes"])
    def test_demand_rejects_zero_and_negative(self, field):
        kwargs = {"vcpus": 1, "ram_mib": 512, "disk_mib": 512, "inodes": 1}
        for bad in (0, -1):
            kwargs[field] = bad
            with pytest.raises(ValueError, match=field):
                Demand(**kwargs)

    def test_demand_rejects_bool(self):
        with pytest.raises(ValueError, match="vcpus"):
            Demand(vcpus=True, ram_mib=512, disk_mib=512)

    def test_lane_spec_requires_resource_id(self):
        with pytest.raises(ValueError, match="resource_id"):
            LaneSpec(lane_id="x", fleet_id="f", resource_id="")

    def test_lane_spec_negative_baseline_rejected(self):
        with pytest.raises(ValueError, match="baseline_ram_mib"):
            LaneSpec(lane_id="x", fleet_id="f", resource_id="p", baseline_ram_mib=-1)


# --------------------------------------------------------------------------- #
# Atomic admission — the core transaction
# --------------------------------------------------------------------------- #
class TestAdmission:
    def test_first_admission_grants_and_issues_token_fence_expiry(self, authority):
        decision = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", ttl_s=60)
        assert decision.granted, decision.detail
        alloc = decision.allocation
        assert alloc is not None
        assert alloc.state is State.RESERVED
        assert alloc.token and alloc.token != alloc.run_id
        assert alloc.fence == 1
        assert alloc.expires_at > time.time()
        assert alloc.demand == DEMAND

    def test_second_admission_on_full_lane_is_refused_and_queued(self, authority):
        assert authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted
        loser = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-2")
        assert not loser.granted
        assert loser.reason in {"lane_busy", "resource_vector_insufficient"}
        assert loser.queue_position == 1
        assert [w[0] for w in authority.queue("lane-a")] == ["run-2"]

    def test_reserved_alone_does_not_free_budget(self, authority):
        """A reservation that has not activated still occupies its vector."""
        authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1")
        room = authority.headroom("lane-a")
        assert room.used["vcpus"] == DEMAND.vcpus
        assert room.used["ram_mib"] == DEMAND.ram_mib

    def test_identical_retry_of_a_live_run_id_replays_the_same_claim(self, authority):
        """Lost reply ≠ new grant: retrying the idempotency key must NOT
        consume a second slot."""
        first = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1")
        assert first.granted
        replay = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1")
        assert replay.granted
        assert replay.allocation.token == first.allocation.token
        assert replay.allocation.fence == first.allocation.fence
        assert "idempotent" in replay.detail
        assert len(authority.allocations(lane_id="lane-a", states=[State.RESERVED])) == 1
        assert authority.headroom("lane-a").used["vcpus"] == DEMAND.vcpus

    def test_reused_run_id_with_a_different_vector_is_refused(self, authority):
        assert authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted
        clash = authority.try_reserve(
            lane_id="lane-a", demand=Demand(vcpus=1, ram_mib=256, disk_mib=256), run_id="run-1"
        )
        assert not clash.granted
        assert clash.reason == "duplicate_run_id"
        assert "different request" in clash.detail

    def test_reused_run_id_after_release_is_not_replayed(self, authority):
        first = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        authority.abort("run-1", first.fence)
        again = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1")
        assert not again.granted
        assert again.reason == "duplicate_run_id"

    def test_unknown_lane_refused(self, authority):
        decision = authority.try_reserve(lane_id="nope", demand=DEMAND)
        assert not decision.granted
        assert decision.reason == "lane_unknown"


# --------------------------------------------------------------------------- #
# Resource-vector admission: one dimension short means admit NOTHING
# --------------------------------------------------------------------------- #
class TestResourceVector:
    def _lane(self, authority, **overrides):
        authority.register_lane(_spec("lane-v", **overrides))

    def test_ram_fits_but_disk_does_not_admits_nothing(self, authority):
        self._lane(authority, ram_mib_total=8192, ram_mib_reserve=0, disk_mib_total=1024)
        decision = authority.try_reserve(
            lane_id="lane-v", demand=Demand(vcpus=1, ram_mib=512, disk_mib=4096), run_id="run-1"
        )
        assert not decision.granted
        assert decision.reason == "resource_vector_insufficient"
        assert "disk_mib" in decision.detail
        # Not a partial grant: nothing is held.
        assert authority.allocations(lane_id="lane-v") == ()

    def test_ram_fits_but_cpu_does_not_admits_nothing(self, authority):
        self._lane(authority, vcpus_total=1)
        decision = authority.try_reserve(
            lane_id="lane-v", demand=Demand(vcpus=2, ram_mib=256, disk_mib=256), run_id="run-1"
        )
        assert not decision.granted
        assert decision.reason == "resource_vector_insufficient"
        assert "vcpus" in decision.detail

    def test_ram_reserve_is_never_admitted_into(self, authority):
        self._lane(authority, ram_mib_total=4096, ram_mib_reserve=3584)
        decision = authority.try_reserve(
            lane_id="lane-v", demand=Demand(vcpus=1, ram_mib=1024, disk_mib=256), run_id="run-1"
        )
        assert not decision.granted
        assert "ram_mib" in decision.detail
        assert decision.headroom["ram_mib"] == pytest.approx(512)

    def test_baseline_of_unmanaged_guest_counts_once(self, authority):
        """A pre-existing idle guest is consumption, not free capacity."""
        self._lane(authority, ram_mib_total=2048, ram_mib_reserve=0, baseline_ram_mib=1536)
        decision = authority.try_reserve(
            lane_id="lane-v", demand=Demand(vcpus=1, ram_mib=1024, disk_mib=256), run_id="run-1"
        )
        assert not decision.granted
        assert decision.headroom["ram_mib"] == pytest.approx(512)

    def test_running_allocation_is_not_double_subtracted(self, authority):
        """The baseline holds UNMANAGED use; managed runs come from the ledger.

        If the ledger run were also baked into the baseline it would be
        subtracted twice and this second admission would be refused.
        """
        self._lane(authority, ram_mib_total=4096, ram_mib_reserve=0, max_concurrent=2, baseline_ram_mib=0)
        first = Demand(vcpus=1, ram_mib=1024, disk_mib=256)
        assert authority.try_reserve(lane_id="lane-v", demand=first, run_id="run-1").granted
        second = Demand(vcpus=1, ram_mib=1024, disk_mib=256)
        decision = authority.try_reserve(lane_id="lane-v", demand=second, run_id="run-2")
        assert decision.granted, decision.detail
        room = authority.headroom("lane-v")
        assert room.used["ram_mib"] == 2048  # exactly two runs, not four

    def test_restart_reuse_of_guest_does_not_recount(self, authority):
        """Re-registering the same lane with the same baseline is stable.

        A reused guest is already in the baseline; re-registering must not
        accumulate it into a phantom second copy.
        """
        self._lane(authority, ram_mib_total=4096, ram_mib_reserve=0, baseline_ram_mib=1024)
        before = authority.headroom("lane-v").free["ram_mib"]
        authority.register_lane(_spec("lane-v", ram_mib_total=4096, ram_mib_reserve=0, baseline_ram_mib=1024))
        after = authority.headroom("lane-v").free["ram_mib"]
        assert before == after == pytest.approx(3072)


# --------------------------------------------------------------------------- #
# Conflict domains — real resources, not "the whole exporter"
# --------------------------------------------------------------------------- #
class TestConflictDomains:
    def test_two_independent_lanes_overlap(self, authority):
        authority.register_lane(_spec("lane-b", resource_id="place-b", domains=("rf-band-b",)))
        d1 = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1")
        d2 = authority.try_reserve(lane_id="lane-b", demand=DEMAND, run_id="run-2")
        assert d1.granted and d2.granted
        assert len(authority.allocations(states=[State.RESERVED])) == 2

    def test_shared_power_domain_prevents_overlap_even_for_different_places(self, authority):
        authority.register_lane(_spec("lane-b", resource_id="place-b", domains=("rf-band-a",)))
        assert authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted
        blocked = authority.try_reserve(lane_id="lane-b", demand=DEMAND, run_id="run-2")
        assert not blocked.granted
        assert blocked.reason == "domain_conflict"
        assert "rf-band-a" in blocked.detail

    def test_explicit_extra_domain_is_honoured(self, authority):
        """A power-strip reset or global hwsim reload takes the broad domain."""
        authority.register_lane(_spec("lane-b", resource_id="place-b", domains=("rf-band-b",)))
        assert authority.try_reserve(
            lane_id="lane-a", demand=DEMAND, run_id="run-1", domains=("power-strip-0",)
        ).granted
        blocked = authority.try_reserve(
            lane_id="lane-b", demand=DEMAND, run_id="run-2", domains=("power-strip-0",)
        )
        assert not blocked.granted
        assert blocked.reason == "domain_conflict"

    def test_sharing_an_exporter_does_not_serialize_by_itself(self, authority):
        """Two lanes behind one exporter process with no declared shared domain
        run concurrently — the audit forbids serializing on the exporter alone."""
        authority.register_lane(_spec("lane-b", resource_id="place-b", domains=("rf-band-b",), coordinator="coord.example:20408"))
        assert authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted
        assert authority.try_reserve(lane_id="lane-b", demand=DEMAND, run_id="run-2").granted

    def test_domain_is_released_only_after_clean_completion(self, authority):
        authority.register_lane(_spec("lane-b", resource_id="place-b", domains=("rf-band-a",)))
        first = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1")
        authority.abort("run-1", first.allocation.fence)
        second = authority.try_reserve(lane_id="lane-b", demand=DEMAND, run_id="run-2")
        assert second.granted


# --------------------------------------------------------------------------- #
# Two-phase lifecycle and fencing
# --------------------------------------------------------------------------- #
class TestTwoPhase:
    def test_activate_then_renew_then_complete(self, authority):
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", ttl_s=30).allocation
        active = authority.activate("run-1", alloc.fence)
        assert active.state is State.ACTIVE
        renewed = authority.renew("run-1", alloc.fence, ttl_s=60)
        assert renewed.expires_at > active.expires_at
        done = authority.complete("run-1", alloc.fence, clean=True, detail="released clean")
        assert done.state is State.RELEASED
        assert done.cleanup_pending is False
        assert authority.headroom("lane-a").used["vcpus"] == 0

    def test_cannot_activate_twice(self, authority):
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        authority.activate("run-1", alloc.fence)
        with pytest.raises(NotGranted, match="not_reservable"):
            authority.activate("run-1", alloc.fence)

    def test_stale_fence_cannot_renew_activate_or_release(self, authority):
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        old_fence = alloc.fence
        authority.fence("run-1", note="supervisor stopped it")
        with pytest.raises(Fenced):
            authority.renew("run-1", old_fence, ttl_s=60)
        with pytest.raises(Fenced):
            authority.activate("run-1", old_fence)
        with pytest.raises(Fenced):
            authority.complete("run-1", old_fence, clean=True)

    def test_abort_only_from_reserved(self, authority):
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        authority.activate("run-1", alloc.fence)
        with pytest.raises(NotGranted, match="not_abortable"):
            authority.abort("run-1", alloc.fence)

    def test_unknown_run_raises(self, authority):
        with pytest.raises(UnknownRun):
            authority.allocation("nope")
        with pytest.raises(UnknownRun):
            authority.fence("nope")


# --------------------------------------------------------------------------- #
# Expiry quarantines: TTL alone never reassigns a lane
# --------------------------------------------------------------------------- #
class TestExpiry:
    def test_expired_lease_quarantines_and_keeps_occupying(self, authority):
        authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1", ttl_s=0.01)
        time.sleep(0.02)
        expired = authority.expire()
        assert [a.run_id for a in expired] == ["run-1"]
        assert authority.quarantined()["lane-a"][0] == "lease_expired"
        nxt = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-2")
        assert not nxt.granted
        assert nxt.reason == "lane_quarantined"

    def test_expired_vector_still_counts_against_the_budget(self, authority):
        """Quarantined-but-uncleaned work keeps its room: the DUT may still run."""
        self._lane_small(authority)
        authority.try_reserve(
            lane_id="lane-small",
            demand=Demand(vcpus=1, ram_mib=1024, disk_mib=256),
            run_id="run-1",
            ttl_s=0.01,
        )
        time.sleep(0.02)
        authority.expire()
        room = authority.headroom("lane-small")
        assert room.used["ram_mib"] == 1024

    def _lane_small(self, authority):
        authority.register_lane(_spec("lane-small", ram_mib_total=2048, ram_mib_reserve=0))

    def test_clear_quarantine_returns_lane_to_service(self, authority):
        authority.quarantine("lane-a", "manual", "operator verified clean")
        assert not authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted
        authority.clear_quarantine("lane-a", note="verified")
        assert authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted


# --------------------------------------------------------------------------- #
# Cleanup-pending is durable and keeps excluding the lane
# --------------------------------------------------------------------------- #
class TestCleanupPending:
    def test_failed_release_persists_and_excludes_lane(self, authority):
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        authority.activate("run-1", alloc.fence)
        pending = authority.complete("run-1", alloc.fence, clean=False, detail="coordinator did not answer")
        assert pending.state is State.CLEANUP_PENDING
        assert pending.cleanup_pending is True
        blocked = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-2")
        assert not blocked.granted
        assert blocked.reason == "lane_quarantined"
        assert authority.cleanup_pending() == (pending,)

    def test_cleanup_pending_survives_a_successful_later_run_elsewhere(self, authority):
        authority.register_lane(_spec("lane-b", resource_id="place-b", domains=("rf-band-b",)))
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        authority.activate("run-1", alloc.fence)
        authority.complete("run-1", alloc.fence, clean=False, detail="boom")
        other = authority.try_reserve(lane_id="lane-b", demand=DEMAND, run_id="run-2").allocation
        authority.activate("run-2", other.fence)
        authority.complete("run-2", other.fence, clean=True)
        assert [a.run_id for a in authority.cleanup_pending()] == ["run-1"]

    def test_clear_cleanup_pending_requires_the_pending_state(self, authority):
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        authority.activate("run-1", alloc.fence)
        authority.complete("run-1", alloc.fence, clean=True)
        with pytest.raises(NotGranted, match="no_cleanup_pending"):
            authority.clear_cleanup_pending("run-1")

    def test_clean_retry_after_pending_releases_lane(self, authority):
        alloc = authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        authority.activate("run-1", alloc.fence)
        authority.complete("run-1", alloc.fence, clean=False, detail="boom")
        cleared = authority.clear_cleanup_pending("run-1", detail="released on retry")
        assert cleared.state is State.RELEASED
        assert authority.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-2").granted


# --------------------------------------------------------------------------- #
# Restart / epoch invalidation
# --------------------------------------------------------------------------- #
class TestEpoch:
    def test_fresh_store_requires_reconcile_before_admission(self, tmp_path):
        auth = FleetAuthority(tmp_path / "fresh.sqlite3")
        auth.register_lane(_spec("lane-a"))
        refused = auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1")
        assert not refused.granted
        assert refused.reason == "authority_needs_reconcile"
        auth.reconcile()
        assert auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").granted

    def test_restart_invalidates_epoch_and_quarantines_inflight(self, store):
        auth = FleetAuthority(store)
        auth.register_lane(_spec("lane-a"))
        auth.reconcile()
        alloc = auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        auth.activate("run-1", alloc.fence)
        epoch = auth.epoch

        # Simulate host restart: a new object over the same store.
        restarted = FleetAuthority(store)
        new_epoch = restarted.invalidate_epoch("host restart")
        assert new_epoch == epoch + 1
        assert restarted.needs_reconcile() is True
        assert restarted.allocation("run-1").state is State.QUARANTINED
        assert "lane-a" in restarted.quarantined()

        reconciled = restarted.reconcile()
        assert [a.run_id for a in reconciled] == ["run-1"]
        blocked = restarted.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-2")
        assert not blocked.granted
        assert blocked.reason == "lane_quarantined"

    def test_epoch_invalidation_fences_old_tokens(self, store):
        auth = FleetAuthority(store)
        auth.register_lane(_spec("lane-a"))
        auth.reconcile()
        alloc = auth.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-1").allocation
        auth.invalidate_epoch("restart")
        with pytest.raises(Fenced):
            auth.renew("run-1", alloc.fence, ttl_s=60)

    def test_schema_mismatch_refused(self, store):
        auth = FleetAuthority(store)
        auth.register_lane(_spec("lane-a"))
        import sqlite3

        conn = sqlite3.connect(store)
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        with pytest.raises(AuthorityError, match="schema_version"):
            FleetAuthority(store)


# --------------------------------------------------------------------------- #
# MULTI-PROCESS LAST-SLOT CONTENTION (acceptance gate 2)
# --------------------------------------------------------------------------- #
_CHILD = textwrap.dedent(
    """
    import json, sys, time
    sys.path.insert(0, {root!r})
    from lib.fleet_authority import Demand, FleetAuthority

    store, run_id, barrier_path, out_path = sys.argv[1:5]
    auth = FleetAuthority(store)
    demand = Demand(vcpus=int(sys.argv[5]), ram_mib=int(sys.argv[6]), disk_mib=int(sys.argv[7]))

    # Synchronise AFTER reading the same snapshot, like two schedulers that each
    # looked at "one slot free" and then submitted.
    snapshot_free = auth.headroom("lane-a").free
    with open(out_path + ".snapshot", "w") as fh:
        json.dump(snapshot_free, fh)
    deadline = time.time() + 20
    while not __import__("os").path.exists(barrier_path):
        if time.time() > deadline:
            break
        time.sleep(0.01)

    decision = auth.try_reserve(lane_id="lane-a", demand=demand, run_id=run_id)
    with open(out_path, "w") as fh:
        json.dump(
            {{
                "run_id": run_id,
                "granted": decision.granted,
                "reason": decision.reason,
                "detail": decision.detail,
                "queue_position": decision.queue_position,
                "free_before": snapshot_free,
                "fence": decision.allocation.fence if decision.allocation else None,
                "token": decision.allocation.token if decision.allocation else None,
            }},
            fh,
        )
    """
)


def _spawn_racer(store: Path, run_id: str, barrier: Path, out: Path, demand: Demand, cwd: Path) -> subprocess.Popen:
    code = _CHILD.format(root=str(ROOT))
    env = dict(os.environ)
    env.pop(ENV_AUTHORITY_PATH, None)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(store),
            run_id,
            str(barrier),
            str(out),
            str(demand.vcpus),
            str(demand.ram_mib),
            str(demand.disk_mib),
        ],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_race(store: Path, tmp_path: Path, demand: Demand, cwd: Path, run_ids=("run-a", "run-b")):
    barrier = tmp_path / "GO"
    outs = [tmp_path / f"{rid}.json" for rid in run_ids]
    procs = [_spawn_racer(store, rid, barrier, out, demand, cwd) for rid, out in zip(run_ids, outs)]
    # Wait until every child has read its snapshot, then release them together.
    deadline = time.time() + 30
    while time.time() < deadline:
        if all(Path(str(o) + ".snapshot").exists() for o in outs):
            break
        time.sleep(0.02)
    snapshots = [json.loads(Path(str(o) + ".snapshot").read_text()) for o in outs]
    barrier.write_text("go")
    results = []
    for proc, out in zip(procs, outs):
        stdout, stderr = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"{proc.args[4]} failed: {stderr}\n{stdout}"
        results.append(json.loads(out.read_text()))
    return snapshots, results


class TestLastSlotContention:
    """Acceptance gate 2 — real processes, real store, no mock lock."""

    def _one_slot_store(self, store: Path) -> Path:
        auth = FleetAuthority(store)
        auth.register_lane(
            _spec(
                "lane-a",
                vcpus_total=2,
                ram_mib_total=2048,
                ram_mib_reserve=0,
                disk_mib_total=4096,
                inodes_total=4096,
                max_concurrent=1,
            )
        )
        auth.reconcile()
        return store

    def test_two_processes_one_slot_exactly_one_reservation(self, store, tmp_path):
        self._one_slot_store(store)
        demand = Demand(vcpus=2, ram_mib=1024, disk_mib=2048)
        snapshots, results = _run_race(store, tmp_path, demand, ROOT)

        # Both processes genuinely saw a free slot: this is the contention the
        # ticket is about, not a trivially-serialized case.
        for snap in snapshots:
            assert snap["vcpus"] >= demand.vcpus
            assert snap["ram_mib"] >= demand.ram_mib

        granted = [r for r in results if r["granted"]]
        refused = [r for r in results if not r["granted"]]
        assert len(granted) == 1, results
        assert len(refused) == 1, results
        assert refused[0]["queue_position"] == 1

        auth = FleetAuthority(store)
        live = auth.allocations(lane_id="lane-a", states=[State.RESERVED])
        assert len(live) == 1
        assert live[0].run_id == granted[0]["run_id"]

    def test_loser_stays_queued_not_retried_forever(self, store, tmp_path):
        self._one_slot_store(store)
        _, results = _run_race(store, tmp_path, Demand(vcpus=2, ram_mib=1024, disk_mib=2048), ROOT)
        auth = FleetAuthority(store)
        queued = [w[0] for w in auth.queue("lane-a")]
        loser = next(r for r in results if not r["granted"])
        assert queued == [loser["run_id"]]

    def test_counters_never_exceed_the_budget(self, store, tmp_path):
        self._one_slot_store(store)
        demand = Demand(vcpus=2, ram_mib=1024, disk_mib=2048)
        _run_race(store, tmp_path, demand, ROOT)
        auth = FleetAuthority(store)
        room = auth.headroom("lane-a")
        assert room.used["vcpus"] <= 2
        assert room.used["ram_mib"] <= 2048
        assert room.used["disk_mib"] <= 4096
        assert room.free["vcpus"] >= 0
        assert room.free["ram_mib"] >= 0

    def test_repeat_across_worktrees_and_hosts(self, store, tmp_path):
        """Same authority, different submitting hosts and different cwds.

        A per-worktree lock or a per-host assumption would let both through;
        the authority is the only shared thing and it must still admit one.
        """
        self._one_slot_store(store)
        demand = Demand(vcpus=2, ram_mib=1024, disk_mib=2048)
        worktree_a = tmp_path / "wt-a"
        worktree_b = tmp_path / "wt-b"
        worktree_a.mkdir()
        worktree_b.mkdir()

        barrier = tmp_path / "GO2"
        outs = [tmp_path / "host1.json", tmp_path / "host2.json"]
        procs = [
            _spawn_racer(store, "run-host1", barrier, outs[0], demand, worktree_a),
            _spawn_racer(store, "run-host2", barrier, outs[1], demand, worktree_b),
        ]
        deadline = time.time() + 30
        while time.time() < deadline:
            if all(Path(str(o) + ".snapshot").exists() for o in outs):
                break
            time.sleep(0.02)
        barrier.write_text("go")
        results = []
        for proc, out in zip(procs, outs):
            stdout, stderr = proc.communicate(timeout=60)
            assert proc.returncode == 0, stderr
            results.append(json.loads(out.read_text()))

        assert sum(1 for r in results if r["granted"]) == 1, results
        auth = FleetAuthority(store)
        assert len(auth.allocations(lane_id="lane-a", states=[State.RESERVED])) == 1

    def test_two_slots_admit_two_processes_concurrently(self, tmp_path):
        store = self._one_slot_store(tmp_path / "two.sqlite3")
        auth = FleetAuthority(store)
        auth.register_lane(
            _spec(
                "lane-a",
                vcpus_total=4,
                ram_mib_total=4096,
                ram_mib_reserve=0,
                disk_mib_total=8192,
                max_concurrent=2,
            )
        )
        demand = Demand(vcpus=2, ram_mib=1024, disk_mib=2048)
        _, results = _run_race(store, tmp_path, demand, ROOT)
        assert sum(1 for r in results if r["granted"]) == 2, results
        assert len(FleetAuthority(store).allocations(lane_id="lane-a", states=[State.RESERVED])) == 2


# --------------------------------------------------------------------------- #
# Cross-process lifecycle: fence + cleanup are visible to the next process
# --------------------------------------------------------------------------- #
class TestCrossProcess:
    def test_sigkill_of_holder_leaves_lane_quarantined_not_reassigned(self, store, tmp_path):
        auth = FleetAuthority(store)
        auth.register_lane(_spec("lane-a"))
        auth.reconcile()

        code = textwrap.dedent(
            f"""
            import sys, time
            sys.path.insert(0, {str(ROOT)!r})
            from lib.fleet_authority import Demand, FleetAuthority
            auth = FleetAuthority({str(store)!r})
            d = auth.try_reserve(lane_id="lane-a", demand=Demand(vcpus=2, ram_mib=1024, disk_mib=2048),
                                 run_id="run-killed", ttl_s=600)
            print("GRANTED" if d.granted else "REFUSED", flush=True)
            time.sleep(600)
            """
        )
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "GRANTED"
        proc.kill()  # SIGKILL
        proc.wait(timeout=30)

        after = FleetAuthority(store)
        alloc = after.allocation("run-killed")
        assert alloc.state is State.RESERVED  # the store still believes it is live

        # The next run must NOT be admitted while that claim is unreconciled:
        # a host restart is the signal that something died.
        after.invalidate_epoch("holder SIGKILLed")
        after.reconcile()
        blocked = after.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-next")
        assert not blocked.granted
        assert blocked.reason == "lane_quarantined"

        # ... and only a verified cleanup returns the lane.
        after.fence("run-killed", note="holder died")
        after.quarantine("lane-a", "cleanup_verified", "operator confirmed DUT idle")
        after.clear_quarantine("lane-a", note="clean")
        assert after.try_reserve(lane_id="lane-a", demand=DEMAND, run_id="run-next").granted


def test_default_authority_path_has_no_implicit_default(monkeypatch):
    monkeypatch.delenv(ENV_AUTHORITY_PATH, raising=False)
    with pytest.raises(AuthorityError, match="no fleet authority store"):
        default_authority_path()
    assert default_authority_path("~/.hermes/state").name == "fleet-authority.sqlite3"
    # The explicit env var wins over any state dir — there is no second guess.
    monkeypatch.setenv(ENV_AUTHORITY_PATH, "/explicit/store.sqlite3")
    assert str(default_authority_path("~/.hermes/state")) == "/explicit/store.sqlite3"
