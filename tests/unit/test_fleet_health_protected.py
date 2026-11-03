"""A health probe must refuse the production gateway, structurally.

`config/protected_devices.yaml` names the GL-MT6000 as `mt6000-gateway`:
"production gateway; hermes must stay online". `lib/protected_devices.py` can
answer for it, and `lib/labgrid_venue.py` refuses to ACQUIRE it.

The health probe added in this PR does not acquire anything — it reads uptime,
a board model and a client list over a serial console. But it can also be
pointed at a console by an operator who has not read the runbook, and this
gateway is the machine that keeps the fleet online. The read-only guard is not
the point: the point is that the *tool* should have said no, not the reviewer
who happened to know which router was production.

These tests pin that the identity the probe reads is checked against the
protected list before the evidence is produced, and that a protected identity
can never reach capacity.
"""
from __future__ import annotations

import pytest

from lib.fleet_capacity import Reason, State
from lib.fleet_health import RouterProbe, router_health_from_probe, read_router_health

FLEET = "x280-bench"
PLACE = "gl-mt6000"
NOW = 1_800_000_000.0
TTL = 60.0
EPOCH = 3

# Verbatim from the production gateway, 2026-11-03.
MT6000_MODEL = "GL.iNet GL-MT6000"
MT6000_BANNER = (
    "==================\nNoDogSplash Status\n==================\n"
    "Version: 5.0.2\nUptime: 42m 47s\nGateway Name: TollGate-MT6000\n"
)
UPTIME = " 15:22:08 up 42 min,  load average: 0.10, 0.08, 0.05"


def _probe(**over) -> RouterProbe:
    base = dict(
        fleet_id=FLEET,
        resource_id=PLACE,
        system_text=UPTIME,
        board_text=MT6000_MODEL,
        portal_text=MT6000_BANNER,
        clients_text="",
        expected_identity=MT6000_MODEL,
        observer_mac="",
    )
    base.update(over)
    return RouterProbe(**base)  # type: ignore[arg-type]


class TestProducerChecksTheProtectedList:
    def test_the_gateways_model_is_recognised_as_protected(self):
        obs = router_health_from_probe(_probe(), now=NOW, ttl_s=TTL, authority_epoch=EPOCH)
        assert obs.protected is True, (
            "the producer read GL.iNet GL-MT6000 and did not flag it as the protected "
            "production gateway"
        )

    def test_a_protected_identity_cannot_be_health_ok(self):
        """Defence in depth: even if a caller ignored `protected`, health is False."""
        obs = router_health_from_probe(_probe(), now=NOW, ttl_s=TTL, authority_epoch=EPOCH)
        assert obs.health_ok is not True

    def test_a_bench_router_is_not_flagged(self):
        obs = router_health_from_probe(
            _probe(board_text="GL.iNet GL-MT3000", expected_identity="GL.iNet GL-MT3000"),
            now=NOW, ttl_s=TTL, authority_epoch=EPOCH,
        )
        assert obs.protected is False

    def test_an_email_style_identity_is_not_protected(self):
        """`is_protected` normalises by stripping a domain; that must not make a
        bench board look like the gateway."""
        obs = router_health_from_probe(
            _probe(board_text="soho.router", expected_identity="soho.router"),
            now=NOW, ttl_s=TTL, authority_epoch=EPOCH,
        )
        assert obs.protected is False


class TestAdapterRefusesBeforeReading:
    def test_a_protected_place_is_refused_before_any_command_runs(self):
        """The refusal must not depend on the board answering first."""
        ran: list[str] = []

        def runner(req):
            ran.append(req.command)
            return req

        probe = read_router_health(
            FLEET, PLACE, run=runner, expected_identity=MT6000_MODEL
        )
        assert ran == [], "a protected target must not be probed at all"
        assert "protected" in (probe.read_errors.get("protected") or "").lower()

    def test_a_bench_board_proceeds_normally(self):
        ran: list[str] = []

        def runner(req):
            ran.append(req.command)
            return req

        read_router_health(FLEET, "gl-mt3000", run=runner, expected_identity="GL.iNet GL-MT3000")
        assert ran, "an unprotected target must still be probed"


class TestProtectedEvidenceIsNeverCapacity:
    def test_slice1_refuses_protected_health_evidence(self):
        from lib.fleet_capacity import (
            Observations,
            coordinator_observation,
            exporter_observation,
            parse_manifest,
            router_capacity,
        )

        manifest = parse_manifest(
            {
                "schema_version": 1,
                "fleets": [
                    {
                        "id": FLEET,
                        "coordinator": {"address": "x280.fips:20408"},
                        "host_controller": {"endpoint": "x280:8099"},
                        "allowed_places": [PLACE],
                        "permitted_actions": ["observe"],
                    }
                ],
            },
            source="<test>",
        )
        obs = router_health_from_probe(_probe(), now=NOW, ttl_s=TTL, authority_epoch=EPOCH)
        verdict = router_capacity(
            manifest, PLACE, now=NOW, authority_epoch=EPOCH,
            observations=Observations(
                coordinator=coordinator_observation(
                    fleet_id=FLEET, reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
                ),
                exporter=exporter_observation(
                    fleet_id=FLEET, resource_id=PLACE, exporter_connected=True,
                    now=NOW, ttl_s=TTL, authority_epoch=EPOCH,
                ),
                health=obs,
            ),
        )
        assert verdict.state is State.REFUSED
        assert Reason.PROTECTED in verdict.reasons
        assert verdict.capacity == 0
