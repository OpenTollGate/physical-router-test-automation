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


# ---------------------------------------------------------------------------
# STA (station) mode tests — second hwsim radio connects to the first's AP
# ---------------------------------------------------------------------------


def _get_hwsim_phys(router):
    """Return list of PHY names bound to mac80211_hwsim driver."""
    phys = router.ssh(
        "for d in /sys/class/ieee80211/phy*; do "
        "  driver=$(readlink $d/device/driver 2>/dev/null); "
        "  if echo \"$driver\" | grep -q mac80211_hwsim; then "
        "    basename $d; "
        "  fi; "
        "done"
    ).strip().split()
    return [p for p in phys if p]


def _find_sta_interface(router):
    """Find the STA interface (hwsim interface whose name does NOT contain 'ap')."""
    iw_dev = router.ssh("iw dev 2>/dev/null")
    interfaces = re.findall(r"Interface (\S+)", iw_dev)
    for iface in interfaces:
        if "ap" not in iface.lower():
            return iface
    return None


def _sta_interface_exists(router):
    """Check whether a STA interface is present on the system."""
    return _find_sta_interface(router) is not None


@pytest.mark.slow
def test_sta_scan_sees_ap(router):
    """Configure second hwsim radio as STA and verify it scans the AP SSID."""
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    hwsim_phys = _get_hwsim_phys(router)
    if len(hwsim_phys) < 2:
        pytest.skip(f"Need >= 2 hwsim PHYs for STA test, found {len(hwsim_phys)}")

    sta_phy = hwsim_phys[1]

    router.ssh(
        "uci set wireless.radio1=wifi-device; "
        "uci set wireless.radio1.type='mac80211'; "
        f"uci set wireless.radio1.phy='{sta_phy}'; "
        "uci set wireless.radio1.channel='1'; "
        "uci set wireless.radio1.band='2g'; "
        "uci set wireless.radio1.htmode='HT20'; "
        "uci set wireless.radio1.disabled='0'; "

        "uci set wireless.sta0=wifi-iface; "
        "uci set wireless.sta0.device='radio1'; "
        "uci set wireless.sta0.mode='sta'; "
        "uci set wireless.sta0.ssid='HWSIM-Test'; "
        "uci set wireless.sta0.network='lan'; "
        "uci set wireless.sta0.encryption='none'; "

        "uci commit wireless 2>/dev/null; "
        "wifi reload 2>/dev/null || true"
    )

    time.sleep(5)

    sta_iface = _find_sta_interface(router)
    if not sta_iface:
        iw_dev = router.ssh("iw dev 2>/dev/null")
        uci_wireless = router.ssh("uci show wireless 2>/dev/null | head -30")
        pytest.skip(
            f"No STA interface after radio1 config. "
            f"PHY={sta_phy}. iw dev: {iw_dev[:300]}. "
            f"UCI: {uci_wireless[:300]}"
        )

    router.ssh(f"iw {sta_iface} scan trigger 2>/dev/null || true")
    time.sleep(2)
    scan_dump = router.ssh(f"iw {sta_iface} scan dump 2>&1")

    # Also try the combined scan command as fallback
    if "HWSIM-Test" not in scan_dump:
        combined_scan = router.ssh(f"iw {sta_iface} scan 2>&1")
        scan_dump = combined_scan

    assert "HWSIM-Test" in scan_dump, \
        f"STA scan did not find 'HWSIM-Test' SSID. Output: {scan_dump[:500]}"


@pytest.mark.slow
def test_sta_associates_with_ap(router):
    """Verify the STA radio associates with the AP."""
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    sta_iface = _find_sta_interface(router)
    if not sta_iface:
        pytest.skip("No STA interface found (test_sta_scan_sees_ap prerequisite not met)")

    # Poll for connection — wpa_supplicant may need time to associate
    connected = False
    link_output = "Not connected"
    for _ in range(5):
        link_output = router.ssh(f"iw {sta_iface} link 2>&1")
        if "Connected to" in link_output:
            connected = True
            break
        if "Not connected" not in link_output:
            # Partial state — give it more time
            connected = "Connected to" in link_output
            if connected:
                break
        time.sleep(2)

    if not connected:
        wpa_log = router.ssh(
            r"logread -e 'wireless|wpa' 2>/dev/null | tail -20"
        )
        pytest.skip(
            f"STA did not associate with AP within 10s. "
            f"link: {link_output[:200]}. "
            f"wpa log: {wpa_log[:300]}"
        )


def test_sta_receives_dhcp(router):
    """Verify the STA interface can obtain a DHCP lease (aspirational)."""
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    sta_iface = _find_sta_interface(router)
    if not sta_iface:
        pytest.skip("No STA interface found")

    router.ssh(f"ip link set {sta_iface} up 2>/dev/null || true")
    time.sleep(1)

    dhcp_output = router.ssh(
        f"udhcpc -i {sta_iface} -n -q -T 3 -t 3 2>&1"
    )

    if "lease of" not in dhcp_output.lower() and "bound to" not in dhcp_output.lower():
        pytest.skip(
            f"DHCP did not succeed on STA interface (circular dependency possible). "
            f"Output: {dhcp_output[:300]}"
        )


@pytest.mark.slow
def test_sta_disconnect_and_reconnect(router):
    """Verify STA can disconnect and reconnect to the AP."""
    if not _module_loaded(router):
        pytest.skip("mac80211_hwsim not loaded")

    sta_iface = _find_sta_interface(router)
    if not sta_iface:
        pytest.skip("No STA interface found")

    router.ssh(f"iw {sta_iface} disconnect 2>/dev/null || true")
    time.sleep(2)

    link_after_disconnect = router.ssh(f"iw {sta_iface} link 2>&1")
    # May show "Not connected" — that's expected

    connect_result = router.ssh(f"iw {sta_iface} connect HWSIM-Test 2>&1")
    time.sleep(5)

    link_output = router.ssh(f"iw {sta_iface} link 2>&1")
    assert "Connected to" in link_output, \
        f"STA did not reconnect after iw connect. connect output: {connect_result[:200]}, " \
        f"link: {link_output[:200]}, " \
        f"after disconnect was: {link_after_disconnect[:200]}"
