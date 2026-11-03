"""Unit tests for lib/fleet_health.py — the producer of router health evidence.

Slice 1 (``lib/fleet_capacity.py``) decides what evidence supports; slice 4
adapters produce coordinator/host/guest evidence. Nothing produced
``RouterHealthObservation``, so every exported router could only ever be
``EXPORTED_AVAILABLE, capacity=0``. These tests pin the producer's honesty
rules, using text fixtures — no router, no console, no network.

The rules that would make this producer dangerous are each pinned here:

* a read that failed is not a pass (``telemetry_read_ok=False``);
* "there are clients" is not "somebody is paying";
* an authenticated/trusted tester is BYPASSED by design, so its vantage is not
  a customer vantage (measured 2026-10-21, retracted-evidence incident);
* an unknown observer MAC is UNKNOWN, never True.
"""
from __future__ import annotations

import pytest

from lib.fleet_capacity import Reason, State
from lib.fleet_health import (
    CLIENT_STATE_AUTHENTICATED,
    CLIENT_STATE_PREAUTHENTICATED,
    CLIENT_STATE_TRUSTED,
    RouterProbe,
    parse_ndsctl_clients,
    parse_uptime_seconds,
    router_health_from_probe,
)

FLEET = "x280-bench"
PLACE = "gl-mt3000"
OBSERVER = "aa:bb:cc:dd:ee:01"
NOW = 1_800_000_000.0
TTL = 60.0
EPOCH = 7


def _clients(*blocks: tuple[str, str, str]) -> str:
    """Build ndsctl-shaped output from (mac, ip, state) triples."""
    lines = []
    for index, (mac, ip, state) in enumerate(blocks):
        lines += [
            f"Client {index}",
            "\tMAC: " + mac,
            "\tIP: " + ip,
            "\tPackets: 12",
            "\tState: " + state,
        ]
    return "\n".join(lines) + "\n"


def _probe(**overrides) -> RouterProbe:
    base = dict(
        fleet_id=FLEET,
        resource_id=PLACE,
        system_text=" 15:22:08 up 13:03,  load average: 0.21, 0.11, 0.06",
        board_text="GL.iNet GL-MT3000",
        clients_text=_clients((OBSERVER, "192.168.50.240", CLIENT_STATE_PREAUTHENTICATED)),
        expected_identity="GL.iNet GL-MT3000",
        observer_mac=OBSERVER,
    )
    base.update(overrides)
    return RouterProbe(**base)  # type: ignore[arg-type]


def _decide(probe: RouterProbe):
    return router_health_from_probe(probe, now=NOW, ttl_s=TTL, authority_epoch=EPOCH)


# ===========================================================================
# 1. ndsctl clients parsing
# ===========================================================================
class TestParseNdsctlClients:
    def test_blocks_are_matched_by_field_name_not_position(self):
        text = (
            "Client 0\n"
            "\tIP: 192.168.50.240\n"  # order inverted vs the usual dump
            "\tPackets: 3\n"
            "\tMAC: aa:bb:cc:dd:ee:01\n"
            "\tState: Preauthenticated\n"
        )
        read = parse_ndsctl_clients(text)
        assert read.read_ok
        assert read.clients[0].mac == "aa:bb:cc:dd:ee:01"
        assert read.clients[0].ip == "192.168.50.240"
        assert read.clients[0].state == CLIENT_STATE_PREAUTHENTICATED

    def test_empty_output_is_not_a_confident_empty_list(self):
        """A portal that answered nothing may not be running at all."""
        for text in ("", "   \n\n", "ndsctl: not running\n"):
            read = parse_ndsctl_clients(text)
            assert not read.read_ok, text
            assert read.clients == ()

    def test_zero_clients_on_a_live_portal_is_still_unreadable(self):
        """Measured 2026-11-03: with no clients, nodogsplash prints nothing.

        A silent success and a stopped portal produce the same empty text, so
        neither may be read as "no paying customers". The state stays UNKNOWN.
        """
        read = parse_ndsctl_clients("")
        assert not read.read_ok
        assert "no client blocks" in read.detail
        obs = _decide(_probe(clients_text=""))
        assert obs.session_active is None

    def test_a_stopped_portal_says_so_in_the_detail(self):
        read = parse_ndsctl_clients("/etc/init.d/nodogsplash: ndsctl: not running\n")
        assert not read.read_ok
        assert "not running" in read.detail

    def test_a_live_portal_with_no_clients_is_proven_idle(self):
        """The status banner is positive proof the portal is running.

        Measured 2026-11-03: ``ndsctl clients`` prints NOTHING when there are no
        clients, so a bare empty list cannot be told from a stopped portal. The
        probe runs ``ndsctl status`` first; its banner settles it.
        """
        banner = (
            "==================\n"
            "NoDogSplash Status\n"
            "==================\n"
            "Version: 5.0.2\n"
            "Uptime: 13h 25m 23s\n"
            "Managed interface: br-tollgate\n"
        )
        read = parse_ndsctl_clients(banner + "\n")
        assert read.read_ok
        assert read.clients == ()
        assert read.paid_sessions == ()
        assert "0 client" in read.detail

    def test_banner_plus_clients_still_parses_the_clients(self):
        banner = "NoDogSplash Status\nVersion: 5.0.2\n\n"
        text = banner + _clients((OBSERVER, "192.168.50.240", CLIENT_STATE_PREAUTHENTICATED))
        read = parse_ndsctl_clients(text)
        assert read.read_ok
        assert read.find(OBSERVER) is not None

    def test_paid_sessions_counts_only_authenticated(self):
        read = parse_ndsctl_clients(
            _clients(
                (OBSERVER, "192.168.50.240", CLIENT_STATE_PREAUTHENTICATED),
                ("aa:bb:cc:dd:ee:02", "192.168.50.241", CLIENT_STATE_AUTHENTICATED),
                ("aa:bb:cc:dd:ee:03", "192.168.50.242", CLIENT_STATE_TRUSTED),
            )
        )
        assert [c.mac for c in read.paid_sessions] == ["aa:bb:cc:dd:ee:02"]

    def test_find_normalises_mac_spelling(self):
        read = parse_ndsctl_clients(_clients((OBSERVER, "192.168.50.240", CLIENT_STATE_PREAUTHENTICATED)))
        assert read.find("AA-BB-CC-DD-EE-01") is not None
        assert read.find("aa:bb:cc:dd:ee:99") is None


# ===========================================================================
# 2. uptime parsing — liveness proof, not decoration
# ===========================================================================
class TestParseUptime:
    @pytest.mark.parametrize(
        "text,seconds",
        [
            (" 15:22:08 up 13:03,  load average: 0.21", 13 * 3600 + 3 * 60),
            (" 15:22:08 up 2 days,  4:07,  load average: 0.0", 2 * 86400 + 4 * 3600 + 7 * 60),
            (" 15:22:08 up 41 min,  load average: 0.0", 41 * 60),
        ],
    )
    def test_parses_uptime_shapes(self, text, seconds):
        assert parse_uptime_seconds(text) == seconds

    @pytest.mark.parametrize("text", ["", "ash: uptime: not found", "Connection closed"])
    def test_unparseable_is_none_not_zero(self, text):
        assert parse_uptime_seconds(text) is None


# ===========================================================================
# 3. The honest healthy-idle verdict
# ===========================================================================
class TestHealthyIdle:
    def test_clean_router_is_healthy_idle(self):
        obs = _decide(_probe())
        assert obs.health_ok is True
        assert obs.session_active is False
        assert obs.vantage_ok is True
        assert obs.telemetry_read_ok is True

    def test_identity_from_board_name_matches(self):
        obs = _decide(_probe(board_text="glinet,mt3000\n", expected_identity="glinet,mt3000"))
        assert obs.health_ok is True
        assert obs.identity == "glinet,mt3000"


# ===========================================================================
# 4. Every failure is a refusal, never a silent pass
# ===========================================================================
class TestRefusals:
    def test_failed_system_read_is_telemetry_unreadable(self):
        obs = _decide(_probe(system_text="", read_errors={"system": "console timeout"}))
        assert obs.telemetry_read_ok is False
        assert obs.health_ok is not True

    def test_unparseable_uptime_is_not_health(self):
        obs = _decide(_probe(system_text="garbage output"))
        assert obs.health_ok is False
        assert obs.telemetry_read_ok is True

    def test_identity_mismatch_is_not_healthy(self):
        obs = _decide(_probe(board_text="dlink,covr-x1860-a1", expected_identity="glinet,mt3000"))
        assert obs.health_ok is False
        assert obs.identity != obs.expected_identity

    def test_unreadable_client_list_leaves_session_state_unknown(self):
        """A dead portal must not be read as "no paying customers"."""
        obs = _decide(_probe(clients_text=""))
        assert obs.session_active is None

    def test_a_paying_client_makes_the_router_busy(self):
        obs = _decide(
            _probe(
                clients_text=_clients(
                    (OBSERVER, "192.168.50.240", CLIENT_STATE_PREAUTHENTICATED),
                    ("aa:bb:cc:dd:ee:02", "192.168.50.241", CLIENT_STATE_AUTHENTICATED),
                )
            )
        )
        assert obs.session_active is True


# ===========================================================================
# 5. Vantage is about the TESTER (the retracted-evidence lesson)
# ===========================================================================
class TestVantage:
    def test_stranger_tester_has_a_customer_vantage(self):
        assert _decide(_probe()).vantage_ok is True

    def test_authenticated_tester_is_bypassed_so_vantage_fails(self):
        obs = _decide(
            _probe(clients_text=_clients((OBSERVER, "192.168.50.240", CLIENT_STATE_AUTHENTICATED)))
        )
        assert obs.vantage_ok is False

    def test_trusted_tester_is_exempt_by_design_so_vantage_fails(self):
        obs = _decide(_probe(clients_text=_clients((OBSERVER, "192.168.50.240", CLIENT_STATE_TRUSTED)))
        )
        assert obs.vantage_ok is False

    def test_observer_not_in_the_client_list_is_unknown_not_true(self):
        obs = _decide(
            _probe(
                clients_text=_clients(
                    ("aa:bb:cc:dd:ee:02", "192.168.50.241", CLIENT_STATE_PREAUTHENTICATED)
                )
            )
        )
        assert obs.vantage_ok is None

    def test_no_observer_mac_configured_is_unknown(self):
        assert _decide(_probe(observer_mac="")).vantage_ok is None

    def test_unreadable_client_list_is_unknown_vantage(self):
        assert _decide(_probe(clients_text="")).vantage_ok is None


# ===========================================================================
# 6. The evidence reaches the slice-1 decision unchanged
# ===========================================================================
class TestFeedsTheCapacityDecision:
    def test_healthy_clean_probe_is_countable_once_admitted_by_slice1(self):
        from lib.fleet_capacity import Observations, parse_manifest, router_capacity

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
        obs = _decide(_probe())
        from lib.fleet_capacity import coordinator_observation, exporter_observation

        verdict = router_capacity(
            manifest,
            PLACE,
            now=NOW,
            authority_epoch=EPOCH,
            observations=Observations(
                coordinator=coordinator_observation(
                    fleet_id=FLEET, reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
                ),
                exporter=exporter_observation(
                    fleet_id=FLEET,
                    resource_id=PLACE,
                    exporter_connected=True,
                    now=NOW,
                    ttl_s=TTL,
                    authority_epoch=EPOCH,
                ),
                health=obs,
            ),
        )
        assert verdict.state is State.HEALTHY_IDLE
        assert verdict.capacity == 1

    def test_busy_router_is_refused_with_the_session_reason(self):
        from lib.fleet_capacity import Observations, parse_manifest, router_capacity

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
        from lib.fleet_capacity import coordinator_observation, exporter_observation

        busy = _decide(
            _probe(
                clients_text=_clients(
                    (OBSERVER, "192.168.50.240", CLIENT_STATE_PREAUTHENTICATED),
                    ("aa:bb:cc:dd:ee:02", "192.168.50.241", CLIENT_STATE_AUTHENTICATED),
                )
            )
        )
        verdict = router_capacity(
            manifest,
            PLACE,
            now=NOW,
            authority_epoch=EPOCH,
            observations=Observations(
                coordinator=coordinator_observation(
                    fleet_id=FLEET, reachable=True, now=NOW, ttl_s=TTL, authority_epoch=EPOCH
                ),
                exporter=exporter_observation(
                    fleet_id=FLEET,
                    resource_id=PLACE,
                    exporter_connected=True,
                    now=NOW,
                    ttl_s=TTL,
                    authority_epoch=EPOCH,
                ),
                health=busy,
            ),
        )
        assert verdict.state is State.REFUSED
        assert Reason.SESSION_ACTIVE in verdict.reasons


# ===========================================================================
# 8. Transport noise from the serial wrapper is not router output
# ===========================================================================
class TestSerialNoiseStripping:
    """``router-serial.py`` echoes the command and prints ``__SERIAL_END___:<rc>``.

    Measured captures (2026-11-03, GL-MT3000 over FTDI BG046H5F) included a bare
    ``$?"`` line before the real output and a sentinel line carrying the exit
    status. A parser that takes "line 0" then compares ``$?"`` against a board
    name and reports an identity mismatch — a refusal caused by the transport,
    not by the router.
    """

    def test_bare_status_echo_is_dropped_but_output_kept(self):
        from lib.fleet_health import _clean_lines

        assert _clean_lines('$?"\n15:27:58 up 13:09,  load average: 0.21') == [
            "15:27:58 up 13:09,  load average: 0.21"
        ]

    def test_sentinel_only_capture_yields_nothing(self):
        from lib.fleet_health import _clean_lines

        assert _clean_lines("___SERIAL_END___:0\n0") == []

    def test_sentinel_before_identity_does_not_become_the_identity(self):
        from lib.fleet_health import _clean_lines

        assert _clean_lines('__SERIAL_END___:$?"\nGL.iNet GL-MT3000\n') == ["GL.iNet GL-MT3000"]

    def test_a_real_identity_is_untouched(self):
        from lib.fleet_health import _clean_lines

        assert _clean_lines("glinet,mt3000\n") == ["glinet,mt3000"]
        assert _clean_lines("GL.iNet GL-MT3000") == ["GL.iNet GL-MT3000"]

    def test_captured_noise_does_not_cause_an_identity_mismatch(self):
        obs = _decide(_probe(board_text='__SERIAL_END___:$?"\nglinet,mt3000\n', expected_identity="glinet,mt3000"))
        assert obs.identity == "glinet,mt3000"
        assert obs.health_ok is True

    def test_uptime_behind_a_status_echo_is_still_read(self):
        obs = _decide(_probe(system_text='$?"\n15:27:58 up 13:09,  load average: 0.21'))
        assert obs.health_ok is True


# ===========================================================================
# 7. The adapter issues only the fixed read-only command set
# ===========================================================================
class TestReadRouterHealth:
    def test_issues_exactly_the_fixed_commands(self):
        from lib.fleet_health import HEALTH_COMMANDS, read_router_health

        seen: list[str] = []

        def runner(req):
            seen.append(req.command)
            return req

        read_router_health(FLEET, PLACE, run=runner)
        assert seen == [command for _, command in HEALTH_COMMANDS]
        assert seen  # non-empty, so the assertion above is not vacuously true
        assert all("ndsctl auth" not in c and "reboot" not in c for c in seen)

    def test_a_timed_out_read_is_an_error_not_empty_output(self):
        from lib.fleet_health import read_router_health

        def runner(req):
            if req.name == "clients":
                from dataclasses import replace

                return replace(req, timed_out=True, stdout="")
            return req

        probe = read_router_health(FLEET, PLACE, run=runner)
        assert "clients" in probe.read_errors
        assert "timed out" in probe.read_errors["clients"]
        obs = _decide(probe)
        # The dead portal must not read as "no paying customers".
        assert obs.session_active is None
        assert obs.telemetry_read_ok is False

    def test_stdout_is_collected_verbatim(self):
        from dataclasses import replace

        from lib.fleet_health import HEALTH_COMMANDS, read_router_health

        outputs = {
            "system": " 15:22:08 up 13:03,  load average: 0.21",
            "board": "GL.iNet GL-MT3000\n",
            "portal": "NoDogSplash Status\nVersion: 5.0.2\n",
            "clients": _clients((OBSERVER, "192.168.50.240", CLIENT_STATE_PREAUTHENTICATED)),
        }
        assert set(outputs) == {name for name, _ in HEALTH_COMMANDS}

        def runner(req):
            return replace(req, stdout=outputs[req.name], returncode=0)

        probe = read_router_health(
            FLEET, PLACE, run=runner, expected_identity="GL.iNet GL-MT3000", observer_mac=OBSERVER
        )
        obs = _decide(probe)
        assert obs.identity == "GL.iNet GL-MT3000"
        assert obs.health_ok is True
        assert obs.session_active is False
        assert obs.vantage_ok is True
        assert obs.telemetry_read_ok is True

    def test_the_fixed_commands_carry_no_breaking_redirect(self):
        """``2>/dev/null`` deterministically empties the wrapper's capture.

        Measured 2026-11-03, GL-MT3000 over FTDI BG046H5F: 5/5 empty with the
        redirect, 5/5 correct without it, alternating on one console. ``2>&1``
        is fine. An empty capture looks like a quiet router, so the command set
        must not contain the breaking spelling.
        """
        from lib.fleet_health import HEALTH_COMMANDS

        for name, command in HEALTH_COMMANDS:
            assert "2>&1" not in command.replace(" ", ""), (name, command)
            assert "2>/dev/null" not in command.replace(" ", ""), (name, command)

    def test_a_breaking_redirect_is_refused_before_any_run(self):
        from lib.fleet_health import _FORBIDDEN_REDIRECTS, read_router_health
        import lib.fleet_health as fh

        saved = fh.HEALTH_COMMANDS
        try:
            fh.HEALTH_COMMANDS = (("board", "cat /tmp/sysinfo/model 2>/dev/null"),)
            ran: list[str] = []

            def runner(req):
                ran.append(req.command)
                return req

            probe = read_router_health(FLEET, PLACE, run=runner)
            assert ran == [], "a refused command must not reach the transport"
            assert "board" in probe.read_errors
            assert "redirect" in probe.read_errors["board"]
        finally:
            fh.HEALTH_COMMANDS = saved
        assert _FORBIDDEN_REDIRECTS

    def test_nonzero_exit_is_an_error_and_health_is_unknown(self):
        from dataclasses import replace

        from lib.fleet_health import read_router_health

        def runner(req):
            if req.name == "system":
                return replace(req, returncode=127, stderr="ash: uptime: not found")
            return req

        obs = _decide(read_router_health(FLEET, PLACE, run=runner))
        assert obs.telemetry_read_ok is False
        assert obs.health_ok is None


# --------------------------------------------------------------------------- #
# Captured from real hardware
# --------------------------------------------------------------------------- #
#: Exact bytes from a GL-MT6000 over /dev/ttyUSB0, 2026-11-03. With zero
#: clients ``ndsctl clients`` prints nothing; the shell wrapper printed a bare
#: "0" (its own exit status). Only ``ndsctl status`` proves the portal is up.
_MT6000_NDSCTL_STATUS = """==================
NoDogSplash Status
====
Version: 5.0.2
Uptime: 42m 47s
Gateway Name: TollGate-MT6000"""


def test_real_mt6000_silent_client_list_is_zero_clients_not_unreadable():
    """Captured on hardware: silence + a running-portal banner means 0 clients."""
    result = parse_ndsctl_clients("0", portal_banner=_MT6000_NDSCTL_STATUS)
    assert result.read_ok is True
    assert result.paid_sessions == ()
    assert "0 client" in result.detail


def test_real_mt6000_silence_without_the_banner_stays_ambiguous():
    """The dangerous direction: a silent read is not evidence of zero clients."""
    result = parse_ndsctl_clients("0")
    assert result.read_ok is False, "silence must not be reported as 'no paying customers'"


def test_real_mt6000_status_banner_is_recognised_as_a_running_portal():
    """The banner is the positive proof; it must never be treated as a client."""
    result = parse_ndsctl_clients("", portal_banner=_MT6000_NDSCTL_STATUS)
    assert result.read_ok is True
    assert result.clients == ()
