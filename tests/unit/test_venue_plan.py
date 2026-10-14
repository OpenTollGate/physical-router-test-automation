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
from lib.labgrid_venue import (
    LabgridBenchError,
    apply_venue_env,
    plan_venue,
    resolve_venue_mode,
    venue_preconditions_met,
)


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
        plan = plan_venue({"TOLLGATE_VENUE": "direct"})
        assert plan.action == "legacy" and plan.binding is None

    def test_lane_value_keeps_legacy(self):
        assert plan_venue({"TOLLGATE_VENUE": "physical"}).action == "legacy"

    def test_auto_unmet_falls_back(self, monkeypatch):
        monkeypatch.setattr("lib.labgrid_venue.inventory_exists", lambda: False)
        plan = plan_venue({})
        assert plan.action == "legacy" and "no lab inventory" in plan.detail

    def test_auto_no_mapping_falls_back(self, host_ready):
        plan = plan_venue({})
        assert plan.action == "legacy" and "no place maps" in plan.detail

    def test_auto_explicit_place(self, host_ready):
        plan = plan_venue({"TOLLGATE_LABGRID_PLACE": "x1860-2"})
        assert plan.action == "venue" and plan.origin == "explicit-place"
        assert plan.binding.entry.place == "x1860-2"

    def test_auto_address_match(self, host_ready):
        plan = plan_venue({"TOLLGATE_SSH_HOST": "192.0.2.11"})
        assert plan.action == "venue" and plan.origin == "address-match"
        assert plan.detail == "x1860-2"

    def test_auto_address_match_placeless_entry_falls_back(self, host_ready):
        plan = plan_venue({"TOLLGATE_SSH_HOST": "192.0.2.12"})
        assert plan.action == "legacy" and "no place maps" in plan.detail

    def test_auto_unknown_explicit_place_is_fatal(self, host_ready):
        with pytest.raises(InventoryError):
            plan_venue({"TOLLGATE_LABGRID_PLACE": "no-such-place"})

    def test_auto_protected_match_is_fatal(self, host_ready):
        with pytest.raises(LabgridBenchError, match="protected"):
            plan_venue({"TOLLGATE_SSH_HOST": "192.0.2.13"})

    def test_forced_default_place(self, host_ready):
        plan = plan_venue({"TOLLGATE_VENUE": "labgrid"})
        assert plan.action == "venue" and plan.origin == "forced" and plan.detail == "ap-lan2"

    def test_forced_unknown_place_is_fatal(self, host_ready):
        with pytest.raises(InventoryError):
            plan_venue({"TOLLGATE_VENUE": "labgrid", "TOLLGATE_LABGRID_PLACE": "nope"})

    def test_forced_missing_inventory_raises(self, monkeypatch):
        def boom():
            raise InventoryError("not found")
        monkeypatch.setattr("lib.labgrid_venue.load_inventory", boom)
        with pytest.raises(InventoryError):
            plan_venue({"TOLLGATE_VENUE": "labgrid"})


class TestApplyVenueEnvPrecedence:
    ENTRY = RouterEntry(
        name="x1860-1", poe_port="3", address="192.0.2.10",
        place="x1860-1", keyfile="~/.ssh/dossier-key", jump_host="jump.example",
    )

    def test_default_venue_is_authoritative(self):
        env: dict[str, str] = {
            "TOLLGATE_SSH_HOST": "elsewhere", "TOLLGATE_SSH_KEY": "operator-key",
        }
        apply_venue_env(self.ENTRY, env)
        assert env["TOLLGATE_SSH_HOST"] == "192.0.2.10"
        assert env["TOLLGATE_SSH_KEY"].endswith("dossier-key")
        assert env["TOLLGATE_SSH_JUMP_HOST"] == "jump.example"

    def test_address_match_respects_operator_env(self):
        env: dict[str, str] = {
            "TOLLGATE_SSH_HOST": "192.0.2.10", "TOLLGATE_SSH_KEY": "operator-key",
            "TOLLGATE_SSH_JUMP_HOST": "operator-jump",
        }
        apply_venue_env(self.ENTRY, env, respect_existing=True)
        assert env["TOLLGATE_SSH_KEY"] == "operator-key"
        assert env["TOLLGATE_SSH_JUMP_HOST"] == "operator-jump"

    def test_address_match_still_fills_gaps(self):
        env: dict[str, str] = {"TOLLGATE_SSH_HOST": "192.0.2.10"}
        apply_venue_env(self.ENTRY, env, respect_existing=True)
        assert env["TOLLGATE_SSH_KEY"].endswith("dossier-key")
