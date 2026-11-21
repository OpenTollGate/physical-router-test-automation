"""The bench contract must never enroll a protected device.

Incident sequence (2026-11-03), both found by a human, not by the tests:

1. the production **GL-MT6000 gateway** was probed as if it were a bench board;
2. the production **GL-E750** (`BH00DL3M`) sits in `configs/labgrid/x280-serial-fleet.json`
   as `bench-e750-lab`, `enrolled: true` — a *test target* in the contract.

The protected list and the bench contract were two independent files that nothing
cross-checked, so a device could be named "must never be used as a test target" in
one and "enrolled bench unit" in the other. These tests join them.

The E750's contract entry also claims the console is merely "electrically silent"
and "unverified until the cable/port is physically checked" — which invites exactly
the wrong next step: someone checking that cable. A production router's console is
not a thing to go and verify.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.protected_devices import is_protected, load_protected, match_protected

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "configs" / "labgrid" / "x280-serial-fleet.json"

# Measured on x280, 2026-11-03.
E750_ADAPTER = "BH00DL3M"
GATEWAY_ADAPTER = "BG02QAPG"


def _units() -> list[dict]:
    return json.loads(CONTRACT.read_text())["units"]


class TestTheTwoProductionAdaptersAreRecorded:
    def test_e750_adapter_is_protected(self):
        assert is_protected(serial_adapters=[E750_ADAPTER]), (
            "the E750's console adapter is not in the protected list"
        )

    def test_gateway_adapter_is_still_protected(self):
        assert is_protected(serial_adapters=[GATEWAY_ADAPTER])

    def test_e750_is_identified_by_its_own_names_too(self):
        assert is_protected(model="GL.iNet GL-E750")
        assert is_protected(board_name="glinet,gl-e750")


class TestNoBenchUnitIsProtected:
    def test_no_contract_unit_carries_a_protected_adapter(self):
        """The cross-check that would have caught both incidents.

        A protected device may be RETAINED in the contract as a record — that is
        how its adapter stays documented so nobody re-enrols it as generic bench
        kit — but it must never be `enrolled: true`. An enrolled protected unit
        is the actual defect: it is a test target.
        """
        offenders = []
        for unit in _units():
            serial = unit.get("ftdi_serial")
            if not serial:
                continue
            row = match_protected(serial_adapters=[serial])
            if row and unit.get("enrolled", True):
                offenders.append(f"{unit['name']} -> {serial} ({row.get('id')})")
        assert not offenders, (
            "the bench contract ENROLLS protected adapters: " + "; ".join(offenders)
        )

    def test_a_retained_protected_record_says_so(self):
        for unit in _units():
            serial = unit.get("ftdi_serial")
            if not serial:
                continue
            row = match_protected(serial_adapters=[serial])
            if row:
                assert unit.get("protected") is True, (
                    f"{unit['name']} carries protected adapter {serial} but is not "
                    "marked protected"
                )

    def test_the_e750_is_not_a_dark_bench_unit_with_an_invitation_to_check_it(self):
        """A production router must not be described as 'unverified, go check the cable'."""
        for unit in _units():
            if unit.get("ftdi_serial") == E750_ADAPTER:
                blob = json.dumps(unit).lower()
                assert unit.get("enrolled") is False, (
                    "a protected device is still enrolled as a bench unit"
                )
                assert "protected" in blob or "production" in blob, (
                    "the entry does not say WHY it is disabled"
                )


class TestProtectedListHygiene:
    def test_adapters_are_unique_across_entries(self):
        """One cable, one owner: a duplicate would be resolved by precedence."""
        seen: dict[str, str] = {}
        for row in load_protected():
            for adapter in row.get("serial_adapters") or []:
                key = adapter.strip().upper()
                assert key not in seen, (
                    f"adapter {key} claimed by both {seen.get(key)!r} and {row.get('id')!r}"
                )
                seen[key] = str(row.get("id"))

    def test_every_entry_states_a_reason(self):
        for row in load_protected():
            assert str(row.get("reason") or "").strip(), f"{row.get('id')} has no reason"


class TestGuardRefusesTheE750Cable:
    def test_the_e750_cable_is_refused(self):
        from lib.protected_devices import ProtectedDeviceError, assert_console_not_protected

        path = f"/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_{E750_ADAPTER}-if00-port0"
        with pytest.raises(ProtectedDeviceError) as err:
            assert_console_not_protected(path)
        assert "e750" in str(err.value).lower()
