"""Tests for lib/guest_readiness.py — proving a guest's four dimensions.

The point is that every dimension is either PROVEN with evidence or left
`None`. A guest that cannot be proven must never be counted, and a probe that
failed must not be recorded as a negative fact.
"""
from __future__ import annotations

import subprocess

import pytest

from lib.guest_readiness import (
    ProbeResult,
    guest_observation_from_probe,
    parse_ssh_banner,
    probe_guest_readiness,
    tap_topology,
)

# Measured on the bench, 2026-11-03.
REAL_BANNER = "SSH-2.0-dropbear\r\n"


class TestSshBanner:
    def test_a_real_banner_proves_health(self):
        result = parse_ssh_banner(REAL_BANNER)
        assert result.value is True
        assert "dropbear" in result.detail

    def test_empty_output_is_unknown_not_unhealthy(self):
        assert parse_ssh_banner("").value is None
        assert parse_ssh_banner("   \n\n").value is None

    def test_a_non_ssh_answer_is_not_a_pass(self):
        result = parse_ssh_banner("HTTP/1.1 400 Bad Request\r\n")
        assert result.value is False

    def test_a_refusal_line_is_not_a_pass(self):
        result = parse_ssh_banner("Connection refused\r\n")
        assert result.value is False


class TestTapTopology:
    # Verbatim `bridge link show` lines from x280.
    FORWARDING = (
        "122: tg-poc-tap: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 "
        "master tg-poc-br state forwarding priority 32 cost 2"
    )
    OTHER_BRIDGE = (
        "9: tg-other: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 "
        "master br-something state forwarding priority 32 cost 2"
    )

    def _run(self, stdout: str, rc: int = 0):
        def runner(cmd, **k):
            return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")

        return runner

    def test_tap_mastered_by_the_expected_bridge_is_proven(self):
        result = tap_topology("tg-poc-tap", "tg-poc-br", run=self._run(self.FORWARDING))
        assert result.value is True
        assert "mastered by tg-poc-br" in result.detail

    def test_tap_on_another_bridge_is_refused(self):
        result = tap_topology("tg-other", "tg-poc-br", run=self._run(self.OTHER_BRIDGE))
        assert result.value is False

    def test_absent_tap_is_refused(self):
        result = tap_topology("tg-missing", "tg-poc-br", run=self._run(self.FORWARDING))
        assert result.value is False
        assert "not present" in result.detail

    def test_missing_bridge_command_is_unknown_not_false(self):
        result = tap_topology("tg-poc-tap", "tg-poc-br", run=self._run("", rc=127))
        assert result.value is None

    def test_raising_runner_is_unknown(self):
        def boom(cmd, **k):
            raise OSError("bridge not installed")

        assert tap_topology("tg-poc-tap", "tg-poc-br", run=boom).value is None


class TestProbeComposition:
    def test_all_four_proven(self):
        probe = probe_guest_readiness(
            address="10.99.99.1",
            tap="tg-poc-tap",
            bridge="tg-poc-br",
            lease_free=True,
            banner_reader=lambda h: REAL_BANNER,
            topology_reader=lambda t, b: ProbeResult(True, "ok"),
        )
        assert probe.all_proven

    def test_no_lease_source_leaves_lease_unknown_and_blocks_counting(self):
        probe = probe_guest_readiness(
            address="10.99.99.1",
            tap="tg-poc-tap",
            bridge="tg-poc-br",
            lease_free=None,
            banner_reader=lambda h: REAL_BANNER,
            topology_reader=lambda t, b: ProbeResult(True, "ok"),
        )
        assert probe.lease_free.value is None
        assert not probe.all_proven, "an unproven lease must block counting"

    def test_unreachable_guest_leaves_client_reach_unknown(self):
        probe = probe_guest_readiness(
            address="10.99.99.1",
            tap="tg-poc-tap",
            bridge="tg-poc-br",
            lease_free=True,
            banner_reader=lambda h: "",
            topology_reader=lambda t, b: ProbeResult(True, "ok"),
        )
        assert probe.health.value is None
        assert probe.client_reachable.value is None
        assert not probe.all_proven

    def test_missing_tap_declaration_is_unknown(self):
        probe = probe_guest_readiness(
            address="10.99.99.1", lease_free=True, banner_reader=lambda h: REAL_BANNER
        )
        assert probe.topology.value is None

    def test_observation_carries_every_proven_dimension(self):
        probe = probe_guest_readiness(
            address="10.99.99.1",
            tap="tg-poc-tap",
            bridge="tg-poc-br",
            lease_free=True,
            banner_reader=lambda h: REAL_BANNER,
            topology_reader=lambda t, b: ProbeResult(True, "ok"),
        )
        obs = guest_observation_from_probe(
            probe, fleet_id="f", resource_id="archy-vm", now=1.0, ttl_s=60, authority_epoch=1
        )
        assert obs.health_ok is True
        assert obs.topology_ok is True
        assert obs.lease_free is True
        assert obs.client_reachable is True


class TestLiveBenchGuest:
    def test_the_real_guest_answers_ssh(self):
        """Measured: 10.99.99.1 answers `SSH-2.0-dropbear`."""
        import socket

        try:
            with socket.create_connection(("10.99.99.1", 22), timeout=3) as s:
                banner = s.recv(128).decode(errors="replace")
        except OSError:
            pytest.skip("bench guest not reachable from this host")
        assert parse_ssh_banner(banner).value is True

    def test_the_real_tap_is_mastered_by_the_real_bridge(self):
        result = tap_topology("tg-poc-tap", "tg-poc-br")
        if result.value is None:
            pytest.skip("bridge tooling unavailable")
        assert result.value is True
