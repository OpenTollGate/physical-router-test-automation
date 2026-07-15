"""Cloud lab worker — inner QEMU VMs."""

from __future__ import annotations

import logging
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path

from lib.cloud_lab.constants import (
    ALPHA_WAN_MAC,
    BETA_BRIDGE,
    BETA_LAN_HOST_IP,
    BETA_LAN_IP,
    BETA_TAP,
    BETA_WAN_MAC,
    DEBIAN_IP,
    DEBIAN_MAC,
    MGMT_ALPHA_IP,
    MGMT_ALPHA_MAC,
    MGMT_BETA_IP,
    MGMT_BETA_MAC,
    MGMT_DEBIAN_IP,
    MGMT_DEBIAN_MAC,
    MGMT_TAP_ALPHA,
    MGMT_TAP_BETA,
    MGMT_TAP_DEBIAN,
    OPENWRT_IP,
    SELLER_OPENWRT_IP,
    SELLER_OPENWRT_MAC,
    UPSTREAM_BRIDGE,
    UPSTREAM_TAP_ALPHA,
    UPSTREAM_TAP_BETA,
    VIRT_LAB_PASSWORD,
    VIRT_LAB_WORKDIR,
    chain_bridge,
    chain_disk_name,
    chain_host_ip,
    chain_lan_ip,
    chain_lan_mac,
    chain_lan_tap,
    chain_mgmt_ip,
    chain_mgmt_mac,
    chain_mgmt_tap,
    chain_wan_mac,
    chain_wan_tap,
)
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.inner_ssh import inner_ssh, wait_inner_ssh
from lib.cloud_lab.worker.network import (
    configure_alpha_wan,
    configure_beta_lan,
    configure_beta_upstream,
    setup_bridge,
)
from lib.cloud_lab.worker.shell import _run, log

def _virt_lab_workdir() -> Path:
    return Path(os.path.expandvars(VIRT_LAB_WORKDIR))
def _launch_qemu(
    *,
    name: str,
    memory_mb: int,
    cpus: int,
    disk_name: str,
    tap_name: str,
    mac: str,
    wan_tap: str | None = None,
    wan_mac: str | None = None,
    vsock_cid: int | None = None,
    mgmt_tap: str | None = None,
    mgmt_mac: str | None = None,
    seed_iso: str | None = None,
) -> subprocess.Popen[str]:
    workdir = _virt_lab_workdir()
    run_dir = workdir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    serial_sock = run_dir / f"{name}.serial.sock"
    monitor_sock = run_dir / f"{name}.monitor.sock"
    pidfile = run_dir / f"{name}.pid"
    qemu_log = Path(f"/tmp/{name}-qemu.log")
    for path in (serial_sock, monitor_sock, pidfile):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    cmd = [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-m",
        str(memory_mb),
        "-smp",
        str(cpus),
        "-display",
        "none",
        "-serial",
        f"unix:{serial_sock},server=on,wait=off",
        "-monitor",
        f"unix:{monitor_sock},server=on,wait=off",
        "-drive",
        f"file={workdir / 'overlays' / disk_name},format=qcow2,if=virtio",
        "-netdev",
        f"tap,id=net0,ifname={tap_name},script=no,downscript=no",
        "-device",
        f"virtio-net-pci,netdev=net0,mac={mac}",
        "-pidfile",
        str(pidfile),
    ]
    if vsock_cid is not None:
        cmd += [
            "-device", f"vhost-vsock-pci,id=vsock0,guest-cid={vsock_cid}",
        ]
    if wan_tap:
        cmd += [
            "-netdev", f"tap,id=net1,ifname={wan_tap},script=no,downscript=no",
            "-device", f"virtio-net-pci,netdev=net1,mac={wan_mac}",
        ]
    if mgmt_tap:
        cmd += [
            "-netdev", f"tap,id=mgmt,ifname={mgmt_tap},script=no,downscript=no",
            "-device", f"virtio-net-pci,netdev=mgmt,mac={mgmt_mac}",
        ]
    if seed_iso:
        cmd += ["-cdrom", str(seed_iso)]
    log.info("Launching %s QEMU: disk=%s tap=%s mac=%s", name, disk_name, tap_name, mac)
    with qemu_log.open("w") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            cwd=workdir,
        )
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name} QEMU exited early with rc={proc.returncode}; see {qemu_log}")
        if serial_sock.exists():
            return proc
        time.sleep(0.5)
    raise RuntimeError(f"{name} QEMU did not create serial socket at {serial_sock}")
def configure_mgmt_nic(guest_ip: str, mgmt_ip: str, mgmt_mac: str) -> None:
    ssh_prefix = (
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{guest_ip}"
    )
    guest_script = (
        f"IFACE=$(ip -o link | grep '{mgmt_mac}' | awk '{{print $2}}' | tr -d ':'); "
        f"[ -z \"$IFACE\" ] && echo mgmt_nic_not_found && exit 0; "
        f"uci set network.mgmt=interface 2>/dev/null || true; "
        f"uci set network.mgmt.proto='static' 2>/dev/null || true; "
        f"uci set network.mgmt.device=$IFACE 2>/dev/null || true; "
        f"uci set network.mgmt.ipaddr='{mgmt_ip}' 2>/dev/null || true; "
        f"uci set network.mgmt.netmask='255.255.255.0' 2>/dev/null || true; "
        f"uci add_list firewall.@zone[0].network='mgmt' 2>/dev/null || true; "
        f"uci commit network 2>/dev/null || true; "
        f"uci commit firewall 2>/dev/null || true; "
        f"/etc/init.d/network restart 2>/dev/null || true; "
        f"echo mgmt_ok_$IFACE"
    )
    r = _run(f"{ssh_prefix} {shlex.quote(guest_script)}", timeout=15, check=False)
    out = (r.stdout or "").strip()
    if "mgmt_ok_" in out:
        log.info("[mgmt] %s: configured %s", guest_ip, out.replace("mgmt_ok_", ""))
    elif "mgmt_nic_not_found" in out:
        log.warning("[mgmt] %s: mgmt NIC (mac=%s) not found", guest_ip, mgmt_mac)
    else:
        log.warning("[mgmt] %s: unexpected (rc=%d): %s", guest_ip, r.returncode, out[:200])
def _recv_serial(conn: socket.socket, timeout: float = 2.0) -> str:
    conn.settimeout(timeout)
    chunks: list[bytes] = []
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except TimeoutError:
        pass
    return b"".join(chunks).decode("utf-8", errors="replace")
def _serial_send_wait(conn: socket.socket, command: str, wait: float = 2.0) -> str:
    conn.sendall((command + "\n").encode())
    time.sleep(wait)
    return _recv_serial(conn, timeout=2.0)
def _provision_openwrt_serial(name: str, ip: str, timeout: int = 90, *, gateway: str = "10.99.99.2") -> None:
    serial_sock = _virt_lab_workdir() / "run" / f"{name}.serial.sock"
    deadline = time.time() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        while True:
            try:
                conn.connect(str(serial_sock))
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if time.time() >= deadline:
                    raise RuntimeError(f"{name} serial socket was not ready at {serial_sock}")
                time.sleep(1)

        log.info("Provisioning %s OpenWrt over serial for %s", name, ip)
        conn.sendall(b"\n")
        booted = False
        while time.time() < deadline:
            conn.sendall(b"\n")
            data = _recv_serial(conn, timeout=2.0)
            if "Please press Enter" in data or "root@OpenWrt" in data or ":/#" in data or "OpenWrt" in data:
                booted = True
                break
            time.sleep(1)
        if not booted:
            raise RuntimeError(f"{name} OpenWrt did not reach serial boot prompt")

        _serial_send_wait(conn, "", wait=2)
        password = shlex.quote(VIRT_LAB_PASSWORD)
        commands = [
            f"printf '%s\\n%s\\n' {password} {password} | passwd root",
            "uci set dropbear.@dropbear[0].PasswordAuth='on'",
            "uci commit dropbear",
            "/etc/init.d/dropbear restart",
            "uci add firewall rule",
            "uci set firewall.@rule[-1].name='Allow-SSH-WAN'",
            "uci set firewall.@rule[-1].src='wan'",
            "uci set firewall.@rule[-1].dest_port='22'",
            "uci set firewall.@rule[-1].proto='tcp'",
            "uci set firewall.@rule[-1].target='ACCEPT'",
            "uci commit firewall",
            "fw4 restart",
            "uci delete network.mgmt 2>/dev/null || true",
            f"uci set network.lan.ipaddr='{ip}'",
            "uci set network.lan.netmask='255.255.255.0'",
            f"uci set network.lan.gateway='{gateway}'",
            "uci set network.lan.dns='8.8.8.8'",
            "uci commit network",
            "/etc/init.d/network restart",
        ]
        for command in commands:
            _serial_send_wait(conn, command, wait=2)
    time.sleep(8)
def reset_openwrt_overlay_only() -> None:
    """Reset OpenWrt disk state; preserve Debian overlay (Playwright cache)."""
    log.info("Resetting OpenWrt overlay only (Debian overlay preserved)")
    _run(
        "killall -9 qemu-system-x86_64 2>/dev/null || true; sleep 1; "
        f"cd {VIRT_LAB_WORKDIR} && "
        "OWRT_BASE=images/openwrt-base.qcow2; "
        "[ -f \"$OWRT_BASE\" ] || OWRT_BASE=../images/openwrt-base.qcow2; "
        "OWRT_BASE=$(readlink -f \"$OWRT_BASE\"); "
        "rm -f overlays/tollgate-poc.qcow2 overlays/tollgate-seller.qcow2 && "
        "qemu-img create -f qcow2 -F qcow2 -b \"$OWRT_BASE\" overlays/tollgate-poc.qcow2 >/dev/null && "
        "qemu-img create -f qcow2 -F qcow2 -b \"$OWRT_BASE\" overlays/tollgate-seller.qcow2 >/dev/null",
        timeout=60,
    )
    r = _run(f"test -f {VIRT_LAB_WORKDIR}/overlays/debian-client.qcow2 && echo DEBIAN_OVERLAY_OK", check=False)
    if "DEBIAN_OVERLAY_OK" in r.stdout:
        log.info("Debian overlay present (cached)")
    else:
        log.warning("Debian overlay missing — creating from base image")
        _run(
            f"cd {VIRT_LAB_WORKDIR} && "
            "DEB_BASE=images/debian-12-base.qcow2; "
            "[ -f \"$DEB_BASE\" ] || DEB_BASE=../images/debian-12-base.qcow2; "
            "[ -f \"$DEB_BASE\" ] || DEB_BASE=images/debian-12-nocloud-amd64.qcow2; "
            "[ -f \"$DEB_BASE\" ] || DEB_BASE=../images/debian-12-nocloud-amd64.qcow2; "
            "DEB_BASE=$(readlink -f \"$DEB_BASE\"); "
            "qemu-img create -f qcow2 -F qcow2 -b \"$DEB_BASE\" overlays/debian-client.qcow2 >/dev/null && "
            "qemu-img resize --shrink overlays/debian-client.qcow2 10G >/dev/null 2>&1 || true",
            timeout=60,
        )
def start_inner_vms(config: WorkerConfig) -> None:
    setup_bridge()
    reset_openwrt_overlay_only()

    if config.vwifi_enabled:
        log.info("[vwifi] Loading vhost_vsock module for cross-VM WiFi")
        _run("modprobe vhost_vsock 2>/dev/null || true && chmod a+rw /dev/vhost-vsock 2>/dev/null || true", timeout=10)

    if config.two_router:
        log.info("Starting Beta OpenWrt VM (upstream router)...")
        beta_proc = _launch_qemu(
            name="openwrt-beta",
            memory_mb=512,
            cpus=1,
            disk_name="tollgate-seller.qcow2",
            tap_name=BETA_TAP,
            mac=SELLER_OPENWRT_MAC,
            wan_tap=UPSTREAM_TAP_BETA,
            wan_mac=BETA_WAN_MAC,
            vsock_cid=11 if config.vwifi_enabled else None,
            mgmt_tap=MGMT_TAP_BETA,
            mgmt_mac=MGMT_BETA_MAC,
        )
        # Beta is on isolated tg-beta-br — always needs serial provisioning
        # because the pre-provisioned base has IP 10.99.99.1 (on tg-poc-br)
        # and there's no DHCP server on tg-beta-br to give it SELLER_OPENWRT_IP.
        _provision_openwrt_serial("openwrt-beta", BETA_LAN_IP, gateway=BETA_LAN_HOST_IP)
        if beta_proc.poll() is not None:
            raise RuntimeError(f"Beta OpenWrt VM exited during provisioning with rc={beta_proc.returncode}")
        if not wait_inner_ssh(BETA_LAN_IP):
            raise RuntimeError("Beta OpenWrt VM did not become reachable")

        configure_mgmt_nic(BETA_LAN_IP, MGMT_BETA_IP, MGMT_BETA_MAC)
        configure_beta_lan(BETA_LAN_IP)
        configure_beta_upstream(MGMT_BETA_IP)

        config.secondary_router_host = MGMT_BETA_IP
        log.info("Beta OpenWrt VM SSH OK at %s (lan=%s, mgmt=%s)", MGMT_BETA_IP, BETA_LAN_IP, MGMT_BETA_IP)

    if config.reseller_scenarios and not config.secondary_router_host:
        log.info("Starting managed seller OpenWrt VM for reseller scenarios...")
        seller_proc = _launch_qemu(
            name="openwrt-seller",
            memory_mb=512,
            cpus=1,
            disk_name="tollgate-seller.qcow2",
            tap_name="tg-poc-tap3",
            mac=SELLER_OPENWRT_MAC,
            mgmt_tap=MGMT_TAP_BETA,
            mgmt_mac=MGMT_BETA_MAC,
        )
        if wait_inner_ssh(SELLER_OPENWRT_IP, timeout=15):
            log.info("Seller OpenWrt base pre-provisioned, skipping serial")
        else:
            _provision_openwrt_serial("openwrt-seller", SELLER_OPENWRT_IP)
        if seller_proc.poll() is not None:
            raise RuntimeError(f"Seller OpenWrt VM exited during provisioning with rc={seller_proc.returncode}")
        if not wait_inner_ssh(SELLER_OPENWRT_IP):
            raise RuntimeError("Seller OpenWrt VM did not become reachable at managed IP")
        config.secondary_router_host = SELLER_OPENWRT_IP
        log.info("Seller OpenWrt VM SSH OK at %s", SELLER_OPENWRT_IP)
        configure_mgmt_nic(SELLER_OPENWRT_IP, MGMT_BETA_IP, MGMT_BETA_MAC)

    log.info("Starting Alpha OpenWrt VM...")
    reseller_proc = _launch_qemu(
        name="openwrt",
        memory_mb=512,
        cpus=1,
        disk_name="tollgate-poc.qcow2",
        tap_name="tg-poc-tap",
        mac="52:54:00:12:34:56",
        wan_tap=UPSTREAM_TAP_ALPHA if config.two_router else None,
        wan_mac=ALPHA_WAN_MAC if config.two_router else None,
        vsock_cid=10 if config.vwifi_enabled else None,
        mgmt_tap=MGMT_TAP_ALPHA,
        mgmt_mac=MGMT_ALPHA_MAC,
    )
    if wait_inner_ssh(OPENWRT_IP, timeout=15):
        log.info("OpenWrt base pre-provisioned, skipping serial")
    else:
        _provision_openwrt_serial("openwrt", OPENWRT_IP)
    if reseller_proc.poll() is not None:
        raise RuntimeError(f"Alpha OpenWrt VM exited during provisioning with rc={reseller_proc.returncode}")
    if not wait_inner_ssh(OPENWRT_IP):
        raise RuntimeError("OpenWrt VM did not become reachable")

    if config.two_router:
        configure_alpha_wan(OPENWRT_IP)

    log.info("Alpha OpenWrt VM SSH OK")
    configure_mgmt_nic(OPENWRT_IP, MGMT_ALPHA_IP, MGMT_ALPHA_MAC)

    # Fix asymmetric routing in single-router mode. Without a separate WAN
    # interface, all forwarded traffic leaves via br-lan (lan zone).  Return
    # traffic from the host goes directly to the client on the same bridge,
    # bypassing the OpenWrt VM.  Conntrack on the VM never sees the SYN-ACK
    # and marks the subsequent ACK as INVALID → fw4 drops it → client gets
    # ERR_CONNECTION_RESET.  Adding a masquerade rule via nft (without fw4
    # restart, which kills SSH) makes the VM NAT forwarded traffic so the
    # host replies to 10.99.99.1 (the VM), which then forwards back to the
    # client — symmetric routing restored.
    if not config.two_router:
        log.info("Applying nft masquerade for asymmetric routing fix")
        r = inner_ssh(
            OPENWRT_IP,
            "nft add table ip tollgate-asym 2>/dev/null; "
            "nft add chain ip tollgate-asym postrouting "
            "'{ type nat hook postrouting priority srcnat + 10 ; }' 2>/dev/null; "
            "nft add rule ip tollgate-asym postrouting "
            "oifname br-lan ct status ! dstnat masquerade 2>/dev/null; "
            "echo ASYM_OK",
            timeout=15,
        )
        if "ASYM_OK" in (r.stdout or ""):
            log.info("Asymmetric routing masquerade applied via nft")
        else:
            log.warning("nft masquerade failed (rc=%d): %s", r.returncode, (r.stderr or "")[:200])

    inner_ssh(
        OPENWRT_IP,
        f"uci add dhcp host 2>/dev/null; "
        f"uci set dhcp.@host[-1].mac='{DEBIAN_MAC}'; "
        f"uci set dhcp.@host[-1].ip='{DEBIAN_IP}'; "
        f"uci set dhcp.@host[-1].name='debian-client'; "
        f"uci commit dhcp; "
        f"/etc/init.d/dnsmasq restart 2>/dev/null; "
        f"echo DHCP_RESERVED",
        timeout=15,
    )

    log.info("Starting Debian VM (cached overlay)...")
    debian_seed = _virt_lab_workdir() / "images" / "debian-seed.iso"
    debian_proc = _launch_qemu(
        name="debian",
        memory_mb=1536,
        cpus=2,
        disk_name="debian-client.qcow2",
        tap_name="tg-poc-tap2",
        mac=DEBIAN_MAC,
        vsock_cid=20 if config.vwifi_enabled else None,
        mgmt_tap=MGMT_TAP_DEBIAN,
        mgmt_mac=MGMT_DEBIAN_MAC,
        seed_iso=str(debian_seed) if debian_seed.exists() else None,
    )
    time.sleep(30)
    if debian_proc.poll() is not None:
        raise RuntimeError(f"Debian VM exited before SSH with rc={debian_proc.returncode}")
    if not wait_inner_ssh(DEBIAN_IP, timeout=120):
        raise RuntimeError("Debian VM did not become reachable")
    log.info("Debian VM SSH OK")
    configure_mgmt_nic(DEBIAN_IP, MGMT_DEBIAN_IP, MGMT_DEBIAN_MAC)
def reset_chain_overlays(router_count: int) -> None:
    log.info("Resetting OpenWrt overlays for %d-router chain", router_count)
    rm_list = " ".join(f"overlays/{chain_disk_name(i)}" for i in range(router_count))
    create_list = " ".join(
        f'qemu-img create -f qcow2 -F qcow2 -b "$OWRT_BASE" overlays/{chain_disk_name(i)} >/dev/null'
        for i in range(router_count)
    )
    _run(
        "killall -9 qemu-system-x86_64 2>/dev/null || true; sleep 1; "
        f"cd {VIRT_LAB_WORKDIR} && "
        "OWRT_BASE=images/openwrt-base.qcow2; "
        "[ -f \"$OWRT_BASE\" ] || OWRT_BASE=../images/openwrt-base.qcow2; "
        "OWRT_BASE=$(readlink -f \"$OWRT_BASE\"); "
        f"rm -f {rm_list} && "
        f"{create_list}",
        timeout=60,
    )
    r = _run(f"test -f {VIRT_LAB_WORKDIR}/overlays/debian-client.qcow2 && echo DEBIAN_OVERLAY_OK", check=False)
    if "DEBIAN_OVERLAY_OK" not in r.stdout:
        _run(
            f"cd {VIRT_LAB_WORKDIR} && "
            "DEB_BASE=images/debian-12-base.qcow2; "
            "[ -f \"$DEB_BASE\" ] || DEB_BASE=../images/debian-12-base.qcow2; "
            "[ -f \"$DEB_BASE\" ] || DEB_BASE=images/debian-12-nocloud-amd64.qcow2; "
            "[ -f \"$DEB_BASE\" ] || DEB_BASE=../images/debian-12-nocloud-amd64.qcow2; "
            "DEB_BASE=$(readlink -f \"$DEB_BASE\"); "
            "qemu-img create -f qcow2 -F qcow2 -b \"$DEB_BASE\" overlays/debian-client.qcow2 >/dev/null && "
            "qemu-img resize --shrink overlays/debian-client.qcow2 10G >/dev/null 2>&1 || true",
            timeout=60,
        )


def start_chain_vms(config: WorkerConfig) -> None:
    from lib.cloud_lab.worker.network import (
        configure_chain_router_lan,
        configure_chain_router_wan,
        setup_chain_bridges,
    )

    n = config.effective_router_count
    setup_bridge()
    setup_chain_bridges(n)
    reset_chain_overlays(n)

    if config.vwifi_enabled:
        _run("modprobe vhost_vsock 2>/dev/null || true && chmod a+rw /dev/vhost-vsock 2>/dev/null || true", timeout=10)

    config.secondary_router_host = ""
    mgmt_ips: list[str] = []

    for i in range(n - 1, -1, -1):
        lan_tap = chain_lan_tap(i)
        lan_mac = chain_lan_mac(i)
        lan_ip = chain_lan_ip(i)
        host_ip = chain_host_ip(i)

        wan_tap = chain_wan_tap(i) if i < n - 1 else None
        wan_mac = chain_wan_mac(i) if i < n - 1 else None

        mgmt_tap = chain_mgmt_tap(i)
        mgmt_mac = chain_mgmt_mac(i)
        mgmt_ip = chain_mgmt_ip(i)

        name = f"openwrt-chain-{i}"
        log.info("[chain] Starting router[%d] VM (%s)...", i, name)

        proc = _launch_qemu(
            name=name,
            memory_mb=512,
            cpus=1,
            disk_name=chain_disk_name(i),
            tap_name=lan_tap,
            mac=lan_mac,
            wan_tap=wan_tap,
            wan_mac=wan_mac,
            mgmt_tap=mgmt_tap,
            mgmt_mac=mgmt_mac,
        )
        _provision_openwrt_serial(name, lan_ip, gateway=host_ip)
        if proc.poll() is not None:
            raise RuntimeError(f"router[{i}] VM exited during provisioning with rc={proc.returncode}")
        if not wait_inner_ssh(lan_ip):
            raise RuntimeError(f"router[{i}] VM did not become reachable at {lan_ip}")

        actual_mgmt = configure_chain_router_lan(i, n)
        mgmt_ips.append(actual_mgmt)

        if i < n - 1:
            configure_chain_router_wan(i)

        if i > 0:
            from lib.cloud_lab.constants import DEBIAN_IP as _DEBIAN_IP, DEBIAN_MAC as _DEBIAN_MAC
            inner_ssh(
                lan_ip,
                f"uci add dhcp host 2>/dev/null; "
                f"uci set dhcp.@host[-1].mac='{_DEBIAN_MAC}'; "
                f"uci set dhcp.@host[-1].ip='{_DEBIAN_IP}'; "
                f"uci commit dhcp; /etc/init.d/dnsmasq restart 2>/dev/null; echo DHCP_RESERVED",
                timeout=15,
            )

        log.info("[chain] router[%d] SSH OK at %s (lan=%s, mgmt=%s)", i, lan_ip, lan_ip, actual_mgmt)

    config.secondary_router_host = mgmt_ips[-2] if len(mgmt_ips) >= 2 else ""
    _start_chain_debian(config)

    env_hosts = " ".join(mgmt_ips)
    _run(f"echo 'TOLLGATE_CHAIN_ROUTER_HOSTS={env_hosts}' >> {os.environ.get('GITHUB_WORKSPACE', '/opt/tollgate-test')}/.env",
         timeout=5, check=False)
    log.info("[chain] All %d routers booted. mgmt_ips=[%s]", n, ", ".join(mgmt_ips))


def _start_chain_debian(config: WorkerConfig) -> None:
    log.info("Starting Debian VM (cached overlay)...")
    workdir = _virt_lab_workdir()
    debian_seed = workdir / "images" / "debian-seed.iso"
    debian_proc = _launch_qemu(
        name="debian",
        memory_mb=1536,
        cpus=2,
        disk_name="debian-client.qcow2",
        tap_name="tg-poc-tap2",
        mac=DEBIAN_MAC,
        mgmt_tap=MGMT_TAP_DEBIAN,
        mgmt_mac=MGMT_DEBIAN_MAC,
        seed_iso=str(debian_seed) if debian_seed.exists() else None,
    )
    time.sleep(30)
    if debian_proc.poll() is not None:
        raise RuntimeError(f"Debian VM exited before SSH with rc={debian_proc.returncode}")
    if not wait_inner_ssh(DEBIAN_IP, timeout=120):
        raise RuntimeError("Debian VM did not become reachable")
    log.info("Debian VM SSH OK")
    configure_mgmt_nic(DEBIAN_IP, MGMT_DEBIAN_IP, MGMT_DEBIAN_MAC)


def stop_inner_vms() -> None:
    _run("killall -9 qemu-system-x86_64 2>/dev/null || true", timeout=15, check=False)
def delete_self(config: WorkerConfig) -> None:
    if config.cloud == "shc":
        import json
        import urllib.request
        import urllib.error

        sid = int(config.service_id) if config.service_id else 0
        if not sid:
            log.warning("No SHC service_id — shutting down instead of cancelling")
            _run("shutdown -h now", timeout=10, check=False)
            return

        api_key = os.environ.get("SHC_API_KEY", "")
        if not api_key:
            log.warning("No SHC_API_KEY — shutting down instead of cancelling")
            _run("shutdown -h now", timeout=10, check=False)
            return

        base = "https://blesta.sovereignhybridcompute.com/user-api/v2"
        cancel_url = f"{base}/vm/{sid}/cancel"
        body = json.dumps({"immediate": True}).encode()
        log.info("Cancelling SHC VM service_id=%d", sid)

        try:
            req = urllib.request.Request(
                cancel_url, data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=30)
            log.info("SHC VM cancelled successfully")
        except urllib.error.HTTPError as e:
            resp_body = e.read().decode()
            idx = resp_body.find("{")
            if idx >= 0:
                resp_body = resp_body[idx:]
            try:
                data = json.loads(resp_body)
            except json.JSONDecodeError:
                data = {}

            conf_id = (
                data.get("confirmation", {})
                .get("structuredContent", {})
                .get("confirmation_id")
            )
            if conf_id:
                log.info("Confirming SHC cancel (confirmation_id=%s)", conf_id)
                req2 = urllib.request.Request(
                    cancel_url, data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                        "X-User-Api-Confirm": conf_id,
                    },
                    method="POST",
                )
                try:
                    urllib.request.urlopen(req2, timeout=30)
                    log.info("SHC VM cancelled successfully (confirmed)")
                    return
                except Exception as exc2:
                    log.warning("SHC cancel confirmation failed: %s", exc2)
            else:
                log.warning("SHC cancel failed: HTTP %d — %s", e.code, resp_body[:200])
        except Exception as exc:
            log.warning("SHC cancel request failed: %s", exc)

        log.warning("Falling back to shutdown")
        _run("shutdown -h now", timeout=10, check=False)
        return

    _run(
        f"gcloud compute instances delete {shlex.quote(config.vm_name)} "
        f"--project={shlex.quote(config.project)} --zone={shlex.quote(config.zone)} "
        "--delete-disks=all --quiet",
        timeout=120,
        check=False,
    )
