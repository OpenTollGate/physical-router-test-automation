"""Unit tests for lib/fleet_capacity.py — slice 1: capacity DETECTION only.

Every test here is pure: no coordinator, no coordinator client, no QEMU, no
network, no hardware. Observations are values the test constructs; the module
decides. Where a test proves "this endpoint is never contacted" it counts
calls on an injected probe function rather than trusting a docstring.

Audit reference: reports/prta-fleet-capacity-audit.md, "Acceptance gates"
item 1 plus the state distinctions in "Required state distinctions".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.fleet_capacity import (
    COUNTABLE_STATES,
    FleetManifest,
    HostDemand,
    ManifestError,
    ManifestRefused,
    Observations,
    ProbeTarget,
    Reservation,
    Reason,
    ResourceKind,
    State,
    coordinator_capacity,
    coordinator_observation,
    configured_capacity,
    exporter_observation,
    guest_capacity,
    guest_observation,
    host_observation,
    load_manifest,
    lanes_independent,
    new_vm_slots,
    observation_plan,
    parse_manifest,
    redact,
    resource_conflicts,
    router_capacity,
    router_observation,
    run_probes,
    summarize,
)

pytestmark = [pytest.mark.story("P11")]

NOW = 1_000_000.0
TTL = 30.0
EPOCH = 7
REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures: two independent contributor fleets, nothing shared
# ---------------------------------------------------------------------------
def _manifest_doc() -> dict:
    return {
        "schema_version": 1,
        "fleets": [
            {
                "id": "alpha",
                "owner": "contributor-a",
                "coordinator": {
                    "address": "alpha-coord.lan:20408",
                    "credential_ref": {"source": "openbao", "name": "secret/fleet/alpha/client"},
                },
                "host_controller": {
                    "endpoint": "alpha-host.lan:8099",
                    "credential_ref": {"source": "openbao", "name": "secret/fleet/alpha/host"},
                },
                "allowed_places": [
                    "alpha-ap",
                    {"id": "alpha-lane1", "kind": "guest"},
                ],
                "permitted_actions": ["observe", "acquire", "release"],
                "capacity_limits": {"max_concurrent_runs": 2, "max_idle_guests": 1, "max_new_vm_slots": 1},
                "shared_conflict_ids": ["rf-2g-band"],
            },
            {
                "id": "beta",
                "owner": "contributor-b",
                "coordinator": {
                    "address": "beta-coord.example:20408",
                    "credential_ref": {"source": "openbao", "name": "secret/fleet/beta/client"},
                },
                "host_controller": {"endpoint": "beta-host.example:8099"},
                "allowed_places": ["beta-ap", {"id": "beta-lane1", "kind": "guest"}],
                "permitted_actions": ["observe"],
                "capacity_limits": {"max_concurrent_runs": 1},
            },
        ],
    }


@pytest.fixture()
def manifest() -> FleetManifest:
    return parse_manifest(_manifest_doc(), source="<test-manifest>")


def _healthy(manifest: FleetManifest, place: str, *, now: float = NOW, epoch: int = EPOCH) -> Observations:
    """Complete, fresh, healthy evidence for one router place in its fleet."""
    owner = manifest.owner_of(place)
    assert owner is not None
    return Observations(
        coordinator=coordinator_observation(fleet_id=owner, reachable=True, now=now, ttl_s=TTL, authority_epoch=epoch),
        exporter=exporter_observation(
            fleet_id=owner, resource_id=place, exporter_connected=True, now=now, ttl_s=TTL, authority_epoch=epoch
        ),
        health=router_observation(
            fleet_id=owner, resource_id=place, now=now, ttl_s=TTL, authority_epoch=epoch
        ),
    )


# ===========================================================================
# 1. Two independent contributors, separate coordinators, no global default
# ===========================================================================
class TestIndependentFleets:
    def test_two_contributors_have_separate_coordinators_and_hosts(self, manifest: FleetManifest):
        assert set(manifest.fleets) == {"alpha", "beta"}
        assert manifest.fleet("alpha").coordinator.address == "alpha-coord.lan:20408"
        assert manifest.fleet("beta").coordinator.address == "beta-coord.example:20408"
        assert manifest.fleet("alpha").host_controller.endpoint != manifest.fleet("beta").host_controller.endpoint

    def test_manifest_has_no_global_default_host_attribute(self, manifest: FleetManifest):
        """No fallback endpoint is reachable from the manifest object itself."""
        for attr in ("coordinator", "host", "host_controller", "default_host", "address"):
            assert not hasattr(manifest, attr), f"manifest exposes a global {attr!r}"

    def test_unknown_fleet_raises_instead_of_defaulting(self, manifest: FleetManifest):
        with pytest.raises(ManifestError) as err:
            manifest.fleet("gamma")
        assert "no default fleet or default host" in str(err.value)

    def test_places_belong_to_exactly_one_fleet(self, manifest: FleetManifest):
        assert manifest.owner_of("alpha-ap") == "alpha"
        assert manifest.owner_of("beta-ap") == "beta"
        assert manifest.owner_of("gamma-ap") is None

    def test_plan_targets_only_declared_endpoints_per_fleet(self, manifest: FleetManifest):
        plan = observation_plan(manifest)
        assert {(t.fleet_id, t.role, t.endpoint) for t in plan} == {
            ("alpha", "coordinator", "alpha-coord.lan:20408"),
            ("alpha", "host_controller", "alpha-host.lan:8099"),
            ("beta", "coordinator", "beta-coord.example:20408"),
            ("beta", "host_controller", "beta-host.example:8099"),
        }

    def test_no_action_permitted_means_no_probe_target(self, manifest: FleetManifest):
        """'power' is authorized by nobody, so no endpoint is planned for it."""
        assert observation_plan(manifest, action="power") == ()

    def test_unknown_fleet_selection_never_silently_widens(self, manifest: FleetManifest):
        with pytest.raises(ManifestError):
            observation_plan(manifest, fleets=["alpha", "gamma"])


# ===========================================================================
# 2. Duplicate ids / aliases refused — one canonical owner per resource
# ===========================================================================
class TestDuplicateOwnership:
    def _two_fleets(self, places_a, places_b) -> dict:
        doc = _manifest_doc()
        doc["fleets"][0]["allowed_places"] = places_a
        doc["fleets"][1]["allowed_places"] = places_b
        return doc

    def test_same_place_under_two_fleets_is_refused(self):
        doc = self._two_fleets(["shared-ap"], ["shared-ap"])
        with pytest.raises(ManifestRefused) as err:
            parse_manifest(doc, source="<dup>")
        message = str(err.value)
        assert "claimed by both" in message
        assert "exactly one authoritative fleet" in message

    def test_alias_collision_across_fleets_is_refused(self):
        doc = self._two_fleets(
            [{"id": "ap-1", "aliases": ["ap-lan2"]}],
            ["ap-lan2"],
        )
        with pytest.raises(ManifestRefused) as err:
            parse_manifest(doc, source="<alias>")
        assert "ap-lan2" in str(err.value)

    def test_place_declared_twice_in_one_fleet_is_refused(self):
        doc = self._two_fleets(["ap-1", {"id": "ap-2", "aliases": ["ap-1"]}], ["beta-ap"])
        with pytest.raises(ManifestRefused) as err:
            parse_manifest(doc, source="<self-dup>")
        assert "twice" in str(err.value)

    def test_duplicate_fleet_id_is_refused(self):
        doc = _manifest_doc()
        doc["fleets"].append(dict(doc["fleets"][0]))
        doc["fleets"][2]["allowed_places"] = ["other-ap"]
        with pytest.raises(ManifestRefused) as err:
            parse_manifest(doc, source="<dup-fleet>")
        assert "declared twice" in str(err.value)

    def test_alias_resolves_to_one_canonical_entry(self):
        doc = _manifest_doc()
        doc["fleets"][0]["allowed_places"] = [{"id": "alpha-ap", "aliases": ["ap-lan2", "alpha-lan2"]}]
        manifest = parse_manifest(doc)
        for name in ("alpha-ap", "ap-lan2", "alpha-lan2"):
            entry = manifest.resource(name)
            assert entry is not None
            assert entry.resource_id == "alpha-ap"
        assert len(manifest.resources_of("alpha")) == 1


# ===========================================================================
# 3. Unconfigured endpoints are never contacted
# ===========================================================================
class TestNeverContactUnconfigured:
    def test_probe_called_only_for_authorized_endpoints(self, manifest: FleetManifest):
        seen: list[ProbeTarget] = []

        def probe(target: ProbeTarget) -> str:
            seen.append(target)
            return "ok"

        results = run_probes(manifest, probe)
        contacted = {t.endpoint for t in seen}
        assert contacted == {
            "alpha-coord.lan:20408",
            "alpha-host.lan:8099",
            "beta-coord.example:20408",
            "beta-host.example:8099",
        }
        # An endpoint that is nowhere in the manifest is never a target.
        assert "gamma-coord.example:20408" not in contacted
        assert "192.0.2.1" not in contacted
        assert set(results) == {t.key for t in seen}

    def test_probe_refuses_a_target_the_manifest_does_not_authorize(self, manifest: FleetManifest):
        """Tampering with the plan cannot reach the probe: the guard re-derives
        authorization from the manifest."""
        import lib.fleet_capacity as fc

        original = fc.observation_plan
        try:
            fc.observation_plan = lambda m, fleets=None, action="observe": (  # type: ignore[assignment]
                *original(m, fleets=fleets, action=action),
                ProbeTarget(fleet_id="alpha", role="coordinator", endpoint="evil.example:20408"),
            )
            seen: list[str] = []
            with pytest.raises(Exception) as err:
                fc.run_probes(manifest, lambda t: seen.append(t.endpoint) or "ok")
            assert "not authorized" in str(err.value)
            # The unauthorized endpoint never reached the probe.
            assert "evil.example:20408" not in seen
        finally:
            fc.observation_plan = original  # type: ignore[assignment]

    def test_unconfigured_resource_is_not_assessed_and_never_counted(self, manifest: FleetManifest):
        verdict = router_capacity(manifest, "gamma-ap", now=NOW, authority_epoch=EPOCH)
        assert verdict.state is State.UNCONFIGURED
        assert verdict.capacity == 0
        assert Reason.NOT_CONFIGURED in verdict.reasons
        with pytest.raises(Exception):
            summarize(manifest, [verdict])

    def test_restricting_selection_contacts_nothing_else(self, manifest: FleetManifest):
        seen: list[str] = []
        run_probes(manifest, lambda t: seen.append(t.endpoint) or "ok", fleets=["beta"])
        assert seen == ["beta-coord.example:20408", "beta-host.example:8099"]

    def test_acquire_only_fleet_is_not_contacted_for_observation(self):
        doc = _manifest_doc()
        doc["fleets"][1]["permitted_actions"] = ["acquire"]
        with pytest.raises(ManifestError) as err:
            parse_manifest(doc, source="<no-observe>")
        assert "observe" in str(err.value)

    def test_unknown_action_is_not_a_wildcard(self, manifest: FleetManifest):
        with pytest.raises(ManifestError):
            observation_plan(manifest, action="scan-subnet")
        doc = _manifest_doc()
        doc["fleets"][0]["permitted_actions"] = ["observe", "flash_everything"]
        with pytest.raises(ManifestError) as err:
            parse_manifest(doc, source="<bad-action>")
        assert "never a wildcard" in str(err.value)


# ===========================================================================
# 4. Secret-bearing malformed YAML produces a REDACTED error
# ===========================================================================
SECRET_VALUE = "hunter2-SuperSecret-0042"  # noqa: S105 - test fixture, not a real secret


class TestRedactedErrors:
    def _write(self, tmp_path: Path, text: str) -> Path:
        target = tmp_path / "fleets.yaml"
        target.write_text(text, encoding="utf-8")
        return target

    def test_yaml_syntax_error_does_not_echo_source_snippet(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            "schema_version: 1\nfleets:\n  - id: alpha\n"
            f"    coordinator_password: {SECRET_VALUE}\n"
            "   bad_indent: [unclosed\n",
        )
        with pytest.raises(ManifestError) as err:
            load_manifest(path)
        message = str(err.value)
        assert "not valid YAML" in message
        assert SECRET_VALUE not in message
        assert "hunter2" not in message
        # PyYAML's own text embeds the offending line; ours is position-only.
        assert "coordinator_password" not in message

    def test_literal_credential_in_manifest_is_refused_and_redacted(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            "schema_version: 1\nfleets:\n  - id: alpha\n"
            "    owner: a\n"
            "    coordinator:\n"
            "      address: alpha-coord.lan:20408\n"
            f"      credential_ref: {{source: openbao, name: x, password: {SECRET_VALUE}}}\n"
            "    host_controller: {endpoint: alpha-host.lan:8099}\n"
            "    allowed_places: [alpha-ap]\n"
            "    permitted_actions: [observe]\n",
        )
        with pytest.raises(ManifestRefused) as err:
            load_manifest(path)
        message = str(err.value)
        assert "never embedded" in message
        assert SECRET_VALUE not in message

    def test_unknown_fleet_key_is_refused_without_echoing_its_value(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            "schema_version: 1\nfleets:\n  - id: alpha\n"
            "    owner: a\n"
            "    coordinator: {address: 'alpha-coord.lan:20408'}\n"
            "    host_controller: {endpoint: 'alpha-host.lan:8099'}\n"
            "    allowed_places: [alpha-ap]\n"
            "    permitted_actions: [observe]\n"
            f"    ssh_password: {SECRET_VALUE}\n",
        )
        with pytest.raises(ManifestRefused) as err:
            load_manifest(path)
        assert "unknown key" in str(err.value)
        assert SECRET_VALUE not in str(err.value)

    def test_redact_helper_covers_common_credential_shapes(self):
        cases = [
            "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
            "nsec1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            "ghp_" + "a" * 36,
            "password: swordfish",
            "api_key=abc123def456",
        ]
        for case in cases:
            assert "swordfish" not in redact(case)
            assert "abc123def456" not in redact(case)
            assert "a" * 36 not in redact(case)
            assert "[REDACTED" in redact(case)

    def test_unreadable_manifest_names_no_content(self, tmp_path: Path):
        with pytest.raises(ManifestError) as err:
            load_manifest(tmp_path / "absent.yaml")
        assert "unreadable" in str(err.value)
        assert "schema" in str(err.value)

    def test_committed_example_is_placeholder_only(self):
        """The shipped example must not load as a real fleet."""
        example = REPO / "config" / "fleets.example.yaml"
        assert example.is_file()
        with pytest.raises(ManifestError) as err:
            load_manifest(example)
        assert "placeholder" in str(err.value)
        text = example.read_text(encoding="utf-8")
        # Credentials appear only as (source, name) references.
        assert "credential_ref" in text
        assert "PRIVATE KEY" not in text


# ===========================================================================
# 5. Stale / unknown telemetry is excluded, never counted as idle
# ===========================================================================
class TestStaleUnknownNeverIdle:
    def test_missing_coordinator_evidence_is_unknown(self, manifest: FleetManifest):
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=Observations())
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0
        assert Reason.NO_TELEMETRY in verdict.reasons

    def test_stale_exporter_evidence_is_stale_not_idle(self, manifest: FleetManifest):
        obs = _healthy(manifest, "alpha-ap", now=NOW - TTL - 1)
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.STALE
        assert verdict.capacity == 0
        assert Reason.STALE_TELEMETRY in verdict.reasons

    def test_old_authority_epoch_is_not_capacity(self, manifest: FleetManifest):
        obs = _healthy(manifest, "alpha-ap", epoch=EPOCH - 1)
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.STALE
        assert verdict.capacity == 0
        assert Reason.EPOCH_MISMATCH in verdict.reasons

    def test_future_timestamp_is_clock_skew_not_capacity(self, manifest: FleetManifest):
        obs = _healthy(manifest, "alpha-ap", now=NOW + 120)
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.STALE
        assert Reason.CLOCK_SKEW in verdict.reasons

    def test_zero_ttl_evidence_never_counts(self, manifest: FleetManifest):
        base = _healthy(manifest, "alpha-ap")
        obs = Observations(
            coordinator=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=0, authority_epoch=EPOCH
            ),
            exporter=base.exporter,
            health=base.health,
        )
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.STALE
        assert verdict.capacity == 0

    def test_unknown_health_dimension_is_unknown_not_idle(self, manifest: FleetManifest):
        base = _healthy(manifest, "alpha-ap")
        unknown_health = router_observation(
            fleet_id="alpha",
            resource_id="alpha-ap",
            now=NOW,
            ttl_s=TTL,
            authority_epoch=EPOCH,
            health_ok=None,
        )
        obs = Observations(coordinator=base.coordinator, exporter=base.exporter, health=unknown_health)
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0

    def test_unknown_session_state_is_unknown_not_idle(self, manifest: FleetManifest):
        base = _healthy(manifest, "alpha-ap")
        health = router_observation(
            fleet_id="alpha", resource_id="alpha-ap", now=NOW, ttl_s=TTL, authority_epoch=EPOCH, session_active=None
        )
        obs = Observations(coordinator=base.coordinator, exporter=base.exporter, health=health)
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0

    def test_stale_evidence_is_carried_in_excluded_list_not_totals(self, manifest: FleetManifest):
        fresh = router_capacity(
            manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=_healthy(manifest, "alpha-ap")
        )
        stale = router_capacity(
            manifest,
            "alpha-lane1",
            now=NOW,
            authority_epoch=EPOCH,
            observations=Observations(
                guest=guest_observation(
                    fleet_id="alpha",
                    resource_id="alpha-lane1",
                    now=NOW - TTL - 5,
                    ttl_s=TTL,
                    authority_epoch=EPOCH,
                    pid=42,
                    pid_start_ticks=9,
                    expected_pid_start_ticks=9,
                    health_ok=True,
                    topology_ok=True,
                    lease_free=True,
                    client_reachable=True,
                )
            ),
        )
        (alpha,) = [s for s in summarize(manifest, [fresh, stale]) if s.fleet_id == "alpha"]
        assert alpha.idle_routers == 1
        assert alpha.idle_guests == 0
        assert [v.resource_id for v in alpha.excluded] == ["alpha-lane1"]

    def test_evidence_from_another_fleet_is_refused(self, manifest: FleetManifest):
        obs = _healthy(manifest, "beta-ap")
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.REFUSED
        assert Reason.WRONG_FLEET in verdict.reasons
        assert verdict.capacity == 0

    def test_evidence_naming_an_alias_is_accepted_and_reported_canonically(self):
        doc = _manifest_doc()
        doc["fleets"][0]["allowed_places"] = [{"id": "alpha-ap", "aliases": ["ap-lan2"]}]
        manifest = parse_manifest(doc, source="<alias-obs>")
        obs = Observations(
            coordinator=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
            exporter=exporter_observation(
                fleet_id="alpha", resource_id="ap-lan2", exporter_connected=True, now=NOW, ttl_s=TTL,
                authority_epoch=EPOCH,
            ),
            health=router_observation(
                fleet_id="alpha", resource_id="ap-lan2", now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
        )
        by_alias = router_capacity(manifest, "ap-lan2", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert by_alias.state is State.HEALTHY_IDLE
        assert by_alias.resource_id == "alpha-ap"
        assert by_alias.capacity == 1

    def test_evidence_for_a_different_resource_is_refused(self, manifest: FleetManifest):
        obs = _healthy(manifest, "alpha-ap")
        other = Observations(
            coordinator=obs.coordinator,
            exporter=obs.exporter,
            health=router_observation(
                fleet_id="alpha", resource_id="beta-ap", now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
        )
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=other)
        assert verdict.state is State.REFUSED
        assert Reason.WRONG_FLEET in verdict.reasons


# ===========================================================================
# 6. Coordinator reachable + exporter disconnected is NOT capacity
# ===========================================================================
class TestControlPlaneIsNotCapacity:
    def test_reachable_coordinator_alone_is_zero(self, manifest: FleetManifest):
        obs = Observations(
            coordinator=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            )
        )
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0
        assert Reason.CONTROL_PLANE_ONLY in verdict.reasons
        assert verdict.countable is False

    def test_coordinator_verdict_never_carries_capacity(self, manifest: FleetManifest):
        verdict = coordinator_capacity(
            manifest,
            "alpha",
            now=NOW,
            authority_epoch=EPOCH,
            observation=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
        )
        assert verdict.state is State.COORDINATOR_REACHABLE
        assert verdict.capacity == 0
        assert verdict.countable is False
        assert Reason.CONTROL_PLANE_ONLY in verdict.reasons

    def test_coordinator_reachable_exporter_disconnected_is_zero(self, manifest: FleetManifest):
        obs = Observations(
            coordinator=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
            exporter=exporter_observation(
                fleet_id="alpha",
                resource_id="alpha-ap",
                exporter_connected=False,
                now=NOW,
                ttl_s=TTL,
                authority_epoch=EPOCH,
            ),
            health=router_observation(
                fleet_id="alpha", resource_id="alpha-ap", now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
        )
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0
        assert Reason.EXPORTER_DISCONNECTED in verdict.reasons
        assert verdict.countable is False

    def test_acquired_place_is_refused_even_when_healthy(self, manifest: FleetManifest):
        obs = Observations(
            coordinator=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
            exporter=exporter_observation(
                fleet_id="alpha",
                resource_id="alpha-ap",
                exporter_connected=True,
                acquired=True,
                now=NOW,
                ttl_s=TTL,
                authority_epoch=EPOCH,
            ),
            health=router_observation(
                fleet_id="alpha", resource_id="alpha-ap", now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
        )
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.REFUSED
        assert Reason.PLACE_ACQUIRED in verdict.reasons

    def test_protected_router_and_active_session_are_refused(self, manifest: FleetManifest):
        base = _healthy(manifest, "alpha-ap")
        protected = router_observation(
            fleet_id="alpha", resource_id="alpha-ap", now=NOW, ttl_s=TTL, authority_epoch=EPOCH, protected=True
        )
        verdict = router_capacity(
            manifest,
            "alpha-ap",
            now=NOW,
            authority_epoch=EPOCH,
            observations=Observations(base.coordinator, base.exporter, protected),
        )
        assert verdict.state is State.REFUSED
        assert Reason.PROTECTED in verdict.reasons

        busy = router_observation(
            fleet_id="alpha", resource_id="alpha-ap", now=NOW, ttl_s=TTL, authority_epoch=EPOCH, session_active=True
        )
        verdict = router_capacity(
            manifest,
            "alpha-ap",
            now=NOW,
            authority_epoch=EPOCH,
            observations=Observations(base.coordinator, base.exporter, busy),
        )
        assert verdict.state is State.REFUSED
        assert Reason.SESSION_ACTIVE in verdict.reasons

    def test_identity_mismatch_and_failed_vantage_are_refused(self, manifest: FleetManifest):
        base = _healthy(manifest, "alpha-ap")
        wrong = router_observation(
            fleet_id="alpha",
            resource_id="alpha-ap",
            now=NOW,
            ttl_s=TTL,
            authority_epoch=EPOCH,
            identity="someone-elses-router",
            expected_identity="alpha-ap",
        )
        verdict = router_capacity(
            manifest,
            "alpha-ap",
            now=NOW,
            authority_epoch=EPOCH,
            observations=Observations(base.coordinator, base.exporter, wrong),
        )
        assert Reason.IDENTITY_MISMATCH in verdict.reasons

        blind = router_observation(
            fleet_id="alpha", resource_id="alpha-ap", now=NOW, ttl_s=TTL, authority_epoch=EPOCH, vantage_ok=False
        )
        verdict = router_capacity(
            manifest,
            "alpha-ap",
            now=NOW,
            authority_epoch=EPOCH,
            observations=Observations(base.coordinator, base.exporter, blind),
        )
        assert verdict.state is State.REFUSED
        assert Reason.VANTAGE_UNAVAILABLE in verdict.reasons

    def test_exported_available_without_health_is_counted_as_exported_not_idle(self, manifest: FleetManifest):
        obs = Observations(
            coordinator=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
            exporter=exporter_observation(
                fleet_id="alpha",
                resource_id="alpha-ap",
                exporter_connected=True,
                now=NOW,
                ttl_s=TTL,
                authority_epoch=EPOCH,
            ),
        )
        verdict = router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.EXPORTED_AVAILABLE
        assert verdict.capacity == 0
        assert verdict.countable is False
        (alpha,) = [s for s in summarize(manifest, [verdict]) if s.fleet_id == "alpha"]
        assert alpha.exported_available == 1
        assert alpha.idle_routers == 0

    def test_complete_fresh_evidence_is_the_only_healthy_idle(self, manifest: FleetManifest):
        verdict = router_capacity(
            manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=_healthy(manifest, "alpha-ap")
        )
        assert verdict.state is State.HEALTHY_IDLE
        assert verdict.capacity == 1
        assert verdict.countable is True


# ===========================================================================
# 7. Idle guest and new VM slots are DISTINCT numbers
# ===========================================================================
def _idle_guest(manifest: FleetManifest, place: str = "alpha-lane1", **overrides) -> Observations:
    owner = manifest.owner_of(place)
    kwargs = dict(
        fleet_id=owner,
        resource_id=place,
        now=NOW,
        ttl_s=TTL,
        authority_epoch=EPOCH,
        pid=4242,
        pid_start_ticks=555,
        expected_pid_start_ticks=555,
        bridge_present=True,
        health_ok=True,
        topology_ok=True,
        lease_free=True,
        client_reachable=True,
    )
    kwargs.update(overrides)
    return Observations(guest=guest_observation(**kwargs))


class TestIdleGuestVsNewVmSlots:
    DEMAND = HostDemand(vcpus=2, ram_mib=1024, disk_mib=4096)

    def _host(self, **overrides):
        kwargs = dict(
            fleet_id="alpha",
            resource_id="alpha-host",
            now=NOW,
            ttl_s=TTL,
            authority_epoch=EPOCH,
            cpu_total=8.0,
            cpu_committed=4.0,     # one 2-vCPU guest idle + 2 vCPU elsewhere
            ram_total_mib=16384,
            ram_committed_mib=4096,  # includes the idle guest's 1024 MiB
            ram_reserve_mib=2048,
            disk_free_mib=40960,
            inode_free=1_000_000,
            kvm_usable=True,
            architecture="x86_64",
        )
        kwargs.update(overrides)
        return host_observation(**kwargs)

    def test_idle_guest_and_new_slots_are_reported_separately(self, manifest: FleetManifest):
        guest = guest_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=_idle_guest(manifest))
        slots = new_vm_slots(
            self._host(),
            demand=self.DEMAND,
            now=NOW,
            authority_epoch=EPOCH,
            required_architecture="x86_64",
        )
        assert guest.state is State.IDLE_GUEST and guest.capacity == 1
        assert slots.state is State.NEW_VM_SLOTS_AVAILABLE
        # 8 CPU - 4 committed = 4 -> 2 lanes; RAM 16384-4096-2048=10240 -> 10;
        # disk 40960/4096 = 10. CPU binds: 2 slots.
        assert slots.slots == 2

        (alpha,) = [s for s in summarize(manifest, [guest, slots]) if s.fleet_id == "alpha"]
        assert alpha.idle_guests == 1
        assert alpha.new_vm_slots == 2
        # A lane and a host-admission slot are never summed into one number.
        assert alpha.lanes_ready == 1

    def test_idle_guest_consumption_is_not_double_subtracted(self, manifest: FleetManifest):
        """A started reservation is already inside *_committed: subtracting it
        again would over-report headroom. An unfinished claim is subtracted."""
        started = Reservation(run_id="r-started", demand=self.DEMAND, expires_at=NOW + 600, started=True)
        pending = Reservation(run_id="r-pending", demand=self.DEMAND, expires_at=NOW + 600)
        base = new_vm_slots(self._host(), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH)
        with_started = new_vm_slots(
            self._host(), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH, reservations=[started]
        )
        with_pending = new_vm_slots(
            self._host(), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH, reservations=[pending]
        )
        assert with_started.slots == base.slots
        assert with_pending.slots == base.slots - 1
        assert with_pending.slots < with_started.slots

    def test_expired_reservation_stops_consuming_budget(self):
        expired = Reservation(run_id="r-old", demand=self.DEMAND, expires_at=NOW - 1)
        verdict = new_vm_slots(self._host(), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH, reservations=[expired])
        assert verdict.slots == 2

    def test_fleet_limit_caps_slots_without_inflating_them(self):
        verdict = new_vm_slots(self._host(), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH, limit=1)
        assert verdict.slots == 1
        assert verdict.state is State.NEW_VM_SLOTS_AVAILABLE

    def test_bridge_present_alone_is_not_an_idle_guest(self, manifest: FleetManifest):
        """The audit's VM-discovery finding: a live bridge is not readiness."""
        obs = Observations(
            guest=guest_observation(
                fleet_id="alpha",
                resource_id="alpha-lane1",
                now=NOW,
                ttl_s=TTL,
                authority_epoch=EPOCH,
                bridge_present=True,
            )
        )
        verdict = guest_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0
        assert Reason.BRIDGE_ONLY in verdict.reasons

    def test_pid_present_without_start_time_is_not_idle(self, manifest: FleetManifest):
        obs = Observations(
            guest=guest_observation(
                fleet_id="alpha", resource_id="alpha-lane1", now=NOW, ttl_s=TTL, authority_epoch=EPOCH, pid=4242
            )
        )
        verdict = guest_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0

    def test_reused_pid_is_refused(self, manifest: FleetManifest):
        obs = _idle_guest(manifest, pid_start_ticks=999, expected_pid_start_ticks=555)
        verdict = guest_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.REFUSED
        assert Reason.GUEST_PID_REUSED in verdict.reasons

    @pytest.mark.parametrize(
        "dimension,value,expected",
        [
            ("health_ok", False, Reason.HEALTH_FAILED),
            ("topology_ok", False, Reason.TOPOLOGY_BROKEN),
            ("lease_free", False, Reason.LEASE_HELD),
            ("client_reachable", False, Reason.VANTAGE_UNAVAILABLE),
        ],
    )
    def test_each_unmet_guest_dimension_refuses(self, manifest, dimension, value, expected):
        obs = _idle_guest(manifest, **{dimension: value})
        verdict = guest_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.REFUSED
        assert verdict.capacity == 0
        assert expected in verdict.reasons
        # The refusal names the dimension, so an operator is not left guessing.
        assert dimension.split("_")[0] in verdict.detail

    def test_partially_proven_guest_is_unknown_not_idle(self, manifest: FleetManifest):
        obs = _idle_guest(manifest, client_reachable=None)
        verdict = guest_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=obs)
        assert verdict.state is State.UNKNOWN
        assert verdict.capacity == 0


# ===========================================================================
# 8. RAM fits but disk or CPU does not -> admits nothing
# ===========================================================================
class TestResourceVectorAdmission:
    DEMAND = HostDemand(vcpus=2, ram_mib=1024, disk_mib=8192)

    def _host(self, **overrides):
        kwargs = dict(
            fleet_id="alpha",
            resource_id="alpha-host",
            now=NOW,
            ttl_s=TTL,
            authority_epoch=EPOCH,
            cpu_total=8.0,
            cpu_committed=0.0,
            ram_total_mib=32768,
            ram_committed_mib=0,
            ram_reserve_mib=1024,
            disk_free_mib=10240,
            inode_free=500_000,
            kvm_usable=True,
            architecture="x86_64",
        )
        kwargs.update(overrides)
        return host_observation(**kwargs)

    def test_ram_fits_repeatedly_but_disk_does_not(self):
        verdict = new_vm_slots(
            self._host(disk_free_mib=4096), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH
        )
        assert verdict.state is State.REFUSED
        assert verdict.slots == 0
        assert Reason.DISK_INSUFFICIENT in verdict.reasons
        assert Reason.RAM_INSUFFICIENT not in verdict.reasons
        assert verdict.headroom["ram_mib"] > 0

    def test_ram_fits_but_cpu_does_not(self):
        verdict = new_vm_slots(self._host(cpu_committed=8.0), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH)
        assert verdict.state is State.REFUSED
        assert verdict.slots == 0
        assert Reason.CPU_INSUFFICIENT in verdict.reasons

    def test_ram_fits_but_inodes_exhausted(self):
        verdict = new_vm_slots(self._host(inode_free=0), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH)
        assert verdict.state is State.REFUSED
        assert Reason.INODE_INSUFFICIENT in verdict.reasons

    def test_ram_safety_reserve_is_respected(self):
        """RAM total minus reserve minus committed — not raw free RAM."""
        verdict = new_vm_slots(
            self._host(ram_total_mib=4096, ram_committed_mib=0, ram_reserve_mib=3072),
            demand=self.DEMAND,
            now=NOW,
            authority_epoch=EPOCH,
        )
        assert verdict.slots == 1  # (4096-3072)/1024

    def test_kvm_unavailable_admits_nothing(self):
        verdict = new_vm_slots(self._host(kvm_usable=False), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH)
        assert verdict.slots == 0
        assert Reason.KVM_UNAVAILABLE in verdict.reasons

    def test_unknown_kvm_is_not_optimistic(self):
        verdict = new_vm_slots(self._host(kvm_usable=None), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH)
        assert verdict.slots == 0
        assert verdict.state is State.UNKNOWN

    def test_unknown_dimension_yields_zero(self):
        verdict = new_vm_slots(
            self._host(unknown_dimensions=frozenset({"overlay_growth"})),
            demand=self.DEMAND,
            now=NOW,
            authority_epoch=EPOCH,
        )
        assert verdict.slots == 0
        assert verdict.state is State.UNKNOWN
        assert Reason.UNKNOWN_DIMENSION in verdict.reasons
        assert "overlay_growth" in verdict.detail

    def test_architecture_mismatch_admits_nothing(self):
        verdict = new_vm_slots(
            self._host(architecture="aarch64"),
            demand=self.DEMAND,
            now=NOW,
            authority_epoch=EPOCH,
            required_architecture="x86_64",
        )
        assert verdict.slots == 0
        assert Reason.ARCH_MISMATCH in verdict.reasons

    def test_stale_host_telemetry_admits_nothing(self):
        verdict = new_vm_slots(
            self._host(observed_at=NOW - TTL - 1), demand=self.DEMAND, now=NOW, authority_epoch=EPOCH
        )
        assert verdict.slots == 0
        assert verdict.state is State.STALE

    def test_missing_host_telemetry_admits_nothing(self):
        verdict = new_vm_slots(None, demand=self.DEMAND, now=NOW, authority_epoch=EPOCH)
        assert verdict.slots == 0
        assert Reason.NO_TELEMETRY in verdict.reasons

    @pytest.mark.parametrize("field", ["vcpus", "ram_mib", "disk_mib", "disk_inodes"])
    def test_demand_must_be_positive(self, field):
        kwargs = dict(vcpus=1, ram_mib=1, disk_mib=1)
        kwargs[field] = 0
        with pytest.raises(ValueError):
            HostDemand(**kwargs)


# ===========================================================================
# Summary shape + honesty invariants
# ===========================================================================
class TestSummaryHonesty:
    def test_configured_is_not_capacity(self, manifest: FleetManifest):
        verdict = configured_capacity(manifest, "alpha-ap")
        assert verdict.state is State.CONFIGURED
        assert verdict.capacity == 0
        assert verdict.countable is False
        assert verdict.fleet_id == "alpha"

    def test_configured_refuses_an_unconfigured_resource(self, manifest: FleetManifest):
        verdict = configured_capacity(manifest, "gamma-ap")
        assert verdict.state is State.UNCONFIGURED
        assert Reason.NOT_CONFIGURED in verdict.reasons

    def test_kind_mismatch_is_distinguished_from_unconfigured(self, manifest: FleetManifest):
        verdict = router_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH)
        assert verdict.state is State.REFUSED
        assert Reason.KIND_MISMATCH in verdict.reasons

    def test_every_declared_state_has_a_producer(self, manifest: FleetManifest):
        """No state may be documentation-only: each is reachable."""
        produced = {
            configured_capacity(manifest, "alpha-ap").state,
            coordinator_capacity(
                manifest,
                "alpha",
                now=NOW,
                authority_epoch=EPOCH,
                observation=coordinator_observation(
                    fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
                ),
            ).state,
            router_capacity(
                manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=_healthy(manifest, "alpha-ap")
            ).state,
            guest_capacity(
                manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=_idle_guest(manifest)
            ).state,
            new_vm_slots(
                host_observation(
                    fleet_id="alpha",
                    resource_id="alpha-host",
                    now=NOW,
                    ttl_s=TTL,
                    authority_epoch=EPOCH,
                    cpu_total=4.0,
                    cpu_committed=0.0,
                    ram_total_mib=8192,
                    ram_committed_mib=0,
                    ram_reserve_mib=0,
                    disk_free_mib=100_000,
                    inode_free=100_000,
                    kvm_usable=True,
                    architecture="x86_64",
                ),
                demand=HostDemand(vcpus=2, ram_mib=1024, disk_mib=1024),
                now=NOW,
                authority_epoch=EPOCH,
            ).state,
            router_capacity(manifest, "gamma-ap", now=NOW, authority_epoch=EPOCH).state,
            router_capacity(manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH).state,
            router_capacity(
                manifest,
                "alpha-ap",
                now=NOW,
                authority_epoch=EPOCH,
                observations=_healthy(manifest, "alpha-ap", now=NOW - TTL - 1),
            ).state,
            router_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH).state,
            router_capacity(
                manifest,
                "alpha-ap",
                now=NOW,
                authority_epoch=EPOCH,
                observations=Observations(
                    coordinator=coordinator_observation(
                        fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
                    ),
                    exporter=exporter_observation(
                        fleet_id="alpha",
                        resource_id="alpha-ap",
                        exporter_connected=True,
                        now=NOW,
                        ttl_s=TTL,
                        authority_epoch=EPOCH,
                    ),
                ),
            ).state,
        }
        assert {s for s in State} <= produced, f"states never produced: {sorted(s.value for s in set(State) - produced)}"

    def test_only_countable_states_can_carry_capacity(self):
        assert COUNTABLE_STATES == {
            State.HEALTHY_IDLE,
            State.IDLE_GUEST,
            State.NEW_VM_SLOTS_AVAILABLE,
        }
        assert State.COORDINATOR_REACHABLE not in COUNTABLE_STATES

    def test_verdicts_for_unconfigured_resources_cannot_be_counted(self, manifest: FleetManifest):
        stray = router_capacity(manifest, "nobody-ap", now=NOW, authority_epoch=EPOCH)
        with pytest.raises(Exception):
            summarize(manifest, [stray])

    def test_fleet_with_no_evidence_reports_zeros(self, manifest: FleetManifest):
        summaries = summarize(manifest, [])
        assert {s.fleet_id for s in summaries} == {"alpha", "beta"}
        for summary in summaries:
            assert summary.idle_routers == 0
            assert summary.idle_guests == 0
            assert summary.new_vm_slots == 0
            assert summary.lanes_ready == 0
            assert summary.coordinator_reachable is False
            assert summary.configured_places == 2

    def test_limits_only_tighten_reported_capacity(self, manifest: FleetManifest):
        guests = [
            guest_capacity(manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=_idle_guest(manifest))
        ]
        (alpha,) = [s for s in summarize(manifest, guests) if s.fleet_id == "alpha"]
        assert alpha.idle_guests == 1  # observed
        assert alpha.max_idle_guests == 1  # declared
        assert alpha.admissible_idle_guests == 1
        # The declared cap can never raise the observation.
        assert alpha.admissible_new_vm_slots == 0
        assert alpha.new_vm_slots == 0

    def test_coordinator_verdict_is_not_a_place(self, manifest: FleetManifest):
        coord = coordinator_capacity(
            manifest,
            "alpha",
            now=NOW,
            authority_epoch=EPOCH,
            observation=coordinator_observation(
                fleet_id="alpha", reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
            ),
        )
        (alpha,) = [s for s in summarize(manifest, [coord]) if s.fleet_id == "alpha"]
        assert alpha.coordinator_reachable is True
        assert alpha.configured_places == 2  # unchanged: a coordinator is not a place
        assert alpha.lanes_ready == 0


# ===========================================================================
# Manifest schema / redaction invariants
# ===========================================================================
class TestManifestSchema:
    def test_example_file_is_valid_yaml_and_documents_the_rules(self):
        import yaml

        text = (REPO / "config" / "fleets.example.yaml").read_text(encoding="utf-8")
        doc = yaml.safe_load(text)  # must parse
        assert isinstance(doc, dict) and "fleets" in doc
        assert "NO GLOBAL DEFAULT" in text
        assert "A PLACEHOLDER" in text.upper()

    def test_there_is_no_top_level_endpoint_fields(self):
        doc = _manifest_doc()
        doc["coordinator"] = {"address": "global.example:20408"}
        # A top-level coordinator is simply unknown data the loader never
        # reads; assert the manifest object exposes no way to reach it.
        manifest = parse_manifest(doc)
        assert not hasattr(manifest, "coordinator")

    def test_placeholder_values_are_rejected(self):
        doc = _manifest_doc()
        doc["fleets"][0]["coordinator"]["address"] = "REPLACE_ME_HOST:20408"
        with pytest.raises(ManifestError) as err:
            parse_manifest(doc, source="<placeholder>")
        assert "placeholder" in str(err.value)

    def test_coordinator_address_must_be_host_port(self):
        doc = _manifest_doc()
        doc["fleets"][0]["coordinator"]["address"] = "alpha-coord"
        with pytest.raises(ManifestError) as err:
            parse_manifest(doc, source="<no-port>")
        assert "host:port" in str(err.value)

    def test_unknown_place_kind_is_rejected(self):
        doc = _manifest_doc()
        doc["fleets"][0]["allowed_places"] = [{"id": "alpha-x", "kind": "toaster"}]
        with pytest.raises(ManifestError):
            parse_manifest(doc, source="<bad-kind>")

    def test_missing_allowed_places_is_rejected(self):
        doc = _manifest_doc()
        doc["fleets"][0].pop("allowed_places")
        with pytest.raises(ManifestError) as err:
            parse_manifest(doc, source="<no-places>")
        assert "never discovered by scanning" in str(err.value)

    def test_schema_version_is_pinned(self):
        doc = _manifest_doc()
        doc["schema_version"] = 2
        with pytest.raises(ManifestError):
            parse_manifest(doc, source="<v2>")

    def test_negative_limit_is_rejected(self):
        doc = _manifest_doc()
        doc["fleets"][0]["capacity_limits"]["max_concurrent_runs"] = -1
        with pytest.raises(ManifestError):
            parse_manifest(doc, source="<neg>")

    def test_placeholder_values_rejected_everywhere(self):
        doc = _manifest_doc()
        doc["fleets"][0]["allowed_places"] = ["<PLACEHOLDER>"]
        with pytest.raises(ManifestError):
            parse_manifest(doc, source="<ph-place>")


# ===========================================================================
# 9. Conflict domains: declared, not "everything shares an exporter"
# ===========================================================================
class TestConflictDomains:
    def test_places_in_the_same_fleet_share_its_conflict_domains(self, manifest: FleetManifest):
        assert resource_conflicts(manifest, "alpha-ap") == frozenset({"rf-2g-band"})
        assert resource_conflicts(manifest, "alpha-lane1") == frozenset({"rf-2g-band"})
        assert resource_conflicts(manifest, "beta-ap") == frozenset()

    def test_unconfigured_resource_has_no_conflict_domains(self, manifest: FleetManifest):
        assert resource_conflicts(manifest, "gamma-ap") == frozenset()

    def test_lanes_between_fleets_without_shared_ids_are_independent(self, manifest: FleetManifest):
        ok, shared = lanes_independent(manifest, "alpha-ap", "beta-ap")
        assert ok is True
        assert shared == ()

    def test_explicitly_shared_conflict_serializes_different_fleets(self):
        doc = _manifest_doc()
        doc["fleets"][1]["shared_conflict_ids"] = ["rf-2g-band"]
        manifest = parse_manifest(doc, source="<shared-rf>")
        ok, shared = lanes_independent(manifest, "alpha-ap", "beta-ap")
        assert ok is False
        assert shared == ("rf-2g-band",)

    def test_a_lane_is_never_independent_of_itself(self, manifest: FleetManifest):
        ok, shared = lanes_independent(manifest, "alpha-ap", "alpha-ap")
        assert ok is False
        assert shared == ("same_resource",)

    def test_independence_of_unconfigured_lane_raises(self, manifest: FleetManifest):
        with pytest.raises(ManifestError):
            lanes_independent(manifest, "alpha-ap", "gamma-ap")


def test_module_decision_functions_take_no_io(monkeypatch):
    """The decision path must not open files, sockets or the clock.

    Proof by prohibition: any use of these entry points during a decision
    raises, so a future edit that "helpfully" probes something fails here.
    """
    import builtins
    import socket
    import time

    def forbidden(*args, **kwargs):
        raise AssertionError("decision path attempted I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(time, "time", forbidden)

    manifest = parse_manifest(_manifest_doc(), source="<pure>")
    verdict = router_capacity(
        manifest, "alpha-ap", now=NOW, authority_epoch=EPOCH, observations=_healthy(manifest, "alpha-ap")
    )
    assert verdict.state is State.HEALTHY_IDLE
    guest = guest_capacity(
        manifest, "alpha-lane1", now=NOW, authority_epoch=EPOCH, observations=_idle_guest(manifest)
    )
    assert guest.kind is ResourceKind.GUEST
    slots = new_vm_slots(
        host_observation(
            fleet_id="alpha",
            resource_id="alpha-host",
            now=NOW,
            ttl_s=TTL,
            authority_epoch=EPOCH,
            cpu_total=4.0,
            cpu_committed=0.0,
            ram_total_mib=8192,
            ram_committed_mib=0,
            ram_reserve_mib=1024,
            disk_free_mib=100_000,
            inode_free=100_000,
            kvm_usable=True,
            architecture="x86_64",
        ),
        demand=HostDemand(vcpus=2, ram_mib=1024, disk_mib=1024),
        now=NOW,
        authority_epoch=EPOCH,
    )
    assert slots.slots == 2
