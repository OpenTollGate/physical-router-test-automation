"""The serial transport must refuse protected devices by identity.

``config/protected_devices.yaml`` lists the MT6000 gateway as protected —
"production gateway; hermes must stay online" — and ``lib/protected_devices``
implements a fail-closed matcher for it. But nothing wired that guard into the
paths an operator or a test actually uses: ``router-serial.py``,
``lib/fleet_health`` and ``scripts/fleet-capacity.py`` never called it.

Measured consequence: a harness was pointed at the production gateway over
/dev/ttyUSB0 and read its identity freely. The reads were non-mutating, but
"we only read it" is not a safety property — the same code path runs flashing,
state prep and captive-portal manipulation.

These tests pin the guard at the transport, which is the earliest point an
identity is available and the only point every serial user must pass through.

The identity asserted here is the one the real device reports:

    cat /tmp/sysinfo/model      -> GL.iNet GL-MT6000
    cat /tmp/sysinfo/board_name -> glinet,gl-mt6000
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from lib.protected_devices import ProtectedDeviceError, assert_not_protected, is_protected

ROOT = Path(__file__).resolve().parents[2]


def test_the_protected_list_actually_contains_the_gateway():
    """If this drifts, every guard below silently stops protecting anything."""
    assert is_protected(board_name="glinet,gl-mt6000")
    assert is_protected(model="GL.iNet GL-MT6000") or is_protected(model="GL-MT6000")


def test_the_real_board_identity_is_refused():
    with pytest.raises(ProtectedDeviceError) as exc:
        assert_not_protected(board_name="glinet,gl-mt6000")
    assert "production gateway" in str(exc.value)


def test_a_lab_device_identity_is_allowed():
    """The guard must not refuse everything, or it would just be a dead path."""
    assert_not_protected(board_name="glinet,gl-mt300n-v2", macs=["02:00:00:00:00:01"])


def test_managed_hosts_are_refused():
    with pytest.raises(ProtectedDeviceError):
        assert_not_protected(hostname="x280")
    with pytest.raises(ProtectedDeviceError):
        assert_not_protected(hostname="cobrador")


def test_an_unknown_identity_is_not_refused_but_is_also_not_proof():
    """Fail-closed on *matching*; absence of a match is not a certification."""
    assert is_protected(board_name="totally-unknown-board") is False


def test_the_serial_transport_exposes_the_guard():
    """router-serial must be able to check identity before running a command."""
    spec = importlib.util.spec_from_file_location("router_serial", ROOT / "scripts/router-serial.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "assert_not_protected"), (
        "router-serial.py does not import the protected-device guard, so a test "
        "harness can drive the production gateway over serial"
    )
