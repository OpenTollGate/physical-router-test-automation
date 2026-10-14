"""Unit tests for the venue planner (lib.labgrid_venue.plan_venue).

The contract under test: auto mode falls back to the legacy env path ONLY on
unmet host preconditions — a venue that resolves and then fails (unknown or
protected place) is fatal, because falling back to direct while a place may
be held would put two sessions on one DUT (#232 convergence).
"""
from __future__ import annotations

import pytest

from lib.lab_inventory import (
    CoordinatorEntry,
    InventoryError,
    LabInventory,
    RouterEntry,
    SwitchEntry,
)
from lib.labgrid_venue import LabgridBenchError, plan_venue, resolve_venue_mode, venue_preconditions_met


def _inv(**overrides) -> LabInventory:
    routers = {
        "ap-lan2": RouterEntry(name="ap-lan2", poe_port="2", address="192.0.2.9", place="ap-lan2"),
        "x1860-1": RouterEntry(name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1"),
        "x1860-2": RouterEntry(name="x1860-2", poe_port="4", address="192.0.2.11", place="x1860-2"),
        "bare": RouterEntry(name="bare", poe_port="5", address="192.0.2.12"),  # no place
        "guarded": RouterEntry(name="guarded", poe_port="6", address="192.0.2.13", place="guarded", protected=True),
    }
    routers.update(overrides)
    return LabInventory(
        switch=SwitchEntry(host="switch"),
        routers=routers,
        coordinator=CoordinatorEntry(address="coord:20408"),
    )


@pytest.fixture
def host_ready(monkeypatch):
    monkeypatch.setattr("lib.labgrid_venue.inventory_exists", lambda: True)
    monkeypatch.setattr("lib.labgrid_venue._default_client_bin", lambda: "labgrid-client")
    monkeypatch.setattr("lib.labgrid_venue.load_inventory", lambda: _inv())


class TestResolveVenueMode:
    @pytest.mark.parametrize("raw,expected", [
        ("labgrid", "labgrid"),
        ("Labgrid", "labgrid"),
        ("direct", "direct"),
        ("", "auto"),
        ("auto", "auto"),
        ("physical", "legacy"),   # extensive-test.sh lane values
        ("virtual", "legacy"),
        ("skip", "legacy"),
    ])
    def test_table(self, raw, expected):
        assert resolve_venue_mode({"TOLLGATE_VENUE": raw}) == expected

    def test_unset(self):
        assert resolve_venue_mode({}) == "auto"


class TestPreconditions:
    def test_non_physical_lab(self):
        ok, why = venue_preconditions_met({"TOLLGATE_LAB_TYPE": "virtual-lab"})
        assert not ok and "non-physical" in why

    def test_no_inventory(self, monkeypatch):
        monkeypatch.setattr("lib.labgrid_venue.inventory_exists", lambda: False)
        ok, why = venue_preconditions_met({})
        assert not ok and "inventory" in why

    def test_no_client(self, monkeypatch):
        monkeypatch.setattr("lib.labgrid_venue.inventory_exists", lambda: True)
        def boom():
            raise LabgridBenchError("labgrid-client not found")
        monkeypatch.setattr("lib.labgrid_venue._default_client_bin", boom)
        ok, why = venue_preconditions_met({})
        assert not ok and "client" in why

    def test_met(self, monkeypatch):
        monkeypatch.setattr("lib.labgrid_venue.inventory_exists", lambda: True)
        monkeypatch.setattr("lib.labgrid_venue._default_client_bin", lambda: "labgrid-client")
        assert venue_preconditions_met({}) == (True, "")


class TestPlanVenue:
    def test_direct_forces_legacy(self):
        action, binding, detail = plan_venue({"TOLLGATE_VENUE": "direct"})
        assert action == "legacy" and binding is None

    def test_lane_value_keeps_legacy(self):
        action, _, _ = plan_venue({"TOLLGATE_VENUE": "physical"})
        assert action == "legacy"

    def test_auto_unmet_falls_back(self, monkeypatch):
        monkeypatch.setattr("lib.labgrid_venue.inventory_exists", lambda: False)
        action, binding, detail = plan_venue({})
        assert action == "legacy" and "no lab inventory" in detail

    def test_auto_no_mapping_falls_back(self, host_ready):
        action, binding, detail = plan_venue({})
        assert action == "legacy" and "no place maps" in detail

    def test_auto_explicit_place(self, host_ready):
        action, binding, place = plan_venue({"TOLLGATE_LABGRID_PLACE": "x1860-2"})
        assert action == "venue" and place == "x1860-2"
        assert binding.entry.place == "x1860-2"

    def test_auto_address_match(self, host_ready):
        action, binding, place = plan_venue({"TOLLGATE_SSH_HOST": "192.0.2.11"})
        assert action == "venue" and place == "x1860-2"

    def test_auto_address_match_placeless_entry_falls_back(self, host_ready):
        action, _, detail = plan_venue({"TOLLGATE_SSH_HOST": "192.0.2.12"})
        assert action == "legacy" and "no place maps" in detail

    def test_auto_unknown_explicit_place_is_fatal(self, host_ready):
        with pytest.raises(InventoryError):
            plan_venue({"TOLLGATE_LABGRID_PLACE": "no-such-place"})

    def test_auto_protected_match_is_fatal(self, host_ready):
        with pytest.raises(LabgridBenchError, match="protected"):
            plan_venue({"TOLLGATE_SSH_HOST": "192.0.2.13"})

    def test_forced_default_place(self, host_ready):
        action, binding, place = plan_venue({"TOLLGATE_VENUE": "labgrid"})
        assert action == "venue" and place == "ap-lan2"

    def test_forced_unknown_place_is_fatal(self, host_ready):
        with pytest.raises(InventoryError):
            plan_venue({"TOLLGATE_VENUE": "labgrid", "TOLLGATE_LABGRID_PLACE": "nope"})

    def test_forced_missing_inventory_raises(self, monkeypatch):
        def boom():
            raise InventoryError("not found")
        monkeypatch.setattr("lib.labgrid_venue.load_inventory", boom)
        with pytest.raises(InventoryError):
            plan_venue({"TOLLGATE_VENUE": "labgrid"})
