"""Requirement 1 (manifest lane → profile endpoint binding) and the non-overlap
half of requirement 2, as pure logic.

Everything here is injected data: a real ``FleetManifest`` built through the
real parser, hand-built ``LabInventory`` objects (the dataclasses, not the
loader), and plain env mappings. No coordinator, no ``labgrid-client``, no
inventory file, no place acquisition — the place belongs to
``tests/conftest.py`` and these tests assert that this layer never touches it.

Two kinds of test:

* **refusals** — each rule in ``lib/fleet_binding`` has at least one test
  asserting the exact machine-readable code, because a code is a contract;
* **the mirror** — ``venue_intent`` must predict what ``plan_venue`` actually
  does. That is pinned against the real ``plan_venue`` (with the inventory
  loader monkeypatched, per ``tests/unit/test_venue_plan.py``), so the
  prediction cannot silently drift from the fixture it is predicting.
"""

from __future__ import annotations

import pytest

from lib.fleet_binding import (
    CODES,
    DEFAULT_COORDINATOR_PORT,
    Binding,
    BindingRefused,
    PlaceClaim,
    Refusal,
    check_binding,
    find_overlaps,
    module_io_audit,
    normalize_coordinator,
    place_candidates,
    venue_intent,
)
from lib.fleet_capacity import parse_manifest
from lib.lab_inventory import (
    CoordinatorEntry,
    LabInventory,
    RouterEntry,
    SwitchEntry,
)

COORD = "coord.example:20408"


# ---------------------------------------------------------------------------
# Fixtures: a manifest and an inventory that DO agree
# ---------------------------------------------------------------------------
def _manifest_doc(fleets=None):
    return {
        "schema_version": 1,
        "fleets": fleets
        if fleets is not None
        else [
            {
                "id": "fleet-a",
                "coordinator": {"address": COORD},
                "host_controller": {"endpoint": "host-a:8099"},
                "allowed_places": [{"id": "x1860-1"}, {"id": "x1860-2"}],
                "permitted_actions": ["observe", "acquire", "release"],
                "capacity_limits": {"max_concurrent_runs": 1},
            }
        ],
    }


def _manifest(**kwargs):
    return parse_manifest(_manifest_doc(**kwargs), source="<test>")


def _two_fleet_manifest():
    """Two fleets, one place each — the manifest parser refuses shared ids."""
    return parse_manifest(
        {
            "schema_version": 1,
            "fleets": [
                {
                    "id": "fleet-a",
                    "coordinator": {"address": COORD},
                    "host_controller": {"endpoint": "host-a:8099"},
                    "allowed_places": ["x1860-1"],
                    "permitted_actions": ["observe", "acquire", "release"],
                },
                {
                    "id": "fleet-b",
                    "coordinator": {"address": COORD},
                    "host_controller": {"endpoint": "host-b:8099"},
                    "allowed_places": ["x1860-2"],
                    "permitted_actions": ["observe", "acquire", "release"],
                },
            ],
        },
        source="<test>",
    )


def _inventory(
    routers=None,
    coordinator=COORD,
    protected_ports=frozenset(),
) -> LabInventory:
    entries = routers if routers is not None else {
        "x1860-1": RouterEntry(
            name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1"
        ),
        "x1860-2": RouterEntry(
            name="x1860-2", poe_port="4", address="192.0.2.11", place="x1860-2"
        ),
    }
    return LabInventory(
        switch=SwitchEntry(host="switch"),
        routers=entries,
        coordinator=CoordinatorEntry(address=coordinator),
        switch_protected_ports=frozenset(protected_ports),
    )


def _env(**overrides) -> dict[str, str]:
    """Env that makes the fixture take place x1860-1 explicitly.

    A ``None`` value REMOVES the key, so a test can ask for "mode direct, no
    place override" without a leftover explicit place defeating the point.
    """
    env = {"TOLLGATE_VENUE": "labgrid", "TOLLGATE_LABGRID_PLACE": "x1860-1"}
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _check(**overrides) -> Binding:
    kwargs = dict(
        manifest=_manifest(),
        inventory=_inventory(),
        environ=_env(),
        fleet_id="fleet-a",
        resource_id="x1860-1",
        lane_id="lane-x1860-1",
        owner="run-42",
        profile_endpoint="192.0.2.10",
    )
    kwargs.update(overrides)
    return check_binding(**kwargs)


def _refusal(code, **overrides) -> BindingRefused:
    with pytest.raises(BindingRefused) as excinfo:
        _check(**overrides)
    assert excinfo.value.code == code.value, (
        f"expected {code.value}, got {excinfo.value.code} ({excinfo.value.detail})"
    )
    return excinfo.value


# ---------------------------------------------------------------------------
# Happy path — the agreement the requirement is about
# ---------------------------------------------------------------------------
class TestAgreement:
    def test_agreeing_manifest_and_venue_bind_one_dut(self):
        binding = _check()
        assert isinstance(binding, Binding)
        assert binding.fleet_id == "fleet-a"
        assert binding.resource_id == "x1860-1"
        assert binding.place == "x1860-1"
        assert binding.dut_address == "192.0.2.10"
        assert binding.coordinator == COORD
        assert binding.owner == "run-42"
        assert binding.origin == "explicit-place"

    def test_binding_env_forces_the_fixture_onto_the_lane_place(self):
        env = _check().as_env()
        assert env["TOLLGATE_VENUE"] == "labgrid"
        assert env["TOLLGATE_LABGRID_PLACE"] == "x1860-1"
        # Forcing the mode is the point: a lane must not fall back to the
        # legacy direct path underneath a host-budget reservation.
        assert "TOLLGATE_SSH_HOST" in env

    def test_auto_venue_resolved_by_address_also_binds(self):
        # No explicit place: the operator host names the dossier, like conftest
        # does after a real session start.
        env = {"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "192.0.2.10"}
        binding = _check(environ=env, profile_endpoint="")
        assert binding.place == "x1860-1"
        assert binding.origin == "address-match"

    def test_operator_host_resolves_the_venue_in_auto_mode(self):
        binding = _check(
            environ={"TOLLGATE_VENUE": "auto"},
            operator_host="192.0.2.10",
            profile_endpoint="",
        )
        assert binding.place == "x1860-1"
        assert binding.origin == "address-match"

    def test_operator_host_is_case_for_case(self):
        """A near-miss operator host must not silently resolve to a place."""
        _refusal(
            Refusal.VENUE_TAKES_NO_PLACE,
            environ={"TOLLGATE_VENUE": "auto"},
            operator_host="192.0.2.10 ",  # trailing space: not the inventory address
            profile_endpoint="",
        )

    def test_lane_inferred_when_fleet_declares_one_place(self):
        manifest = _manifest(
            fleets=[
                {
                    "id": "solo",
                    "coordinator": {"address": COORD},
                    "host_controller": {"endpoint": "host-a:8099"},
                    "allowed_places": ["x1860-1"],
                    "permitted_actions": ["observe", "acquire", "release"],
                }
            ]
        )
        binding = check_binding(
            manifest=manifest,
            inventory=_inventory(),
            environ=_env(),
            owner="run-42",
        )
        assert binding.fleet_id == "solo"
        assert binding.resource_id == "x1860-1"
        assert binding.lane_id == "x1860-1"

    def test_profile_endpoint_may_be_absent(self):
        # An unset profile endpoint is not a mismatch; the venue decides.
        assert _check(profile_endpoint="").dut_address == "192.0.2.10"


# ---------------------------------------------------------------------------
# (a) coordinator agreement
# ---------------------------------------------------------------------------
class TestCoordinatorAgreement:
    def test_mismatched_coordinator_is_refused(self):
        err = _refusal(Refusal.COORDINATOR_ADDRESS_MISMATCH, inventory=_inventory(coordinator="other:20408"))
        assert "other:20408" in err.detail

    def test_missing_port_on_one_side_normalises_to_labgrid_default(self):
        manifest = _manifest(
            fleets=[
                {
                    "id": "fleet-a",
                    "coordinator": {"address": f"coord.example:{DEFAULT_COORDINATOR_PORT}"},
                    "host_controller": {"endpoint": "host-a:8099"},
                    "allowed_places": ["x1860-1"],
                    "permitted_actions": ["observe", "acquire", "release"],
                }
            ]
        )
        # Inventory side without a port: same coordinator, different spelling.
        binding = check_binding(
            manifest=manifest,
            inventory=_inventory(coordinator="coord.example"),
            environ=_env(),
            fleet_id="fleet-a",
            owner="run-42",
        )
        assert binding.coordinator == f"coord.example:{DEFAULT_COORDINATOR_PORT}"

    def test_case_and_trailing_dot_normalise(self):
        binding = _check(inventory=_inventory(coordinator="COORD.Example.:20408"))
        assert binding.coordinator == COORD

    def test_different_port_is_a_different_coordinator(self):
        _refusal(Refusal.COORDINATOR_ADDRESS_MISMATCH, inventory=_inventory(coordinator="coord.example:20409"))

    def test_empty_inventory_coordinator_is_refused_not_assumed(self):
        _refusal(Refusal.COORDINATOR_ADDRESS_MISSING, inventory=_inventory(coordinator=""))

    def test_missing_manifest_coordinator_is_refused(self):
        manifest = _manifest(
            fleets=[
                {
                    "id": "fleet-a",
                    "coordinator": {"address": COORD},
                    "host_controller": {"endpoint": "host-a:8099"},
                    "allowed_places": ["x1860-1"],
                    "permitted_actions": ["observe", "acquire", "release"],
                }
            ]
        )
        object.__setattr__(manifest.fleets["fleet-a"].coordinator, "address", "")
        _refusal(Refusal.COORDINATOR_ADDRESS_MISSING, manifest=manifest)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("coord:20408", "coord:20408"),
            ("coord", f"coord:{DEFAULT_COORDINATOR_PORT}"),
            ("COORD:20408", "coord:20408"),
            ("coord.:20408", "coord:20408"),
            ("[::1]:20408", "[::1]:20408"),
            ("::1", "[::1]:20408"),
            ("coord:020408", "coord:20408"),
        ],
    )
    def test_normalisation_table(self, raw, expected):
        assert normalize_coordinator(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", ":20408", "coord:notaport", "coord:0", "coord:70000", "[::1"])
    def test_unnormalisable_addresses_raise(self, raw):
        with pytest.raises(ValueError):
            normalize_coordinator(raw)


# ---------------------------------------------------------------------------
# (b) place agreement, including the env override
# ---------------------------------------------------------------------------
class TestPlaceAgreement:
    def test_manifest_place_and_venue_place_must_match(self):
        # The venue resolves x1860-1 by ADDRESS while the lane is x1860-2:
        # no explicit override, so this is a plain disagreement.
        env = {"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "192.0.2.10"}
        err = _refusal(
            Refusal.PLACE_MISMATCH,
            resource_id="x1860-2",
            lane_id="lane-2",
            environ=env,
            profile_endpoint="",
        )
        assert "x1860-1" in err.detail and "x1860-2" in err.detail

    def test_env_override_that_disagrees_is_refused_not_accepted(self):
        # The env says x1860-2 while the lane is x1860-1: an operator env var
        # may never retarget a reserved lane.
        err = _refusal(
            Refusal.PLACE_OVERRIDE_CONFLICT,
            environ=_env(TOLLGATE_LABGRID_PLACE="x1860-2"),
        )
        assert "TOLLGATE_LABGRID_PLACE" in err.detail

    def test_env_override_that_agrees_is_accepted(self):
        binding = _check(environ=_env(TOLLGATE_LABGRID_PLACE="x1860-1"))
        assert binding.origin == "explicit-place"

    def test_override_naming_an_unknown_place_is_refused(self):
        _refusal(
            Refusal.VENUE_PLACE_UNRESOLVABLE,
            environ=_env(TOLLGATE_LABGRID_PLACE="does-not-exist"),
        )

    def test_dossier_mapping_the_lane_place_elsewhere_is_refused(self):
        # The lane names x1860-1; the dossier the venue would reach by address
        # claims a DIFFERENT place, so the two names are not the same DUT.
        routers = {
            "x1860-1": RouterEntry(
                name="x1860-1", poe_port="3", address="192.0.2.10", place="somewhere-else"
            ),
        }
        env = {"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "192.0.2.10"}
        _refusal(
            Refusal.PLACE_MISMATCH,
            inventory=_inventory(routers=routers),
            environ=env,
            profile_endpoint="",
        )

    def test_dossier_without_a_place_cannot_be_a_lane(self):
        routers = {
            "x1860-1": RouterEntry(name="x1860-1", poe_port="3", address="192.0.2.10"),
        }
        _refusal(Refusal.VENUE_PLACE_UNRESOLVABLE, inventory=_inventory(routers=routers))


# ---------------------------------------------------------------------------
# (c) venue modes that never acquire a place
# ---------------------------------------------------------------------------
class TestModesThatAcquireNothing:
    @pytest.mark.parametrize(
        "env,label",
        [
            ({"TOLLGATE_VENUE": "direct"}, "direct"),
            ({"TOLLGATE_VENUE": "physical"}, "legacy"),
            ({"TOLLGATE_VENUE": "virtual"}, "legacy"),
            ({}, "auto-unusable"),
        ],
    )
    def test_place_less_modes_are_refused_under_a_manifest(self, env, label):
        override = dict(env)
        override["TOLLGATE_LABGRID_PLACE"] = None  # nothing may sneak a place in
        if label == "auto-unusable":
            _refusal(
                Refusal.VENUE_TAKES_NO_PLACE,
                environ={},
                preconditions_met=False,
            )
        else:
            _refusal(Refusal.VENUE_TAKES_NO_PLACE, environ=override)

    def test_xdist_worker_is_refused_even_with_an_explicit_place(self):
        _refusal(Refusal.VENUE_TAKES_NO_PLACE, xdist_worker=True)

    def test_unit_only_session_auto_skip_is_refused_under_a_manifest(self):
        # conftest would log "auto skipped — unit-only/parity-only session" and
        # own no place, while the runner reserved host budget for a DUT.
        err = _refusal(
            Refusal.FIXTURE_WOULD_SKIP_PLACE,
            environ={"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "192.0.2.10"},
            session_scope="unit",
            profile_endpoint="",
        )
        assert "unit-only" in err.detail

    def test_parity_only_session_auto_skip_is_refused_under_a_manifest(self):
        _refusal(
            Refusal.FIXTURE_WOULD_SKIP_PLACE,
            environ={"TOLLGATE_VENUE": "auto"},
            session_scope="parity",
            profile_endpoint="",
        )

    def test_unit_only_session_with_forced_venue_still_binds(self):
        # conftest's comment: forced labgrid still acquires for unit-only
        # sessions (that pairing doubles as the cheap smoke). So this binds.
        binding = _check(environ=_env(), session_scope="unit")
        assert binding.place == "x1860-1"

    def test_auto_mode_with_no_place_for_the_target_is_refused(self):
        env = {"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "203.0.113.9"}
        _refusal(
            Refusal.VENUE_TAKES_NO_PLACE,
            environ=env,
            operator_host="203.0.113.9",
            profile_endpoint="",
        )


    def test_venue_mode_needing_a_place_without_an_inventory_is_refused(self):
        with pytest.raises(BindingRefused) as excinfo:
            venue_intent(environ=_env(), inventory=None)
        assert excinfo.value.code == Refusal.VENUE_PLACE_UNRESOLVABLE.value

    def test_auto_mode_without_an_inventory_is_refused(self):
        with pytest.raises(BindingRefused) as excinfo:
            venue_intent(environ={"TOLLGATE_VENUE": "auto"}, inventory=None)
        assert excinfo.value.code == Refusal.VENUE_PLACE_UNRESOLVABLE.value

    def test_legacy_mode_needs_no_inventory(self):
        intent = venue_intent(environ={"TOLLGATE_VENUE": "direct"}, inventory=None)
        assert not intent.owns_place

    def test_bad_inventory_coordinator_port_is_a_missing_address_refusal(self):
        _refusal(
            Refusal.COORDINATOR_ADDRESS_MISSING,
            inventory=_inventory(coordinator="coord.example:notaport"),
        )


# ---------------------------------------------------------------------------
# (d) protected places
# ---------------------------------------------------------------------------
class TestProtected:
    def test_protected_dossier_is_refused(self):
        routers = {
            "x1860-1": RouterEntry(
                name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1", protected=True
            ),
            "x1860-2": RouterEntry(name="x1860-2", poe_port="4", address="192.0.2.11", place="x1860-2"),
        }
        err = _refusal(Refusal.RESOURCE_PROTECTED, inventory=_inventory(routers=routers))
        assert "x1860-1" in err.detail

    def test_protected_candidate_carries_the_flag(self):
        routers = {
            "x1860-1": RouterEntry(
                name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1", protected=True
            )
        }
        candidates = place_candidates(_inventory(routers=routers))
        assert candidates["x1860-1"][0].protected is True

    def test_place_without_an_address_is_refused(self):
        routers = {
            "x1860-1": RouterEntry(name="x1860-1", poe_port="3", place="x1860-1"),
        }
        _refusal(Refusal.VENUE_ADDRESS_MISSING, inventory=_inventory(routers=routers))

    def test_protected_is_refused_before_the_place_agreement(self):
        """A protected place is its own code even when the lane also mismatches.

        Precedence is deliberate: the operator must not be told "wrong place"
        when the real answer is "that DUT may never be acquired".
        """
        routers = {
            "x1860-2": RouterEntry(
                name="x1860-2", poe_port="4", address="192.0.2.11", place="x1860-2", protected=True
            ),
            "x1860-1": RouterEntry(name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1"),
        }
        _refusal(
            Refusal.RESOURCE_PROTECTED,
            inventory=_inventory(routers=routers),
            resource_id="x1860-1",
            environ=_env(TOLLGATE_LABGRID_PLACE="x1860-2"),
        )


# ---------------------------------------------------------------------------
# (e) ambiguity
# ---------------------------------------------------------------------------
class TestAmbiguity:
    def test_multiple_fleets_without_a_named_fleet_is_refused(self):
        _refusal(Refusal.FLEET_AMBIGUOUS, manifest=_two_fleet_manifest(), fleet_id=None)

    def test_named_fleet_selects_exactly_one(self):
        binding = _check(manifest=_two_fleet_manifest(), fleet_id="fleet-a", resource_id="x1860-1")
        assert binding.fleet_id == "fleet-a"

    def test_unknown_fleet_is_refused(self):
        _refusal(Refusal.FLEET_UNKNOWN, fleet_id="fleet-zzz")

    def test_multiple_places_without_a_named_resource_is_refused(self):
        _refusal(Refusal.LANE_AMBIGUOUS, resource_id=None, lane_id=None)

    def test_place_claimed_by_two_dossiers_is_refused(self):
        routers = {
            "x1860-1": RouterEntry(name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1"),
            "x1860-1-alias": RouterEntry(
                name="x1860-1-alias", poe_port="9", address="192.0.2.99", place="x1860-1"
            ),
        }
        _refusal(Refusal.PLACE_AMBIGUOUS, inventory=_inventory(routers=routers))

    def test_place_name_claimed_by_two_dossiers_is_refused(self):
        """Resolution by PLACE NAME (not dossier key) must refuse ambiguity too."""
        routers = {
            "a": RouterEntry(name="a", poe_port="3", address="192.0.2.10", place="shared"),
            "b": RouterEntry(name="b", poe_port="4", address="192.0.2.11", place="shared"),
        }
        with pytest.raises(BindingRefused) as excinfo:
            venue_intent(
                environ=_env(TOLLGATE_LABGRID_PLACE="shared"),
                inventory=_inventory(routers=routers),
            )
        assert excinfo.value.code == Refusal.PLACE_AMBIGUOUS.value

    def test_place_name_with_one_dossier_resolves(self):
        """Resolution by place NAME when no dossier key matches it."""
        routers = {
            "x1860-1-dossier": RouterEntry(
                name="x1860-1-dossier", poe_port="3", address="192.0.2.10", place="x1860-1"
            ),
        }
        intent = venue_intent(
            environ=_env(TOLLGATE_LABGRID_PLACE="x1860-1"),
            inventory=_inventory(routers=routers),
        )
        assert intent.candidate.router_name == "x1860-1-dossier"
        assert intent.place == "x1860-1"

    def test_dossier_without_a_place_skipped_when_indexing(self):
        routers = {
            "bare": RouterEntry(name="bare", poe_port="5", address="192.0.2.12"),
            "x1860-1": RouterEntry(name="x1860-1", poe_port="3", address="192.0.2.10", place="x1860-1"),
        }
        assert set(place_candidates(_inventory(routers=routers))) == {"x1860-1"}

    def test_two_candidates_indexed_by_place(self):
        routers = {
            "a": RouterEntry(name="a", poe_port="3", address="192.0.2.10", place="shared"),
            "b": RouterEntry(name="b", poe_port="4", address="192.0.2.11", place="shared"),
        }
        candidates = place_candidates(_inventory(routers=routers))
        assert len(candidates["shared"]) == 2


# ---------------------------------------------------------------------------
# Resource identity refusals
# ---------------------------------------------------------------------------
class TestResourceIdentity:
    def test_resource_owned_by_another_fleet_is_refused(self):
        _refusal(
            Refusal.RESOURCE_NOT_AUTHORIZED,
            manifest=_two_fleet_manifest(),
            fleet_id="fleet-a",
            resource_id="x1860-2",
        )

    def test_manifest_without_the_named_resource_is_refused(self):
        _refusal(Refusal.RESOURCE_NOT_AUTHORIZED, resource_id="x1860-99", lane_id="lane-99")

    def test_guest_resource_cannot_be_bound_to_a_place(self):
        manifest = _manifest(
            fleets=[
                {
                    "id": "fleet-a",
                    "coordinator": {"address": COORD},
                    "host_controller": {"endpoint": "host-a:8099"},
                    "allowed_places": [{"id": "lane-qemu-1", "kind": "guest"}],
                    "permitted_actions": ["observe", "acquire", "release"],
                }
            ]
        )
        _refusal(
            Refusal.RESOURCE_KIND_UNSUPPORTED,
            manifest=manifest,
            resource_id="lane-qemu-1",
        )

    def test_missing_manifest_is_refused(self):
        _refusal(Refusal.MANIFEST_MISSING, manifest=None)

    @pytest.mark.parametrize("owner", ["", "   "])
    def test_missing_owner_identity_is_refused(self, owner):
        _refusal(Refusal.OWNER_MISSING, owner=owner)


# ---------------------------------------------------------------------------
# Profile endpoint (requirement 1's env half)
# ---------------------------------------------------------------------------
class TestProfileEndpoint:
    def test_profile_endpoint_naming_another_place_is_refused(self):
        err = _refusal(Refusal.PROFILE_ENDPOINT_MISMATCH, profile_endpoint="192.0.2.11")
        assert "x1860-2" in err.detail

    def test_profile_endpoint_naming_no_dossier_is_refused(self):
        _refusal(Refusal.PROFILE_ENDPOINT_UNKNOWN, profile_endpoint="203.0.113.77")

    def test_profile_endpoint_matching_the_lane_place_is_accepted(self):
        assert _check(profile_endpoint="192.0.2.10").dut_address == "192.0.2.10"


# ---------------------------------------------------------------------------
# Non-overlap: this layer acquires nothing, and overlap is detectable
# ---------------------------------------------------------------------------
class TestNonOverlap:
    def test_layering_audit_finds_no_io_or_acquisition(self):
        audit = module_io_audit()
        assert audit["modules"] == (), f"unexpected imports: {audit['modules']}"
        assert audit["names"] == (), f"unexpected imported symbols: {audit['names']}"
        assert audit["calls"] == (), f"acquisition/I-O calls: {audit['calls']}"

    def test_the_only_project_import_is_the_pure_mode_resolver(self):
        from lib import fleet_binding
        from lib.fleet_binding import PERMITTED_MODULES, PERMITTED_NAMES

        assert PERMITTED_MODULES == frozenset(
            {
                "__future__",
                "ast",
                "collections",
                "collections.abc",
                "dataclasses",
                "enum",
                "pathlib",
                "typing",
                "lib",
                "lib.labgrid_venue",
            }
        )
        assert PERMITTED_NAMES == frozenset({"resolve_venue_mode"})
        # Executable lines only: the docstring names the lifecycle to explain
        # why it must not appear, so a raw substring scan would be wrong.
        import ast

        tree = ast.parse(open(fleet_binding.__file__, encoding="utf-8").read())
        code_names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        for forbidden in ("venue_mutex", "bench_for_router", "LabgridBench", "load_inventory", "hardware_lock"):
            assert forbidden not in code_names, f"{forbidden} is referenced by code in the binding layer"

    def test_check_binding_never_calls_the_place_lifecycle(self, monkeypatch):
        """Resolution must not acquire: patch every acquisition entry point to explode."""
        import lib.labgrid_venue as venue

        def boom(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("binding layer touched the place lifecycle")

        monkeypatch.setattr(venue.LabgridBench, "acquire", boom)
        monkeypatch.setattr(venue.LabgridBench, "release", boom)
        monkeypatch.setattr(venue, "venue_mutex", boom)
        monkeypatch.setattr(venue, "bench_for_router", boom)

        binding = _check()
        assert binding.place == "x1860-1"

    def test_binding_layer_reports_a_claim_a_caller_can_record(self):
        binding = _check()
        assert binding.claim == PlaceClaim(place="x1860-1", owner="run-42")

    def test_a_second_resolution_for_the_same_place_from_another_run_overlaps(self):
        """Two runs, same place: the second is detectable BEFORE acquiring."""
        seen: list[PlaceClaim] = []
        first = _check(owner="run-1")
        seen.append(first.claim)

        with pytest.raises(BindingRefused) as excinfo:
            _check(owner="run-2", already_claimed=seen)
        assert excinfo.value.code == Refusal.PLACE_ALREADY_CLAIMED.value
        assert "run-1" in excinfo.value.detail

    def test_two_runs_on_different_places_do_not_overlap(self):
        seen = [_check(owner="run-1", resource_id="x1860-1").claim]
        second = _check(
            owner="run-2",
            resource_id="x1860-2",
            environ=_env(TOLLGATE_LABGRID_PLACE="x1860-2"),
            profile_endpoint="192.0.2.11",
            already_claimed=seen,
        )
        assert second.place == "x1860-2"

    def test_a_retry_of_the_same_run_is_not_an_overlap(self):
        seen = [_check(owner="run-1").claim]
        again = _check(owner="run-1", already_claimed=seen)
        assert again.claim == seen[0]

    def test_find_overlaps_is_symmetric_and_owner_aware(self):
        a = PlaceClaim(place="p1", owner="run-1")
        b = PlaceClaim(place="p1", owner="run-2")
        c = PlaceClaim(place="p2", owner="run-3")
        assert find_overlaps([a, b, c]) == ((a, b),)
        assert find_overlaps([b, c]) == ()
        assert find_overlaps([a, PlaceClaim(place="p1", owner="")]) == ()

    def test_blank_places_never_overlap(self):
        assert not PlaceClaim(place="", owner="a").overlaps(PlaceClaim(place="", owner="b"))

    def test_the_audit_detects_a_violation(self, tmp_path):
        """The audit must not be vacuous: a file that acquires is reported."""
        rogue = tmp_path / "rogue.py"
        rogue.write_text(
            "import subprocess\n"
            "from lib.labgrid_venue import venue_mutex\n"
            "def f():\n"
            "    venue_mutex(None, 'phase')\n"
            "    open('x')\n"
            "    subprocess.run(['x'])\n",
            encoding="utf-8",
        )
        audit = module_io_audit(rogue)
        assert "subprocess" in audit["modules"]
        assert "venue_mutex" in audit["names"]
        assert "venue_mutex" in audit["calls"]
        assert "open" in audit["calls"]

    def test_the_audit_flags_an_unlisted_plain_import(self, tmp_path):
        rogue = tmp_path / "rogue3.py"
        rogue.write_text("import requests\n", encoding="utf-8")
        assert module_io_audit(rogue)["modules"] == ("requests",)

    def test_the_audit_flags_an_unlisted_from_import(self, tmp_path):
        rogue = tmp_path / "rogue4.py"
        rogue.write_text("from lib.fleet_scheduler import FleetScheduler\n", encoding="utf-8")
        audit = module_io_audit(rogue)
        assert audit["modules"] == ("lib.fleet_scheduler",)
        assert audit["names"] == ("FleetScheduler",)

    def test_the_audit_flags_an_unlisted_project_import(self, tmp_path):
        rogue = tmp_path / "rogue2.py"
        rogue.write_text(
            "from lib.labgrid_venue import bench_for_router\n",
            encoding="utf-8",
        )
        audit = module_io_audit(rogue)
        assert "bench_for_router" in audit["names"]


# ---------------------------------------------------------------------------
# The mirror: venue_intent must predict plan_venue
# ---------------------------------------------------------------------------
class TestVenueIntentMirrorsPlanVenue:
    """Pin the pure prediction against the real planner.

    ``plan_venue`` is patched only at its I/O boundaries (inventory loader,
    client-binary probe) exactly as ``tests/unit/test_venue_plan.py`` does —
    the venue DECISION code under it is the real thing.
    """

    @pytest.fixture
    def patched(self, monkeypatch):
        inv = _inventory()
        monkeypatch.setattr("lib.labgrid_venue.inventory_exists", lambda: True)
        monkeypatch.setattr("lib.labgrid_venue._default_client_bin", lambda: "labgrid-client")
        monkeypatch.setattr("lib.labgrid_venue.load_inventory", lambda *a, **k: inv)
        return inv

    @pytest.mark.parametrize(
        "environ,scope,xdist",
        [
            ({"TOLLGATE_VENUE": "labgrid", "TOLLGATE_LABGRID_PLACE": "x1860-1"}, "full", False),
            ({"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "192.0.2.10"}, "full", False),
            ({"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "203.0.113.9"}, "full", False),
            ({"TOLLGATE_VENUE": "auto", "TOLLGATE_SSH_HOST": "192.0.2.10"}, "unit", False),
            ({"TOLLGATE_VENUE": "direct"}, "full", False),
            ({"TOLLGATE_VENUE": "labgrid", "TOLLGATE_SSH_HOST": "203.0.113.9"}, "full", False),
        ],
    )
    def test_intent_matches_the_planner(self, patched, environ, scope, xdist):
        from lib.labgrid_venue import plan_venue

        # conftest's own short-circuits come first, in this order.
        if xdist or (environ.get("TOLLGATE_VENUE", "auto") == "auto" and scope in ("unit", "parity")):
            assert not venue_intent(environ=environ, inventory=patched, session_scope=scope).owns_place
            return

        plan = None
        plan_error: Exception | None = None
        try:
            plan = plan_venue(environ=environ)
        except Exception as exc:  # noqa: BLE001 - the planner's refusal is the answer
            plan_error = exc

        if plan_error is not None:
            # Both sides must refuse; neither may fall back to a place-less run.
            with pytest.raises(BindingRefused):
                venue_intent(environ=environ, inventory=patched, session_scope=scope)
            return

        assert plan is not None
        intent = venue_intent(environ=environ, inventory=patched, session_scope=scope)
        if plan.action == "venue":
            assert intent.owns_place
            assert intent.candidate is not None
            assert intent.place == plan.binding.entry.place
            assert intent.candidate.router_name == plan.binding.entry.name
        else:
            assert not intent.owns_place, f"planner said legacy ({plan.detail}), intent claimed a place"

    def test_default_place_fallback_matches_the_planner(self, patched):
        from lib.labgrid_venue import plan_venue

        inv = _inventory(
            routers={
                "ap-lan2": RouterEntry(
                    name="ap-lan2", poe_port="1", address="192.0.2.9", place="ap-lan2"
                ),
            }
        )
        import lib.labgrid_venue as venue

        original = venue.load_inventory
        # plan_venue closes over the module attribute, so patch it directly.
        venue.load_inventory = lambda *a, **k: inv
        try:
            plan = plan_venue(environ={"TOLLGATE_VENUE": "labgrid"})
        finally:
            venue.load_inventory = original
        intent = venue_intent(environ={"TOLLGATE_VENUE": "labgrid"}, inventory=inv)
        assert plan.action == "venue"
        assert plan.binding.entry.place == intent.place == "ap-lan2"


# ---------------------------------------------------------------------------
# Code stability (log scrapers and CI assertions key off these strings)
# ---------------------------------------------------------------------------
class TestReasonCodes:
    EXPECTED = {
        "manifest_missing",
        "owner_missing",
        "fleet_ambiguous",
        "fleet_unknown",
        "lane_ambiguous",
        "resource_not_authorized",
        "resource_kind_unsupported",
        "venue_place_unresolvable",
        "place_ambiguous",
        "venue_takes_no_place",
        "fixture_would_skip_place",
        "resource_protected",
        "venue_address_missing",
        "coordinator_address_missing",
        "coordinator_address_mismatch",
        "place_override_conflict",
        "place_mismatch",
        "profile_endpoint_mismatch",
        "profile_endpoint_unknown",
        "place_already_claimed",
    }

    def test_codes_are_exactly_the_documented_set(self):
        assert CODES == self.EXPECTED

    def test_refusal_carries_code_and_reason(self):
        err = BindingRefused(Refusal.PLACE_MISMATCH, "detail here")
        assert err.code == "place_mismatch"
        assert err.reason == "place_mismatch"
        assert "place_mismatch: detail here" in str(err)

    def test_refusal_accepts_a_plain_string_for_forward_compat(self):
        assert BindingRefused("future_code").code == "future_code"
