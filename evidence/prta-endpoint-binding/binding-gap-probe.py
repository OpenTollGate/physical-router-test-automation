#!/usr/bin/env python3
"""Prove the defect this milestone exists to remove, on the code as it is.

Requirement 1 of "Remaining work to enable fleet execution" says a run must
derive ``(coordinator, place)`` from the selected manifest resource and refuse
when the profile/venue resolution targets a different DUT. Before
``lib/fleet_binding`` there is nothing that compares the two, so both of these
silently "succeed":

  A. a fleet manifest whose coordinator is NOT the inventory's coordinator
     (the lane's host budget would cover fleet B's router);
  B. a manifest lane whose place is NOT the place ``plan_venue`` resolves
     (the pytest fixture would acquire a different DUT, or none at all).

No coordinator, no labgrid-client, no place and no inventory FILE are touched:
the inventory is built from the dataclasses and ``plan_venue``'s I/O seams are
patched, exactly like ``tests/unit/test_venue_plan.py``.

Run: python3 evidence/prta-endpoint-binding/binding-gap-probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.fleet_capacity import parse_manifest  # noqa: E402
from lib.lab_inventory import (  # noqa: E402
    CoordinatorEntry,
    LabInventory,
    RouterEntry,
    SwitchEntry,
)

MANIFEST = {
    "schema_version": 1,
    "fleets": [
        {
            "id": "fleet-b",
            "coordinator": {"address": "coord-b.example:20408"},
            "host_controller": {"endpoint": "host-b:8099"},
            "allowed_places": ["x1860-2"],
            "permitted_actions": ["observe", "acquire", "release"],
        }
    ],
}

INVENTORY = LabInventory(
    switch=SwitchEntry(host="switch"),
    routers={
        "x1860-1": RouterEntry(
            name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1"
        ),
    },
    coordinator=CoordinatorEntry(address="coord-a.example:20408"),
)


def main() -> int:
    import lib.labgrid_venue as venue

    venue.inventory_exists = lambda: True
    venue._default_client_bin = lambda: "labgrid-client"
    venue.load_inventory = lambda *a, **k: INVENTORY

    manifest = parse_manifest(MANIFEST, source="<probe>")
    fleet = manifest.fleets["fleet-b"]
    resource = manifest.resource("x1860-2")

    print(f"manifest fleet coordinator : {fleet.coordinator.address}")
    print(f"inventory coordinator      : {INVENTORY.coordinator.address}")
    print(f"manifest lane place        : {resource.resource_id}")

    # The venue resolution the pytest fixture would perform, for a session the
    # runner launched with a profile that names x1860-1.
    plan = venue.plan_venue(
        environ={"TOLLGATE_VENUE": "labgrid", "TOLLGATE_LABGRID_PLACE": "x1860-1"}
    )
    resolved_place = plan.binding.entry.place
    print(f"venue would acquire place  : {resolved_place}")

    print("")
    mismatches = []
    if fleet.coordinator.address != INVENTORY.coordinator.address:
        mismatches.append("coordinator")
    if resource.resource_id != resolved_place:
        mismatches.append("place")
    mode_acquires = plan.action == "venue"

    print(f"acquisition-capable venue  : {mode_acquires}")

    if mismatches:
        print("")
        print(f"DEFECT CONFIRMED: {len(mismatches)} disagreement(s) — "
              f"{', '.join(mismatches)}")
        print("Nothing on the current path compares the manifest lane with the")
        print("venue resolution, so this run would reserve host budget for one")
        print("DUT and drive another. `lib.fleet_binding.check_binding` refuses")
        print("both: coordinator_address_mismatch / place_override_conflict.")
        return 0
    print("")
    print("No disagreement found — the defect is not reproducible here.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
