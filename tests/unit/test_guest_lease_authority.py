"""A guest's lease must come from the authority, keyed by LANE.

Two defects this pins:

1. Ordering — `read_authority_reservations` ran AFTER the guest loop, so
   `spec.lease_free` could never be derived from the store. The dimension was
   structurally unknowable no matter what the store said.
2. Keying — `Reservation` carried only `run_id`, so even a correct read could
   not be attributed to a lane. A lease held on `archy-vm` would have been
   indistinguishable from a lease held on any other lane.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from lib.fleet_observe import read_authority_reservations


def _store(tmp_path, rows):
    path = tmp_path / "authority.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('epoch', '1')")
    conn.execute(
        "CREATE TABLE allocations (run_id TEXT, lane_id TEXT, state TEXT, "
        "demand TEXT, expires_at REAL)"
    )
    conn.executemany("INSERT INTO allocations VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


OCCUPYING = "active"


class TestLaneAttribution:
    def test_a_lease_is_attributed_to_its_lane(self, tmp_path):
        path = _store(
            tmp_path,
            [("run-1", "archy-vm", OCCUPYING, '{"vcpus":2,"ram_mib":512,"disk_mib":64,"inodes":1}', time.time() + 600)],
        )
        read = read_authority_reservations(path, now=time.time())
        assert read.available is True
        assert len(read.reservations) == 1
        assert read.reservations[0].lane_id == "archy-vm"

    def test_another_lanes_lease_is_not_mine(self, tmp_path):
        path = _store(
            tmp_path,
            [("run-1", "comfast", OCCUPYING, '{"vcpus":2,"ram_mib":512,"disk_mib":64,"inodes":1}', time.time() + 600)],
        )
        read = read_authority_reservations(path, now=time.time())
        assert [r.lane_id for r in read.reservations] == ["comfast"]

    def test_lane_id_is_preserved_not_blanked(self, tmp_path):
        """A missing lane_id must never read as "this lane" — the dangerous direction."""
        path = _store(
            tmp_path,
            [("run-1", "", OCCUPYING, '{"vcpus":2,"ram_mib":512,"disk_mib":64,"inodes":1}', time.time() + 600)],
        )
        read = read_authority_reservations(path, now=time.time())
        assert read.reservations[0].lane_id == ""
        assert read.reservations[0].lane_id != "archy-vm"


class TestLeaseFreeDerivation:
    def test_lane_with_no_claim_is_free_when_store_is_complete(self, tmp_path):
        path = _store(tmp_path, [])
        read = read_authority_reservations(path, now=time.time())
        assert read.available is True
        assert read.reservations == ()

    def test_unavailable_store_yields_no_verdict(self, tmp_path):
        """An unreadable store must leave the lease UNKNOWN, never "free"."""
        read = read_authority_reservations(tmp_path / "absent.sqlite3", now=time.time())
        assert read.available is False
