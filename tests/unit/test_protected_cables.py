"""Cable-to-protected-device mapping, pinned.

The host carries five identical FTDI FT232R adapters. Nothing on the cable
distinguishes them, and ``/dev/ttyUSB*`` renumbers on every re-enumeration, so
"the one on ttyUSB5" is not an identity — it is whichever cable inherited that
number this boot.

Everything therefore hangs on the FTDI serial. Two of these adapters are wired
to PRODUCTION routers (the MT6000 gateway, owner-confirmed; the E750,
owner-confirmed 2026-11-03) and must be refused before a byte is typed down
them.

Measured on x280, 2026-11-03:

    BG02QAPG -> /dev/ttyUSB0  GL.iNet GL-MT6000        PROTECTED
    BH00DL3M -> /dev/ttyUSB5  shell answers, garbles    PROTECTED (E750)
    BG046H5F -> /dev/ttyUSB1  GL.iNet GL-MT3000        bench
    BG046H14 -> /dev/ttyUSB3  COMFAST CF-WR632AX       bench
    BH00D6DL -> /dev/ttyUSB4  D-Link COVR-X1860 A1     bench
"""

from __future__ import annotations

import pytest

from lib.protected_devices import (
    is_protected,
    load_protected,
    match_protected,
    resolve_serial_adapter,
    ProtectedDeviceError,
    assert_not_protected,
)

#: Adapter serials observed on this host, with what they are actually wired to.
GATEWAY_ADAPTER = "BG02QAPG"
E750_ADAPTER = "BH00DL3M"
BENCH_ADAPTERS = ("BG046H5F", "BG046H14", "BH00D6DL")


def _row_for_adapter(serial: str):
    for row in load_protected():
        if serial.upper() in {str(s).upper() for s in (row.get("serial_adapters") or [])}:
            return row
    return None


def test_the_gateway_cable_is_recorded_as_protected():
    row = _row_for_adapter(GATEWAY_ADAPTER)
    assert row is not None, "the gateway adapter is not in the protected list"
    assert row["id"] == "mt6000-gateway"


def test_the_e750_cable_is_recorded_as_protected():
    """Owner-confirmed production router. An unrecorded production cable is
    indistinguishable from bench kit."""
    row = _row_for_adapter(E750_ADAPTER)
    assert row is not None, (
        "the E750 adapter BH00DL3M is not in the protected list — a production "
        "router that answers to no identity check is exactly the failure mode "
        "this file exists to prevent"
    )
    assert "production" in str(row.get("reason", "")).lower()


def test_bench_cables_are_not_protected():
    """The guard must not swallow the whole bench, or it stops being usable."""
    for serial in BENCH_ADAPTERS:
        assert _row_for_adapter(serial) is None, (
            f"bench adapter {serial} is listed as protected; that would refuse "
            "the entire lab, not a production device"
        )


def test_every_protected_row_has_a_reason():
    for row in load_protected():
        assert row.get("reason"), f"protected row {row.get('id')!r} has no reason"


def test_e750_model_spellings_all_resolve_to_the_same_row():
    """The board may answer with or without the vendor prefix."""
    ids = {
        (match_protected(model=spelling) or {}).get("id")
        for spelling in ("GL-E750", "GL.iNet GL-E750", "GL.iNet GL-E750 (V1)")
    }
    assert ids == {"e750-production"}, f"model spellings disagree: {ids}"


def test_e750_board_name_is_refused():
    with pytest.raises(ProtectedDeviceError):
        assert_not_protected(board_name="glinet,gl-e750")


def test_asserting_the_e750_adapter_is_refused_by_cable_alone():
    """No board answer needed: the cable identity is sufficient to refuse."""
    with pytest.raises(ProtectedDeviceError) as exc:
        assert_not_protected(serial_adapters=[E750_ADAPTER])
    assert "e750" in str(exc.value).lower()


def test_resolve_serial_adapter_reads_the_by_id_name():
    """Resolution is by FTDI serial, never the tty path."""
    link = f"/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_{E750_ADAPTER}-if00-port0"
    assert resolve_serial_adapter(link) == E750_ADAPTER


def test_resolve_serial_adapter_fails_closed_on_an_unidentifiable_path():
    """An unresolvable adapter is reported as None, which the caller must treat
    as 'cannot certify' rather than 'bench'."""
    assert resolve_serial_adapter("/dev/ttyUSB99") is None
