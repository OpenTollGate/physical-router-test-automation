"""Cloud lab worker — hwsim and vwifi setup."""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

from lib.cloud_lab.constants import TEST_DIR, VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.inner_ssh import inner_ssh
from lib.cloud_lab.worker.shell import _run, log

_VWIFI_BIN_DIR = Path("/opt/vwifi")
def _ensure_vwifi_binaries() -> Path:
    """Ensure vwifi binaries are available on the host. Returns binary dir."""
    server = _VWIFI_BIN_DIR / "host" / "vwifi-server"
    if server.exists() and os.access(server, os.X_OK):
        log.info("[vwifi] Binaries already present at %s", _VWIFI_BIN_DIR)
        return _VWIFI_BIN_DIR

    log.info("[vwifi] Building vwifi from source...")
    build_script = Path(TEST_DIR) / "scripts" / "build-vwifi.sh"
    if build_script.exists():
        _run(
            f"bash {shlex.quote(str(build_script))} --output-dir {shlex.quote(str(_VWIFI_BIN_DIR))}",
            timeout=300,
        )
    else:
        # Fallback: install deps + Docker, then build host (glibc) and
        # guest binaries (static musl via Alpine container).
        _run(
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "cmake make g++ pkg-config libnl-3-dev libnl-genl-3-dev git docker.io "
            ">/dev/null 2>&1 || true && "
            "systemctl start docker 2>/dev/null || true && "
            "rm -rf /tmp/vwifi-build && "
            "git clone --depth 1 --branch master https://github.com/Raizo62/vwifi.git /tmp/vwifi-build && "
            f"mkdir -p {_VWIFI_BIN_DIR}/host {_VWIFI_BIN_DIR}/debian {_VWIFI_BIN_DIR}/openwrt && "
            # Host binaries (glibc dynamic — runs on the GCP Ubuntu host)
            "cd /tmp/vwifi-build && "
            "rm -rf build-host && mkdir -p build-host && cd build-host && "
            "cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc) && "
            f"cp vwifi-server vwifi-ctrl {_VWIFI_BIN_DIR}/host/ && "
            # Guest binaries (static musl via Alpine Docker — runs on musl OpenWrt)
            "cd /tmp/vwifi-build && "
            "docker run --rm -v /tmp/vwifi-build:/src -v /opt/vwifi:/output alpine:latest sh -c '"
            "set -e && "
            "apk add --no-cache cmake make g++ pkgconf libnl3-dev libnl3-static "
            "libstdc++-dev musl-dev linux-headers 2>&1 | tail -3 && "
            "cd /src && rm -rf build-musl && mkdir build-musl && cd build-musl && "
            "rm -f /usr/lib/libnl*.so* && "
            "cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXE_LINKER_FLAGS=-static && "
            "make -j$(nproc) vwifi-client vwifi-add-interfaces && "
            "mkdir -p /output/debian /output/openwrt && "
            "cp vwifi-client vwifi-add-interfaces /output/debian/ && "
            "cp vwifi-client vwifi-add-interfaces /output/openwrt/"
            "'",
            timeout=600,
        )

    if not server.exists():
        raise RuntimeError(f"vwifi-server binary not found at {server}")
    log.info("[vwifi] Binaries ready at %s", _VWIFI_BIN_DIR)
    return _VWIFI_BIN_DIR
def setup_vwifi_host() -> int | None:
    """Start vwifi-server on the GCP host. Returns server PID or None on failure."""
    log.info("[vwifi] Setting up host-side vwifi-server")

    # Load vhost_vsock kernel module
    _run("modprobe vhost_vsock 2>/dev/null || true", timeout=10)
    _run("chmod a+rw /dev/vhost-vsock 2>/dev/null || true", timeout=5)

    # Ensure binaries exist
    bin_dir = _ensure_vwifi_binaries()
    server_bin = bin_dir / "host" / "vwifi-server"
    if not server_bin.exists():
        log.warning("[vwifi] vwifi-server binary not found, skipping vwifi setup")
        return None

    # Start vwifi-server in background
    server_log = Path("/tmp/vwifi-server.log")
    proc = subprocess.Popen(
        [str(server_bin)],
        stdout=server_log.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    time.sleep(2)

    if proc.poll() is not None:
        log.error("[vwifi] vwifi-server exited early (rc=%d). Log: %s",
                  proc.returncode, server_log.read_text()[-500:] if server_log.exists() else "(no log)")
        return None

    log.info("[vwifi] vwifi-server started (pid=%d)", proc.pid)

    # Verify it's listening (check log for startup message)
    time.sleep(1)
    if server_log.exists():
        log_text = server_log.read_text()[-500:]
        log.info("[vwifi] Server log: %s", log_text[:200])

    return proc.pid
def setup_vwifi_guests(alpha_ip: str, debian_ip: str, config: WorkerConfig, results_dir: str = "") -> None:
    """Install vwifi on OpenWrt and Debian VMs for cross-VM frame relay.

    Correct vwifi procedure (from README):
      1. modprobe mac80211_hwsim radios=0  (empty — no local radios)
      2. vwifi-add-interfaces <n> <mac>     (creates relayed wlan interfaces)
      3. vwifi-client <host_ip>             (relays frames for those interfaces)

    vwifi-client controls ONLY interfaces created by vwifi-add-interfaces.
    Local hwsim radios are invisible to the relay.

    OpenWrt complication: baked snapshot has 2 local hwsim radios with netifd
    holding refs.  We can't rmmod.  Instead we:
      - Copy vwifi-add-interfaces + vwifi-client to OpenWrt
      - Use vwifi-add-interfaces to ADD relayed interfaces alongside local ones
      - Reconfigure hostapd to use the relayed interface for SSID broadcast
      - Start vwifi-client (relays ONLY the vwifi-created interfaces)
    """
    bin_dir = _VWIFI_BIN_DIR
    openwrt_client = bin_dir / "openwrt" / "vwifi-client"
    openwrt_add_if = bin_dir / "openwrt" / "vwifi-add-interfaces"
    debian_client = bin_dir / "debian" / "vwifi-client"
    debian_add_if = bin_dir / "debian" / "vwifi-add-interfaces"

    # --- OpenWrt VM (alpha) ---
    log.info("[vwifi] Setting up vwifi on OpenWrt alpha (%s)", alpha_ip)

    # Copy both binaries to OpenWrt
    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(openwrt_client))} root@{alpha_ip}:/usr/bin/vwifi-client && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(openwrt_add_if))} root@{alpha_ip}:/usr/bin/vwifi-add-interfaces && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{alpha_ip} "
        "'chmod +x /usr/bin/vwifi-client /usr/bin/vwifi-add-interfaces'",
        timeout=30,
    )

    # Wait for existing netifd-managed interfaces to be ready
    inner_ssh(alpha_ip, """
        for i in $(seq 1 15); do
            iw dev 2>/dev/null | grep -q Interface && break
            sleep 1
        done
    """, timeout=30)

    # Create relayed wlan interface on OpenWrt via vwifi-add-interfaces
    r_owrt_add = inner_ssh(alpha_ip, """
        vwifi-add-interfaces 1 0a:0b:0c:01:01 2>&1; echo "EXIT=$?"
        sleep 2
        iw dev 2>/dev/null | grep Interface || echo NO_INTERFACES
    """, timeout=15)
    log.info("[vwifi] OpenWrt vwifi-add-interfaces: %s", r_owrt_add.stdout.strip()[:400])

    owrt_vwifi_iface = None
    add_if_ok = "EXIT=0" in r_owrt_add.stdout
    for line in r_owrt_add.stdout.strip().splitlines():
        if "Interface" in line:
            iface = line.strip().split()[-1]
            if "wlan" in iface.lower() and not iface.startswith("phy"):
                owrt_vwifi_iface = iface
                break

    if not owrt_vwifi_iface:
        r_all = inner_ssh(alpha_ip, "iw dev 2>/dev/null | grep Interface || echo NONE", timeout=10)
        log.warning("[vwifi] OpenWrt interfaces after add-interfaces: %s", r_all.stdout.strip()[:300])
        for line in r_all.stdout.strip().splitlines():
            if "Interface" in line:
                iface = line.strip().split()[-1]
                if "wlan" in iface.lower() and not iface.startswith("phy"):
                    owrt_vwifi_iface = iface
                    break

    if owrt_vwifi_iface:
        log.info("[vwifi] OpenWrt relayed interface: %s", owrt_vwifi_iface)
        inner_ssh(alpha_ip, f"""
            ip link set {owrt_vwifi_iface} up 2>/dev/null
            cat > /tmp/vwifi-hostapd.conf << 'HOSTAPD'
interface={owrt_vwifi_iface}
driver=nl80211
ssid=TollGate-ALPHA
hw_mode=g
channel=6
HOSTAPD
            hostapd -B /tmp/vwifi-hostapd.conf 2>&1 || echo HOSTAPD_FAILED
        """, timeout=15)
    else:
        log.warning("[vwifi] vwifi-add-interfaces did not create relayed iface on OpenWrt (ok=%s)", add_if_ok)

    # Start vwifi-client on OpenWrt (BusyBox ash has no nohup — use bare &)
    r_owrt_client = inner_ssh(alpha_ip, """
        vwifi-client 10.99.99.2 >/tmp/vwifi-client.log 2>&1 &
        sleep 3
        cat /tmp/vwifi-client.log
        echo VWIFI_CLIENT_OPENWRT_DONE
    """, timeout=20)
    log.info("[vwifi] OpenWrt vwifi-client: %s", r_owrt_client.stdout.strip()[:300])

    # --- Debian VM ---
    log.info("[vwifi] Setting up vwifi on Debian (%s)", debian_ip)

    inner_ssh(debian_ip, "apt-get install -y -qq iw 2>&1 | tail -1", timeout=60)

    # Copy both binaries to Debian
    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(debian_client))} root@{debian_ip}:/usr/local/bin/vwifi-client && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(debian_add_if))} root@{debian_ip}:/usr/local/bin/vwifi-add-interfaces && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{debian_ip} "
        "'chmod +x /usr/local/bin/vwifi-client /usr/local/bin/vwifi-add-interfaces'",
        timeout=30,
    )

    # Load mac80211_hwsim with radios=0 (empty — vwifi creates interfaces via add-interfaces)
    r_mod = inner_ssh(debian_ip, """
        rmmod mac80211_hwsim 2>/dev/null || true
        modprobe mac80211_hwsim radios=0 2>&1 && echo HWSIM_ZERO_OK || {
            modprobe mac80211_hwsim 2>&1 && echo HWSIM_DEFAULT_OK || echo HWSIM_FAIL
        }
    """, timeout=60)
    log.info("[vwifi] Debian hwsim radios=0: %s", r_mod.stdout.strip()[:300])

    # Create relayed wlan interface on Debian via vwifi-add-interfaces
    r_deb_add = inner_ssh(debian_ip, """
        vwifi-add-interfaces 1 0a:0b:0c:02:01 2>&1
        sleep 2
        iw dev 2>/dev/null | grep Interface || echo NO_INTERFACES
    """, timeout=15)
    log.info("[vwifi] Debian vwifi-add-interfaces: %s", r_deb_add.stdout.strip()[:300])

    # Start vwifi-client on Debian
    r_deb_client = inner_ssh(debian_ip, """
        nohup vwifi-client 10.99.99.2 >/tmp/vwifi-client.log 2>&1 &
        disown
        sleep 5
        cat /tmp/vwifi-client.log
        echo VWIFI_CLIENT_DEBIAN_DONE
    """, timeout=30)
    log.info("[vwifi] Debian vwifi-client: %s", r_deb_client.stdout.strip()[:300])

    # Find relayed interface on Debian
    r_iw = inner_ssh(debian_ip, "iw dev 2>/dev/null | grep Interface || echo NO_INTERFACES", timeout=10)
    log.info("[vwifi] Debian interfaces: %s", r_iw.stdout.strip()[:200])

    debian_iface = None
    for line in r_iw.stdout.strip().splitlines():
        if "Interface" in line and "wlan" in line.lower():
            debian_iface = line.strip().split()[-1]
            break

    # --- Capture iw scan proof artifacts from both VMs ---
    scan_dir = Path(f"{results_dir}/raw/virtual-wifi") if results_dir else None
    if scan_dir:
        scan_dir.mkdir(parents=True, exist_ok=True)

    # OpenWrt scan (from local AP interface — shows own beacons + any nearby)
    owrt_ap_ifaces = inner_ssh(
        alpha_ip,
        "iw dev 2>/dev/null | grep -E 'Interface|type' | head -10",
        timeout=10,
    )
    owrt_scan_output = "(no AP interface found)\n" + owrt_ap_ifaces.stdout
    for line in owrt_ap_ifaces.stdout.strip().splitlines():
        if "Interface" in line:
            iface = line.strip().split()[-1]
            r_owrt_scan = inner_ssh(alpha_ip, f"iw {iface} scan trigger 2>/dev/null; sleep 2; iw {iface} scan dump 2>&1 | head -200", timeout=20)
            if r_owrt_scan.stdout.strip():
                owrt_scan_output = r_owrt_scan.stdout
                break

    if scan_dir:
        (scan_dir / "iw-scan-openwrt.txt").write_text(owrt_scan_output)
        log.info("[vwifi] Saved OpenWrt scan to %s/iw-scan-openwrt.txt", scan_dir)
    log.info("[vwifi] OpenWrt scan preview: %s", owrt_scan_output[:300].replace("\n", " | "))

    # Debian scan (from relayed interface — the cross-VM proof)
    debian_scan_output = "(no relayed interface)"
    if debian_iface:
        log.info("[vwifi] Debian relayed interface: %s", debian_iface)

        inner_ssh(debian_ip, f"ip link set {debian_iface} up", timeout=10)
        time.sleep(3)  # give hostapd time to broadcast beacons through relay
        r_scan = inner_ssh(debian_ip, f"iw {debian_iface} scan 2>&1", timeout=15)
        debian_scan_output = r_scan.stdout
        if "TollGate-ALPHA" in r_scan.stdout:
            log.info("[vwifi] ✅ Debian scan sees TollGate-ALPHA — cross-VM WiFi relay working!")
        else:
            log.warning("[vwifi] Debian scan did NOT find TollGate-ALPHA. Output: %s",
                        r_scan.stdout[:500])
    else:
        log.warning("[vwifi] No relayed interface on Debian. iw dev: %s",
                    r_iw.stdout[:300])

    if scan_dir:
        (scan_dir / "iw-scan-debian.txt").write_text(debian_scan_output)
        log.info("[vwifi] Saved Debian scan to %s/iw-scan-debian.txt", scan_dir)

    log.info("[vwifi] Guest setup complete")
def setup_hwsim_wifi(alpha_ip: str, *, vwifi_mode: bool = False) -> None:
    """Provision virtual WiFi interfaces on the OpenWrt VM via mac80211_hwsim.

    When *vwifi_mode* is True, keeps existing hwsim PHYs (netifd holds
    references so rmmod fails).  ``_setup_vwifi_guests()`` runs vwifi-client
    which auto-discovers all hwsim interfaces and relays frames through the
    vsock server.  Still configures UCI wireless for SSID/metadata consistency.

    When *vwifi_mode* is False (default), creates AP interfaces manually
    (bypasses netifd's mac80211.sh which fails with hwsim due to
    HOSTAPD_START_FAILED).  The manual path:
      modprobe → iw phy … interface add … type __ap → brctl addif → ip link up

    Idempotent — safe to call multiple times.  Non-fatal: logs a warning
    and returns on any failure so the cloud lab continues without WiFi.
    """
    log.info("[hwsim] Setting up virtual WiFi on %s (vwifi=%s)", alpha_ip, vwifi_mode)

    r = inner_ssh(alpha_ip, "lsmod | grep mac80211_hwsim", timeout=10)
    already_loaded = r.returncode == 0 and "mac80211_hwsim" in r.stdout

    if vwifi_mode:
        if not already_loaded:
            r = inner_ssh(alpha_ip, "modprobe mac80211_hwsim radios=2 2>&1", timeout=15)
            if r.returncode != 0:
                log.warning("[hwsim] modprobe failed (rc=%d): %s — skipping WiFi",
                            r.returncode, (r.stderr or r.stdout or "").strip()[:300])
                return
        r2 = inner_ssh(alpha_ip, "iw phy 2>/dev/null | grep -c 'Wiphy'", timeout=10)
        phy_count = r2.stdout.strip() if r2.returncode == 0 else "?"
        log.info("[hwsim] vwifi mode: using existing hwsim (phy_count=%s)", phy_count)

        inner_ssh(alpha_ip, """
            uci set wireless.radio0.type='mac80211'
            uci set wireless.radio0.band='2g'
            uci set wireless.radio0.channel='6'
            uci set wireless.radio0.htmode='HT20'
            uci set wireless.radio0.disabled='0'

            uci set wireless.default_radio0.device='radio0'
            uci set wireless.default_radio0.mode='ap'
            uci set wireless.default_radio0.ssid='TollGate-ALPHA'
            uci set wireless.default_radio0.network='lan'
            uci set wireless.default_radio0.encryption='none'

            uci set wireless.radio1.type='mac80211'
            uci set wireless.radio1.band='5g'
            uci set wireless.radio1.channel='36'
            uci set wireless.radio1.htmode='VHT80'
            uci set wireless.radio1.disabled='0'

            uci set wireless.default_radio1.device='radio1'
            uci set wireless.default_radio1.mode='ap'
            uci set wireless.default_radio1.ssid='TollGate-ALPHA'
            uci set wireless.default_radio1.network='lan'
            uci set wireless.default_radio1.encryption='none'

            uci commit wireless 2>/dev/null
            wifi reload 2>/dev/null || true
        """, timeout=30)
        log.info("[hwsim] UCI wireless configured (vwifi mode)")
        log.info("[hwsim] Setup complete (vwifi mode)")
        return

    # Non-vwifi: load hwsim with 2 radios if not already loaded
    if already_loaded:
        log.info("[hwsim] Module already loaded, skipping modprobe")
    else:
        r = inner_ssh(alpha_ip, "modprobe mac80211_hwsim radios=2 2>&1", timeout=15)
        if r.returncode != 0:
            log.warning("[hwsim] modprobe mac80211_hwsim failed (rc=%d): %s — skipping WiFi setup",
                        r.returncode, (r.stderr or r.stdout or "").strip()[:300])
            return
        r = inner_ssh(alpha_ip, "lsmod | grep mac80211_hwsim", timeout=10)
        if r.returncode != 0:
            log.warning("[hwsim] Module not in lsmod after modprobe — skipping WiFi setup")
            return
        log.info("[hwsim] Loaded mac80211_hwsim radios=2")

    # --- 2. Remove stale tmp interfaces left by netifd ---
    inner_ssh(alpha_ip, "iw dev tmp.radio0 del 2>/dev/null; iw dev tmp.radio1 del 2>/dev/null", timeout=5)

    # --- 3. Create AP interfaces manually (bypasses broken netifd wifi reload) ---
    r = inner_ssh(alpha_ip, "iw dev 2>/dev/null | grep -c 'phy0-ap0'", timeout=10)
    if r.returncode == 0 and "1" in r.stdout.strip():
        log.info("[hwsim] phy0-ap0 already exists, skipping manual creation")
    else:
        log.info("[hwsim] Creating AP interfaces manually")
        r = inner_ssh(alpha_ip, """
            iw phy phy0 interface add phy0-ap0 type __ap 2>&1 && \
            iw phy phy1 interface add phy1-ap0 type __ap 2>&1
        """, timeout=15)
        if r.returncode != 0:
            log.warning("[hwsim] Manual interface creation failed: %s", (r.stdout or r.stderr or "").strip()[:300])
            return

    # --- 4. Add to br-lan and bring up ---
    inner_ssh(alpha_ip, """
        brctl addif br-lan phy0-ap0 2>/dev/null
        brctl addif br-lan phy1-ap0 2>/dev/null
        ip link set phy0-ap0 up 2>/dev/null
        ip link set phy1-ap0 up 2>/dev/null
    """, timeout=10)

    # --- 5. Configure UCI wireless for consistency (iwinfo reads SSID from UCI) ---
    r = inner_ssh(alpha_ip, "uci get wireless.radio0.type 2>/dev/null", timeout=10)
    if r.returncode != 0 or "mac80211" not in r.stdout:
        inner_ssh(alpha_ip, """
            while uci -q delete wireless.@wifi-device[0]; do true; done
            while uci -q delete wireless.@wifi-iface[0]; do true; done

            PHY0_PATH=$(readlink -f /sys/class/ieee80211/phy0/device 2>/dev/null)
            PHY1_PATH=$(readlink -f /sys/class/ieee80211/phy1/device 2>/dev/null)

            uci set wireless.radio0=wifi-device
            uci set wireless.radio0.type='mac80211'
            uci set wireless.radio0.path="${PHY0_PATH#*/sys/devices/}"
            uci set wireless.radio0.band='2g'
            uci set wireless.radio0.channel='6'
            uci set wireless.radio0.htmode='HT20'
            uci set wireless.radio0.disabled='0'

            uci set wireless.default_radio0=wifi-iface
            uci set wireless.default_radio0.device='radio0'
            uci set wireless.default_radio0.mode='ap'
            uci set wireless.default_radio0.ssid='TollGate-ALPHA'
            uci set wireless.default_radio0.network='lan'
            uci set wireless.default_radio0.encryption='none'

            uci set wireless.radio1=wifi-device
            uci set wireless.radio1.type='mac80211'
            uci set wireless.radio1.path="${PHY1_PATH#*/sys/devices/}"
            uci set wireless.radio1.band='5g'
            uci set wireless.radio1.channel='36'
            uci set wireless.radio1.htmode='VHT80'
            uci set wireless.radio1.disabled='0'

            uci set wireless.default_radio1=wifi-iface
            uci set wireless.default_radio1.device='radio1'
            uci set wireless.default_radio1.mode='ap'
            uci set wireless.default_radio1.ssid='TollGate-ALPHA'
            uci set wireless.default_radio1.network='lan'
            uci set wireless.default_radio1.encryption='none'

            uci commit wireless 2>/dev/null
        """, timeout=30)
        log.info("[hwsim] UCI wireless configured")

    # --- 6. Verify interfaces ---
    r = inner_ssh(alpha_ip, "iw dev 2>/dev/null | grep Interface", timeout=10)
    interfaces = [line.strip() for line in r.stdout.strip().splitlines() if "Interface" in line]
    verified = any("ap0" in iface for iface in interfaces)

    if verified:
        log.info("[hwsim] AP interfaces verified: %s", ", ".join(interfaces))
    else:
        log.warning("[hwsim] No AP interfaces detected — WiFi tests may skip. iw dev: %s",
                    r.stdout.strip()[:300])

    log.info("[hwsim] Setup complete (verified=%s)", verified)
