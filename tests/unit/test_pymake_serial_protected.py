"""`serial-recovery` runs an ARBITRARY command as root. Guard the cable.

`scripts/pymake.py:_run_serial_ops` takes `TOLLGATE_SERIAL_PORT` from the
environment and, for the `serial-recovery` target, executes `args.cmd` — any
string the caller supplies — over that console:

    make serial-recovery CMD='wifi reload'

The port is operator-supplied and unvalidated, and before this change nothing
asked *which device* was on the other end. That is the highest-consequence
console path in the repo: it is explicitly the "emergency" one, so it will be
reached for when a device is misbehaving — possibly the production gateway
someone is trying to fix at the time.

The guard refuses by the CABLE's identity, before the command is sent.
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYMAKE = ROOT / "scripts" / "pymake.py"

GATEWAY_BY_ID = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02QAPG-if00-port0"
E750_BY_ID = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BH00DL3M-if00-port0"
BENCH_BY_ID = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG046H5F-if00-port0"


def _load():
    spec = importlib.util.spec_from_file_location("pymake_under_test", PYMAKE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _args(cmd: str | None = "uname -a") -> argparse.Namespace:
    return argparse.Namespace(cmd=cmd)


@pytest.fixture()
def pymake(monkeypatch):
    mod = _load()
    sent: list[str] = []

    class FakeConsole:
        def __init__(self, port, *a, **k):
            self.port = port

        def exec_command(self, command, timeout=30):
            sent.append(command)
            return "ok"

        def interactive_shell(self):
            sent.append("<shell>")
            return 0

    monkeypatch.setattr(mod, "_sent_commands", sent, raising=False)

    import lib.serial_console as sc

    monkeypatch.setattr(sc, "SerialConsole", FakeConsole)
    return mod, sent


class TestRecoveryRefusesProtectedCables:
    def test_gateway_cable_is_refused_before_any_command(self, pymake, monkeypatch):
        mod, sent = pymake
        monkeypatch.setenv("TOLLGATE_SERIAL_PORT", GATEWAY_BY_ID)
        with pytest.raises(SystemExit) as err:
            mod._run_serial_ops("serial-recovery", _args("reboot"))
        assert err.value.code not in (0, None)
        assert sent == [], f"a command reached a protected device: {sent}"

    def test_e750_cable_is_refused(self, pymake, monkeypatch):
        mod, sent = pymake
        monkeypatch.setenv("TOLLGATE_SERIAL_PORT", E750_BY_ID)
        with pytest.raises(SystemExit):
            mod._run_serial_ops("serial-recovery", _args("reboot"))
        assert sent == []

    def test_shell_refuses_too(self, pymake, monkeypatch):
        """An interactive root shell is the most dangerous form, not the least."""
        mod, sent = pymake
        monkeypatch.setenv("TOLLGATE_SERIAL_PORT", GATEWAY_BY_ID)
        with pytest.raises(SystemExit):
            mod._run_serial_ops("serial-shell", _args(None))
        assert sent == []

    def test_bench_cable_still_runs_the_command(self, pymake, monkeypatch):
        mod, sent = pymake
        monkeypatch.setenv("TOLLGATE_SERIAL_PORT", BENCH_BY_ID)
        assert mod._run_serial_ops("serial-recovery", _args("uname -a")) == 0
        assert sent == ["uname -a"]

    def test_unidentifiable_cable_fails_closed(self, pymake, monkeypatch):
        mod, sent = pymake
        monkeypatch.setenv("TOLLGATE_SERIAL_PORT", "/dev/ttyUSB99")
        monkeypatch.setattr(
            "lib.protected_devices._udev_serial_short", lambda p: None
        )
        with pytest.raises(SystemExit):
            mod._run_serial_ops("serial-recovery", _args("reboot"))
        assert sent == []
