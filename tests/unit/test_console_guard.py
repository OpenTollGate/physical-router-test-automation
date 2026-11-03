"""The console guard: refuse the PRODUCTION GATEWAY'S CABLE, not its name.

Incident this pins (2026-11-03): a health probe was pointed at
`/dev/ttyUSB0` and issued `uptime`, a board-model read and `ndsctl` commands to
it. The adapter on that cable belongs to the production gateway
(`mt6000-gateway`: "production gateway; hermes must stay online"). Nothing in the
harness connected a *path* to a *device identity*, so a manifest place name was
the only thing standing between a test run and the fleet's uplink.

The guard resolves the USB adapter's SERIAL and checks it against
`serial_adapters` in the protected list. Paths are not used: `/dev/ttyUSB*`
renumbers on replug, so a path-based guard would protect whichever cable got that
number next.
"""
from __future__ import annotations

import pytest

from lib.protected_devices import (
    ProtectedDeviceError,
    assert_console_not_protected,
    is_protected,
    match_protected,
    resolve_serial_adapter,
)

# Measured on x280, 2026-11-03.
GATEWAY_ADAPTER = "BG02QAPG"       # production GL-MT6000
BENCH_ADAPTERS = ["BG046H14", "BG046H5F", "BH00D6DL", "BH00DL3M"]
GATEWAY_BY_ID = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02QAPG-if00-port0"
BENCH_BY_ID = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG046H5F-if00-port0"


class TestAdapterIdentityIsInTheProtectedList:
    def test_the_gateway_adapter_is_protected(self):
        assert is_protected(serial_adapters=[GATEWAY_ADAPTER])

    def test_bench_adapters_are_not_protected(self):
        for adapter in BENCH_ADAPTERS:
            assert not is_protected(serial_adapters=[adapter]), adapter

    def test_adapter_lookup_is_case_insensitive(self):
        assert match_protected(serial_adapters=["bg02qapg"]) is not None


class TestResolveSerialAdapter:
    def test_by_id_symlink_name_carries_the_serial(self):
        assert resolve_serial_adapter(GATEWAY_BY_ID) == GATEWAY_ADAPTER

    def test_udev_is_consulted_for_a_bare_path(self):
        assert resolve_serial_adapter("/dev/ttyUSB0", udev=lambda p: "BG02QAPG") == GATEWAY_ADAPTER

    def test_unresolvable_returns_none(self):
        assert resolve_serial_adapter("/dev/ttyUSB99", udev=lambda p: None) is None

    def test_udev_failure_is_reported_not_raised(self):
        def boom(_p):
            raise OSError("udevadm missing")

        assert resolve_serial_adapter("/dev/ttyUSB99", udev=boom) is None


class TestTheGuardRefuses:
    def test_gateway_cable_is_refused(self):
        with pytest.raises(ProtectedDeviceError) as err:
            assert_console_not_protected(GATEWAY_BY_ID)
        assert "mt6000-gateway" in str(err.value)
        assert "production gateway" in str(err.value)

    def test_bare_gateway_path_is_refused_via_udev(self):
        with pytest.raises(ProtectedDeviceError):
            assert_console_not_protected("/dev/ttyUSB0", udev=lambda p: GATEWAY_ADAPTER)

    def test_bench_cable_is_allowed_and_returns_its_serial(self):
        assert assert_console_not_protected(BENCH_BY_ID) == "BG046H5F"

    def test_an_unidentifiable_cable_fails_closed(self):
        """'I do not know what is on this cable' is the state that caused the
        incident, so it is refused — never treated as 'probably fine'."""
        with pytest.raises(ProtectedDeviceError) as err:
            assert_console_not_protected("/dev/ttyUSB99", udev=lambda p: None)
        assert "cannot determine" in str(err.value)


class TestLiveAdaptersOnThisHost:
    def test_the_real_gateway_adapter_is_refused_if_present(self):
        """If the gateway's adapter is plugged into this host, the guard must
        refuse it. Skipped when the cable is elsewhere, because absence of the
        cable is not a licence to claim the guard works."""
        import os

        if not os.path.exists(GATEWAY_BY_ID):
            pytest.skip("gateway adapter not attached to this host")
        with pytest.raises(ProtectedDeviceError):
            assert_console_not_protected(GATEWAY_BY_ID)

    def test_a_known_bench_adapter_is_accepted_if_present(self):
        import os

        if not os.path.exists(BENCH_BY_ID):
            pytest.skip("bench adapter not attached to this host")
        assert assert_console_not_protected(BENCH_BY_ID) == "BG046H5F"
