"""``read_authority_pool`` — host headroom without becoming a writer.

The point of this reader is a contradiction the branch already hit once: the
only handle that computes host headroom (``FleetAuthority.host_headroom``)
lives behind ``FleetAuthority.__init__``, which CREATES the store. An
observation command that used it would mint a 64 KB authority database at
whatever path it was pointed at.

So these tests check three things in order of danger:

1. **It never creates a store.** Pointed at an absent path it must leave the
   path absent — the exact regression the epoch reader was written to fix.
2. **Unreadable is not free.** An absent pool, a pool with unpriceable
   occupancy, or a non-store file must all say so instead of reporting zero.
3. **The arithmetic matches the authority.** Given the same rows, the reader and
   ``FleetAuthority.host_headroom()`` must agree, or the CLI would print a
   number the runner disagrees with.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec
from lib.fleet_observe import read_authority_pool


def _store(tmp_path, *, vcpus=8, ram=8192, disk=65536, inodes=100_000):
    path = tmp_path / "authority.sqlite3"
    auth = FleetAuthority(path)
    auth.reconcile()
    auth.register_host(
        HostSpec(
            vcpus_total=vcpus,
            ram_mib_total=ram,
            disk_mib_total=disk,
            inodes_total=inodes,
        )
    )
    return path, auth


def _lane(auth, lane_id="lane-a"):
    auth.register_lane(
        LaneSpec(
            lane_id=lane_id,
            fleet_id="fleet-a",
            resource_id=lane_id,
            vcpus_total=8,
            ram_mib_total=8192,
            disk_mib_total=65536,
            max_concurrent=2,
        )
    )


# --------------------------------------------------------------------------- #
# 1. read-only: the command must not mint what it reads
# --------------------------------------------------------------------------- #
def test_absent_path_is_never_created(tmp_path):
    absent = tmp_path / "does-not-exist.sqlite3"
    read = read_authority_pool(absent)
    assert read.available is False
    assert not absent.exists(), "an observation read created the authority store"


def test_absent_path_reports_absence_not_zero_capacity(tmp_path):
    read = read_authority_pool(tmp_path / "nope.sqlite3")
    assert read.available is False
    assert "not found" in read.detail
    assert read.free == {}, "an unreadable pool must not report free capacity"


def test_no_path_configured_is_refused_explicitly():
    read = read_authority_pool(None)
    assert read.available is False
    assert "no host authority store configured" in read.detail


# --------------------------------------------------------------------------- #
# 2. unreadable is not free
# --------------------------------------------------------------------------- #
def test_unregistered_host_is_reported_not_defaulted(tmp_path):
    path = tmp_path / "authority.sqlite3"
    auth = FleetAuthority(path)
    auth.reconcile()  # store exists, but no host_pool row
    read = read_authority_pool(path)
    assert read.available is False
    assert "host_unknown" in read.detail


def test_a_non_store_file_is_refused(tmp_path):
    junk = tmp_path / "not-a-store.sqlite3"
    junk.write_text("this is not sqlite")
    read = read_authority_pool(junk)
    assert read.available is False


def test_unpriceable_occupancy_refuses_the_whole_read(tmp_path):
    """An occupying allocation with a broken demand must not read as zero."""
    path, auth = _store(tmp_path)
    _lane(auth)
    auth.try_reserve(
        lane_id="lane-a",
        demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1),
        run_id="healthy",
    )
    # Inject a corrupting row the way a partial write would.
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO allocations (run_id, lane_id, token, state, fence, demand,"
            " domains, expires_at, created_at, updated_at, owner_host, cleanup_pending)"
            " VALUES ('broken', 'lane-a', 't', 'active', 1, 'not-json', '[]',"
            " 9e18, 0, 0, 'h/u', 0)"
        )
    conn.close()

    read = read_authority_pool(path)
    assert read.available is False
    assert "could not be priced" in read.detail
    assert read.free == {}, "the readable rows must not be reported as the whole truth"


# --------------------------------------------------------------------------- #
# 3. the arithmetic agrees with the authority
# --------------------------------------------------------------------------- #
def test_pool_arithmetic_matches_the_authority(tmp_path):
    path, auth = _store(tmp_path)
    _lane(auth)
    auth.try_reserve(
        lane_id="lane-a",
        demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1),
        run_id="run-1",
    )

    read = read_authority_pool(path)
    assert read.available is True
    headroom = auth.host_headroom()

    assert read.free["vcpus"] == headroom.free["vcpus"]
    assert read.free["ram_mib"] == headroom.free["ram_mib"]
    assert read.free["disk_mib"] == headroom.free["disk_mib"]
    assert read.free["inodes"] == headroom.free["inodes"]
    assert read.used["vcpus"] == 2
    assert read.occupying_runs == ("run-1",)


def test_a_reservation_reduces_reported_headroom(tmp_path):
    path, auth = _store(tmp_path)
    _lane(auth)

    before = read_authority_pool(path)
    assert before.available is True

    auth.try_reserve(
        lane_id="lane-a",
        demand=Demand(vcpus=3, ram_mib=2048, disk_mib=8192, inodes=1),
        run_id="run-1",
    )
    after = read_authority_pool(path)

    assert after.free["vcpus"] == before.free["vcpus"] - 3
    assert after.free["ram_mib"] == before.free["ram_mib"] - 2048


def test_a_released_reservation_returns_its_capacity(tmp_path):
    path, auth = _store(tmp_path)
    _lane(auth)
    decision = auth.try_reserve(
        lane_id="lane-a",
        demand=Demand(vcpus=2, ram_mib=1024, disk_mib=4096, inodes=1),
        run_id="run-1",
    )
    assert decision.granted
    auth.abort("run-1", decision.allocation.fence)

    read = read_authority_pool(path)
    assert read.available is True
    assert read.used["vcpus"] == 0
    assert read.occupying_runs == ()


def test_ram_reserve_is_subtracted_from_free_only(tmp_path):
    path = tmp_path / "authority.sqlite3"
    auth = FleetAuthority(path)
    auth.reconcile()
    auth.register_host(
        HostSpec(
            vcpus_total=8,
            ram_mib_total=8192,
            ram_mib_reserve=512,
            disk_mib_total=65536,
            inodes_total=100_000,
        )
    )
    read = read_authority_pool(path)
    assert read.available is True
    assert read.ram_reserve_mib == 512
    assert read.total["ram_mib"] == 8192
    # The reserve lowers what is free without pretending the host got smaller.
    assert read.free["ram_mib"] == 8192 - 512
