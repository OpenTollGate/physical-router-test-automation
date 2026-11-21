"""The bench probe must refuse a protected device BEFORE touching it.

Incident (2026-11-03): the bench probe walked every unit in
`configs/labgrid/x280-serial-fleet.json`. Two of those units turned out to be
production hardware — the GL-MT6000 gateway and the GL-E750 — and the probe's
own docstring describes what it does to each:

    "the console answers on its FTDI by-id port (wake, login if needed, ...)"

`wake_shell` writes a newline and, if it sees a login prompt, sends `root`.
`probe_ssh` authenticates with the bench key. Neither is read-only in the sense
that matters: both put bytes on a production device and establish access to it.

`lib.protected_devices.assert_console_not_protected` can refuse a cable by FTDI
serial without opening it. These tests require the probe to use it — for the
console AND for the SSH leg — and to report the refusal rather than skip quietly.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts" / "bench" / "serial-fleet-probe.py"

GATEWAY_ADAPTER = "BG02QAPG"
E750_ADAPTER = "BH00DL3M"
BENCH_ADAPTER = "BG046H5F"


def _load_probe():
    spec = importlib.util.spec_from_file_location("serial_fleet_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unit(adapter: str, **over) -> dict:
    base = {
        "name": f"unit-{adapter}",
        "enrolled": True,
        "ftdi_serial": adapter,
        "baud": 115200,
        "ssh": {"address": "192.0.2.10", "username": "root"},
        "identity": {"model": "GL.iNet GL-MT3000", "distro": "OpenWrt", "arch": "aarch64_cortex-a53", "target": "mediatek/filogic"},
    }
    base.update(over)
    return base


class TestConsoleLegRefusesBeforeOpening:
    def test_protected_adapter_is_refused_without_opening_the_port(self, monkeypatch):
        probe = _load_probe()
        opened: list[str] = []

        class Boom:
            def __init__(self, *a, **k):
                opened.append(a[0] if a else "?")

            def __getattr__(self, name):
                raise AssertionError(f"serial port was used despite protection: {name}")

        monkeypatch.setattr(probe.serial, "Serial", Boom)
        result = probe.probe_console(_unit(GATEWAY_ADAPTER))

        assert opened == [], "a protected adapter's port must never be opened"
        assert result["error"], "the refusal must be reported, not silently skipped"
        assert "protected" in result["error"].lower()
        assert result.get("protected") is True

    def test_e750_adapter_is_refused_too(self, monkeypatch):
        probe = _load_probe()
        opened: list[str] = []

        class Boom:
            def __init__(self, *a, **k):
                opened.append(a[0] if a else "?")

            def __getattr__(self, name):
                raise AssertionError(f"serial port was used despite protection: {name}")

        monkeypatch.setattr(probe.serial, "Serial", Boom)
        result = probe.probe_console(_unit(E750_ADAPTER))
        assert opened == []
        assert result.get("protected") is True

    def test_bench_adapter_still_proceeds(self, monkeypatch):
        probe = _load_probe()
        opened: list[str] = []

        class FakeSerial:
            def __init__(self, *a, **k):
                opened.append(a[0] if a else "?")

            def close(self):
                pass

        monkeypatch.setattr(probe.serial, "Serial", FakeSerial)
        monkeypatch.setattr(probe, "wake_shell", lambda ser: True)
        monkeypatch.setattr(probe, "console_exec", lambda ser, cmd, timeout=25.0: ["ok"])
        result = probe.probe_console(_unit(BENCH_ADAPTER))
        assert opened, "an unprotected adapter must still be probed"
        assert result.get("protected") is not True


class TestSshLegRefusesBeforeConnecting:
    def test_protected_unit_is_not_sshed(self, monkeypatch):
        probe = _load_probe()
        calls: list[list[str]] = []
        monkeypatch.setattr(
            probe.subprocess, "run", lambda cmd, **k: calls.append(list(cmd))
        )
        result = probe.probe_ssh(_unit(GATEWAY_ADAPTER, ftdi_serial=GATEWAY_ADAPTER), Path("/nonexistent"))

        assert calls == [], "ssh must not be attempted against a protected device"
        assert result["error"]
        assert "protected" in result["error"].lower()

    def test_bench_unit_ssh_still_proceeds(self, monkeypatch):
        probe = _load_probe()
        calls: list[list[str]] = []

        class Done:
            returncode = 0
            stdout = "GL.iNet GL-MT3000\n"
            stderr = ""

        def fake_run(cmd, **k):
            calls.append(list(cmd))
            return Done()

        monkeypatch.setattr(probe.subprocess, "run", fake_run)
        probe.probe_ssh(_unit(BENCH_ADAPTER), Path("/nonexistent"))
        assert calls, "an unprotected unit must still be reachable over ssh"


class TestTheContractItWalks:
    def test_no_enrolled_contract_unit_is_protected(self):
        """Belt and braces: the probe should never have such a unit to walk."""
        from lib.protected_devices import match_protected

        contract = ROOT / "configs" / "labgrid" / "x280-serial-fleet.json"
        units = json.loads(contract.read_text())["units"]
        bad = [
            u["name"]
            for u in units
            if u.get("enrolled", True) and u.get("ftdi_serial")
            and match_protected(serial_adapters=[u["ftdi_serial"]])
        ]
        assert bad == [], f"enrolled protected units in the contract: {bad}"
