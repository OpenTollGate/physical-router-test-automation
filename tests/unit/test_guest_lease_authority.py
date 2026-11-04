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
        assert read.available is False, "unattributed occupancy must not prove other lanes free"


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


@pytest.mark.parametrize("held, expected, registered, quarantined", [
    (True, False, True, False), (False, None, False, False),
    (False, True, True, False), (False, False, True, True),
    (False, None, "wrong-fleet", False),
    (False, None, "duplicate", False),
    (False, None, "missing-store", False),
])
def test_collect_explicit_free_cannot_override_authority(tmp_path, monkeypatch, held, expected, registered, quarantined):
    from test_guest_telemetry_real import _manifest, PLACE, NOW, EPOCH
    from test_fleet_observe import _client, FakeRunner, _local_host_kwargs
    from lib.fleet_observe import collect, GuestSpec
    from lib.guest_readiness import GuestProbe, ProbeResult

    def readiness(**kwargs):
        return GuestProbe(
            health=ProbeResult(True, "fixture"),
            topology=ProbeResult(True, "fixture"),
            client_reachable=ProbeResult(True, "fixture"),
            lease_free=ProbeResult(kwargs["lease_free"], "authority"),
        )

    monkeypatch.setattr("lib.guest_readiness.probe_guest_readiness", readiness)
    rows = []
    if held:
        rows.append((
            "run-held", "authority-lane-7", OCCUPYING,
            '{"vcpus":2,"ram_mib":512,"disk_mib":64,"inodes":1}', NOW + 600,
        ))
    path = _store(tmp_path, rows)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE lanes (lane_id TEXT, fleet_id TEXT, resource_id TEXT)")
        conn.execute("CREATE TABLE quarantine (lane_id TEXT)")
        if quarantined:
            conn.execute("INSERT INTO quarantine VALUES ('authority-lane-7')")
        if registered:
            conn.execute("INSERT INTO lanes VALUES (?, ?, ?)",
                         ("authority-lane-7", "other-fleet" if registered == "wrong-fleet" else "x280-bench", PLACE))
        if registered == "duplicate":
            conn.execute("INSERT INTO lanes VALUES (?, ?, ?)",
                         ("second-lane", "x280-bench", PLACE))
    if registered == "missing-store":
        path = tmp_path / "absent.sqlite3"
    statements = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr("lib.fleet_observe.sqlite3.connect", traced_connect)
    evidence = collect(
        _manifest(), now=NOW, authority_epoch=EPOCH,
        authority_path=path, client=_client(FakeRunner()),
        guests=[GuestSpec(resource_id=PLACE, pidfile=tmp_path / "missing.pid",
                          address="fixture.invalid", lease_free=True)],
        **_local_host_kwargs(tmp_path),
    )
    assert evidence.guests[PLACE].lease_free is expected
    if registered == "missing-store":
        assert not path.exists(), "observer must not create an authority store"
    else:
        assert "BEGIN" in statements, "lease evidence must use an explicit read snapshot"


def test_concurrent_quarantine_is_seen_only_by_next_snapshot(tmp_path, monkeypatch):
    from test_guest_telemetry_real import _manifest, PLACE, NOW, EPOCH
    from test_fleet_observe import _client, FakeRunner, _local_host_kwargs
    from lib.fleet_observe import collect, GuestSpec
    from lib.guest_readiness import GuestProbe, ProbeResult

    path = _store(tmp_path, [])
    real_connect = sqlite3.connect
    with real_connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE lanes (lane_id TEXT, fleet_id TEXT, resource_id TEXT)")
        db.execute("CREATE TABLE quarantine (lane_id TEXT)")
        db.execute("INSERT INTO lanes VALUES (?, ?, ?)", ("lane-7", "x280-bench", PLACE))
    writes = []

    def interleave(statement):
        if statement == "SELECT lane_id, fleet_id, resource_id FROM lanes" and not writes:
            with real_connect(path) as writer:
                writer.execute("INSERT INTO quarantine VALUES ('lane-7')")
            writes.append("committed")

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(interleave)
        return connection

    def readiness(**kwargs):
        yes = ProbeResult(True, "fixture")
        return GuestProbe(health=yes, topology=yes, client_reachable=yes,
                          lease_free=ProbeResult(kwargs["lease_free"], "snapshot"))

    monkeypatch.setattr("lib.fleet_observe.sqlite3.connect", traced_connect)
    monkeypatch.setattr("lib.guest_readiness.probe_guest_readiness", readiness)
    kwargs = _local_host_kwargs(tmp_path)

    def observe():
        return collect(
            _manifest(), now=NOW, authority_epoch=EPOCH, authority_path=path,
            client=_client(FakeRunner()),
            guests=[GuestSpec(resource_id=PLACE, pidfile=tmp_path / "missing.pid",
                              address="fixture.invalid")], **kwargs,
        ).guests[PLACE].lease_free

    assert observe() is True
    assert writes == ["committed"], "a separate connection must commit during the read"
    assert observe() is False
