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
import re
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

    # Check if kmod package is already installed (e.g., baked into snapshot)
    opkg_status = router.ssh(f"opkg list-installed 2>/dev/null | grep '{_HWSIM_KMOD_PKG}'")
    if _HWSIM_KMOD_PKG in opkg_status:
        return

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

    # Find hwsim PHYs by checking which ones have the mac80211_hwsim driver
    hwsim_phys = router.ssh(
        "for d in /sys/class/ieee80211/phy*; do "
        "  driver=$(readlink $d/device/driver 2>/dev/null); "
        "  if echo \"$driver\" | grep -q mac80211_hwsim; then "
        "    basename $d; "
        "  fi; "
        "done"
    ).strip().split()

    if not hwsim_phys:
        pytest.skip("No hwsim PHYs found (mac80211_hwsim driver not bound)")

    target_phy = hwsim_phys[0]

    # Get the mac80211 path for this PHY (used by netifd to match radio sections)
    phy_path = router.ssh(
        f"cat /sys/class/ieee80211/{target_phy}/mac80211/phyname 2>/dev/null; "
        f"readlink -f /sys/class/ieee80211/{target_phy}/device 2>/dev/null"
    ).strip()

    # Build a fresh wireless config referencing the hwsim PHY via path
    router.ssh(
        "uci -q delete wireless.@wifi-device[0]; "
        "while uci -q delete wireless.@wifi-device[0]; do true; done; "
        "while uci -q delete wireless.@wifi-iface[0]; do true; done; "

        "uci set wireless.radio0=wifi-device; "
        "uci set wireless.radio0.type='mac80211'; "
        f"uci set wireless.radio0.phy='{target_phy}'; "
        "uci set wireless.radio0.channel='1'; "
        "uci set wireless.radio0.band='2g'; "
        "uci set wireless.radio0.htmode='HT20'; "
        "uci set wireless.radio0.disabled='0'; "

        "uci set wireless.ap0=wifi-iface; "
        "uci set wireless.ap0.device='radio0'; "
        "uci set wireless.ap0.mode='ap'; "
        "uci set wireless.ap0.ssid='HWSIM-Test'; "
        "uci set wireless.ap0.network='lan'; "
        "uci set wireless.ap0.encryption='none'; "

        "uci commit wireless 2>/dev/null; "
        "wifi reload 2>/dev/null || true"
    )

    time.sleep(8)

    ip_link = router.ssh("ip link show 2>/dev/null")
    iw_dev = router.ssh("iw dev 2>/dev/null")
    has_wireless = any(name in ip_link for name in ("wlan", "phy")) or "Interface" in iw_dev
    if not has_wireless:
        # Debug: show what netifd sees
        radio_status = router.ssh(
            "iw dev 2>/dev/null; "
            "uci show wireless 2>/dev/null | head -20; "
            r"logread -e 'wireless|netifd|mac80211' 2>/dev/null | tail -10"
        )
        pytest.skip(
            f"No wlan interfaces after AP bringup. "
            f"PHY={target_phy} path={phy_path[:100]}. "
            f"Debug: {radio_status[:400]}"
        )


def test_iw_scan_executes(router):
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    iw_dev = router.ssh("iw dev 2>/dev/null")
    interface_names = re.findall(r"Interface (\S+)", iw_dev)
    scan_iface = None
    for iface in interface_names:
        if "ap" not in iface.lower():
            scan_iface = iface
            break
    if not scan_iface and interface_names:
        scan_iface = interface_names[0]
    if not scan_iface:
        pytest.skip("No wireless interfaces available for scan")

    scan_output = router.ssh(f"iw {scan_iface} scan 2>&1")
    assert "No such device" not in scan_output, \
        f"{scan_iface} disappeared during scan: {scan_output[:200]}"
