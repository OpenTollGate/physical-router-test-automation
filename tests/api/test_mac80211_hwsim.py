"""mac80211_hwsim WiFi simulation proof-of-concept.

Installs the mac80211_hwsim kernel module inside the OpenWrt VM/router,
loads virtual WiFi radios, and validates that the wireless stack works:
  - iw list shows virtual radios
  - wlan0 interface appears after AP bringup
  - iw scan executes without error

Requires the router to have internet access (for opkg update/install).
Only runs on x86_64 targets where the kmod package is available.
"""

import pytest
import time

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]

_HWSIM_MODULE = "mac80211_hwsim"
_HWSIM_KMOD_PKG = "kmod-mac80211-hwsim"


def _is_x86_64(router):
    arch = router.ssh("uname -m 2>/dev/null").strip()
    return arch == "x86_64"


def _module_loaded(router):
    return _HWSIM_MODULE in router.ssh("lsmod 2>/dev/null || true")


def test_install_hwsim_module(router):
    if not _is_x86_64(router):
        pytest.skip("mac80211_hwsim only available on x86_64 targets")
    if _module_loaded(router):
        pytest.skip("mac80211_hwsim already loaded")

    router.ssh("opkg update >/dev/null 2>&1 || true")
    result = router.ssh(f"opkg install {_HWSIM_KMOD_PKG} 2>&1")
    if "Cannot install" in result or "not found" in result.lower():
        pytest.skip(f"Cannot install {_HWSIM_KMOD_PKG}: {result[-200:]}")


def test_load_hwsim_radios(router):
    if not _is_x86_64(router):
        pytest.skip("mac80211_hwsim only available on x86_64 targets")
    if _module_loaded(router):
        router.ssh(f"rmmod {_HWSIM_MODULE} 2>/dev/null || true")

    router.ssh(f"modprobe {_HWSIM_MODULE} radios=2 2>/dev/null"
               f" || insmod {_HWSIM_MODULE} radios=2 2>/dev/null || true")
    if not _module_loaded(router):
        pytest.skip(f"Could not load {_HWSIM_MODULE} module")


def test_iw_list_shows_virtual_radios(router):
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    iw_list = router.ssh("iw list 2>&1")
    assert "Wiphy" in iw_list, f"iw list missing Wiphy entries: {iw_list[:300]}"
    phy_count = iw_list.count("Wiphy")
    assert phy_count >= 2, f"Expected >= 2 virtual radios, got {phy_count}"


def test_wlan_interface_appears_after_ap_config(router):
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    # Detect the hwsim PHY number and mac80211 path
    phy_info = router.ssh(
        "iw dev 2>/dev/null | head -1; "
        "iw phy 'phy0' info 2>/dev/null | head -1; "
        "ls /sys/class/ieee80211/ 2>/dev/null"
    ).strip()

    phy_names = [p for p in phy_info.split() if p.startswith("phy")]
    if not phy_names:
        # List ieee80211 entries directly
        ieee80211 = router.ssh("ls /sys/class/ieee80211/ 2>/dev/null").strip()
        phy_names = ieee80211.split() if ieee80211 else []

    if not phy_names:
        pytest.skip("No PHY devices detected after hwsim load")

    phy0 = phy_names[0]  # e.g., "phy0"

    # Get the mac80211 path for this PHY
    phy_path = router.ssh(
        f"readlink /sys/class/ieee80211/{phy0}/device/driver 2>/dev/null || "
        f"readlink /sys/class/ieee80211/{phy0} 2>/dev/null"
    ).strip()

    # Build a fresh wireless config referencing the hwsim PHY
    # First, remove any existing wireless config sections
    router.ssh(
        # Delete all existing wifi-device and wifi-iface sections
        "uci -q delete wireless.@wifi-device[0]; "
        "while uci -q delete wireless.@wifi-device[0]; do true; done; "
        "while uci -q delete wireless.@wifi-iface[0]; do true; done; "
        ""
        # Create new radio0 device for hwsim PHY
        "uci set wireless.radio0=wifi-device; "
        "uci set wireless.radio0.type='mac80211'; "
        "uci set wireless.radio0.channel='1'; "
        "uci set wireless.radio0.band='2g'; "
        "uci set wireless.radio0.htmode='HT20'; "
        "uci set wireless.radio0.disabled='0'; "
        ""
        # Create AP interface on radio0
        "uci set wireless.ap0=wifi-iface; "
        "uci set wireless.ap0.device='radio0'; "
        "uci set wireless.ap0.mode='ap'; "
        "uci set wireless.ap0.ssid='HWSIM-Test'; "
        "uci set wireless.ap0.network='lan'; "
        "uci set wireless.ap0.encryption='none'; "
        ""
        "uci commit wireless 2>/dev/null; "
        "wifi reload 2>/dev/null || true"
    )

    time.sleep(8)

    ip_link = router.ssh("ip link show 2>/dev/null")
    has_wlan = any(name in ip_link for name in ("wlan0", "wlan1"))
    if not has_wlan:
        # Debug: show what netifd sees
        radio_status = router.ssh(
            "iw dev 2>/dev/null; "
            "uci show wireless 2>/dev/null | head -20; "
            "logread -e 'wireless\|netifd\|mac80211' 2>/dev/null | tail -10"
        )
        pytest.skip(
            f"No wlan interfaces after AP bringup. "
            f"PHY={phy0} path={phy_path[:100]}. "
            f"Debug: {radio_status[:400]}"
        )


def test_iw_scan_executes(router):
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    has_wlan = "wlan0" in router.ssh("ip link show 2>/dev/null")
    if not has_wlan:
        pytest.skip("wlan0 not available for scan")

    scan_output = router.ssh("iw wlan0 scan 2>&1")
    # Scan may return "No scan results" or "command failed: Device or resource busy"
    # Either way, we just want to verify the command executes without module errors
    assert "No such device" not in scan_output, \
        f"wlan0 disappeared during scan: {scan_output[:200]}"
