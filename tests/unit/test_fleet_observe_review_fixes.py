"""Regression tests for the cold review of PR #334 (deepseek-flash, 2026-10-30).

Each test here pins a finding the review raised against a file in this slice's
own scope (``lib/fleet_observe.py``, ``scripts/fleet-capacity.py`` and the one
slice-1 verdict they feed). They are RED against 1baeea3 and stay red until the
behaviour is actually fixed — none of them is satisfied by a docstring edit
alone.

Findings addressed here:

* HIGH  ``read_authority_reservations`` dropped a row it could not size with
        ``continue``, so a reservation that *occupies* host capacity became
        invisible and was accounted as zero. "Missing telemetry defaulted to
        zero-as-available" is exactly what slice 1 refuses to do.
* MEDIUM the CLI module docstring said the pool's headroom is "deliberately not
        consulted" *because* the store is opened read-only. The store IS
        consulted (``read_authority_reservations``) and read-only-ness is not
        what stops headroom from being read — the real reason is that
        ``FleetAuthority`` is a writer whose constructor mints the store.
* NIT   ``LabgridReadOnlyClient.run`` validated only the subcommand token.
        ``coordinator`` and ``place`` were interpolated raw, so a manifest value
        starting with ``-`` was subject to labgrid-client's own option parsing.
* NIT   the store path was interpolated into a ``file:`` URI without escaping,
        so a path containing ``?``/``#``/``%`` opened a *different* file and a
        real store was reported as absent.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from lib.fleet_capacity import (
    HostDemand,
    Reason,
    SpawnVerdict,
    State,
    host_observation,
    new_vm_slots,
)
from lib.fleet_observe import (
    CommandResult,
    LabgridReadOnlyClient,
    ObservationRefused,
    ProbeLimits,
    read_authority_epoch,
    read_authority_reservations,
)

pytestmark = [pytest.mark.story("P11")]

NOW = 1_000_000.0
TTL = 60.0
EPOCH = 3


# ---------------------------------------------------------------------------
# Finding: an allocation that cannot be sized must not become zero capacity
# ---------------------------------------------------------------------------
def _store_with_unsizable_row(tmp_path):
    """A real authority store plus one allocation whose demand is corrupt.

    The row is written through the store's own schema so this is what a
    truncated/hand-edited demand column actually looks like on a live bench —
    not a mocked-out dataclass.
    """
    from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec

    store = tmp_path / "fleet-authority.sqlite3"
    authority = FleetAuthority(store)
    authority.reconcile()
    authority.register_host(
        HostSpec(vcpus_total=8, ram_mib_total=8192, disk_mib_total=65536, inodes_total=100_000)
    )
    authority.register_lane(
        LaneSpec(
            lane_id="lane-1",
            fleet_id="fleet-a",
            resource_id="lane-a1",
            vcpus_total=8,
            ram_mib_total=8192,
            disk_mib_total=65536,
            inodes_total=10_000,
            max_concurrent=2,
        )
    )
    granted = authority.try_reserve(
        lane_id="lane-1", demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1), run_id="run-ok"
    )
    assert granted.granted
    broken = authority.try_reserve(
        lane_id="lane-1", demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1), run_id="run-broken"
    )
    assert broken.granted, broken.detail
    conn = sqlite3.connect(store)
    conn.execute("UPDATE allocations SET demand = ? WHERE run_id = ?", ('{"vcpus": 2}', "run-broken"))
    conn.commit()
    conn.close()
    return store


def test_unsizable_allocation_is_reported_as_unknown_not_as_zero(tmp_path):
    """One unsized occupier makes the *whole* reservation read unavailable.

    Returning the rows it could size and silently omitting the one it could not
    is the audit's "missing telemetry becomes zero-as-available" failure in the
    dangerous direction: headroom is computed as if the broken claim cost
    nothing.
    """
    store = _store_with_unsizable_row(tmp_path)
    read = read_authority_reservations(store, now=time.time())

    assert read.available is False, "an unsized occupying allocation makes the total unknown"
    assert "run-broken" in read.detail or "cannot be sized" in read.detail
    assert read.unsized == ("run-broken",), "the caller must be able to name what it could not size"
    # The rows it *could* size are still reported: losing them would be a second
    # silent loss of evidence.
    assert [r.run_id for r in read.reservations] == ["run-ok"]


def test_unsized_reservations_refuse_a_slot_count():
    """Slice 1's verdict must fail closed when the reservation set is incomplete."""
    host = host_observation(
        fleet_id="fleet-a",
        resource_id="host:fleet-a",
        now=NOW,
        ttl_s=TTL,
        authority_epoch=EPOCH,
        cpu_total=8,
        cpu_committed=0,
        ram_total_mib=16384,
        ram_committed_mib=2048,
        disk_free_mib=200_000,
        inode_free=1_000_000,
        kvm_usable=True,
        architecture="x86_64",
    )
    demand = HostDemand(vcpus=2, ram_mib=1024, disk_mib=4096, disk_inodes=1)
    verdict = new_vm_slots(
        host,
        demand=demand,
        now=NOW,
        authority_epoch=EPOCH,
        reservations=(),
        reservations_incomplete=True,
    )
    assert verdict.state is State.UNKNOWN
    assert verdict.slots == 0
    assert Reason.RESERVATIONS_UNSIZED in verdict.reasons


def test_a_complete_reservation_set_still_answers_normally():
    """The refusal must be conditional, not blanket."""
    host = host_observation(
        fleet_id="fleet-a",
        resource_id="host:fleet-a",
        now=NOW,
        ttl_s=TTL,
        authority_epoch=EPOCH,
        cpu_total=8,
        cpu_committed=0,
        ram_total_mib=16384,
        ram_committed_mib=2048,
        disk_free_mib=200_000,
        inode_free=1_000_000,
        kvm_usable=True,
        architecture="x86_64",
    )
    demand = HostDemand(vcpus=2, ram_mib=1024, disk_mib=4096, disk_inodes=1)
    verdict = new_vm_slots(
        host, demand=demand, now=NOW, authority_epoch=EPOCH, reservations=(), reservations_incomplete=False
    )
    assert isinstance(verdict, SpawnVerdict)
    assert Reason.RESERVATIONS_UNSIZED not in verdict.reasons
    assert verdict.slots >= 1


# ---------------------------------------------------------------------------
# Finding: argv option confusion through coordinator / place
# ---------------------------------------------------------------------------
class _RecordingRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="")


@pytest.mark.parametrize(
    "coordinator,place",
    [
        ("-x", None),
        ("--help", None),
        ("127.0.0.1:20408", "-p"),
        ("127.0.0.1:20408", "--help"),
        ("127.0.0.1:20408", "-x"),
    ],
)
def test_client_refuses_option_shaped_coordinator_or_place(coordinator, place):
    """A value that starts with '-' would be re-tokenized by labgrid-client.

    The allowlist pins the subcommand position; it does not pin the *values*
    that precede it. A manifest (or a manifest's operator) must not be able to
    reach a different flag through ``-p <value>``.
    """
    runner = _RecordingRunner()
    client = LabgridReadOnlyClient(ProbeLimits(ttl_s=TTL), binary="/usr/bin/labgrid-client", runner=runner)
    with pytest.raises(ObservationRefused):
        client.run(coordinator, "show", place=place)
    assert runner.calls == [], "a refused value must not reach the process boundary"


def test_client_still_accepts_ordinary_values():
    runner = _RecordingRunner()
    client = LabgridReadOnlyClient(ProbeLimits(ttl_s=TTL), binary="/usr/bin/labgrid-client", runner=runner)
    client.run("127.0.0.1:20408", "show", place="router-a1")
    assert runner.calls == [["/usr/bin/labgrid-client", "-x", "127.0.0.1:20408", "-p", "router-a1", "show"]]


# ---------------------------------------------------------------------------
# Finding: the store path must be a URI, not interpolated text
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hostile", ["q?x.sqlite3", "h#x.sqlite3", "p%x.sqlite3", "all?a#b%c.sqlite3"])
def test_a_store_path_with_uri_metacharacters_is_read_not_mistaken_for_absent(tmp_path, hostile):
    """``file:{path}?mode=ro`` truncates at the first '?'.

    Measured before the fix: ``.../a?b#c%d.sqlite3`` opened ``.../a``. A real
    store was then reported as "not found"/"not an authority store" — the
    operator gets the wrong diagnosis, and a second store on disk could be read
    in place of the configured one.
    """
    store = tmp_path / hostile
    conn = sqlite3.connect(store)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('epoch', '7')")
    conn.execute(
        "CREATE TABLE allocations (run_id TEXT PRIMARY KEY, state TEXT, demand TEXT, expires_at REAL)"
    )
    conn.execute(
        "INSERT INTO allocations VALUES ('run-uri', 'reserved', ?, ?)",
        (json.dumps({"vcpus": 2, "ram_mib": 1024, "disk_mib": 4096, "inodes": 1}), time.time() + 600),
    )
    conn.commit()
    conn.close()

    assert read_authority_epoch(store) == 7
    read = read_authority_reservations(store, now=time.time())
    assert read.available is True
    assert [r.run_id for r in read.reservations] == ["run-uri"]
