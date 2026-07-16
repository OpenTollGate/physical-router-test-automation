"""Cloud lab worker — network bridges and two-router topology."""

from __future__ import annotations

import json
import logging
import re
import shlex
import time

from lib.cloud_lab.constants import (
    BETA_BRIDGE,
    BETA_LAN_HOST_IP,
    BETA_LAN_IP,
    BETA_LAN_SUBNET,
    BETA_TAP,
    BETA_WAN_IP,
    LOCAL_MINT_HOST,
    MGMT_BETA_IP,
    MGMT_BRIDGE,
    MGMT_HOST_IP,
    MGMT_SUBNET,
    MGMT_TAP_ALPHA,
    MGMT_TAP_BETA,
    MGMT_TAP_DEBIAN,
    OPENWRT_IP,
    UPSTREAM_BRIDGE,
    UPSTREAM_TAP_ALPHA,
    UPSTREAM_TAP_BETA,
    VIRT_LAB_PASSWORD,
    VIRT_LAB_WORKDIR,
    chain_bridge,
    chain_host_ip,
    chain_lan_ip,
    chain_lan_tap,
    chain_mgmt_ip,
    chain_mgmt_tap,
    chain_subnet,
    chain_subnet_prefix,
    chain_wan_tap,
)
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.inner_ssh import inner_ssh
from lib.cloud_lab.worker.shell import _run, log


def _ensure_outer_wallet_balance(mint_url: str, needed: int) -> bool:
    """Warm up the outer VM's cashu wallet and ensure sufficient balance.

    The cashu CLI wallet DB must be initialised with the mint URL (keysets
    fetched) before any ``send`` command — otherwise the CLI raises
    ``KeyError: '<mint_url>'`` because the mint is not in its trusted list.

    This function:
      1. Calls ``cashu -h <url> -t balance`` to fetch keysets + check balance.
      2. If balance < needed, mints tokens via auto-settled invoice
         (``cashu -h <url> -t -y invoice <amount>``).

    Returns True if the wallet has >= *needed* sats after the call.
    """
    cashu_bin = "/opt/cashu-venv/bin/cashu"
    url = shlex.quote(mint_url)

    warmup_r = _run(
        f"{cashu_bin} -h {url} -t balance 2>&1",
        timeout=30,
        check=False,
    )

    balance = 0
    match = re.search(r"Balance:\s*(\d+)", warmup_r.stdout or "")
    if match:
        balance = int(match.group(1))

    if balance >= needed:
        log.info("[cashu] Outer wallet balance=%d (needed=%d) — OK", balance, needed)
        return True

    mint_amount = needed - balance + 50  # small buffer
    log.info("[cashu] Outer wallet balance=%d, minting %d sats...", balance, mint_amount)
    _run(
        f"{cashu_bin} -h {url} -t -y invoice {mint_amount} 2>&1",
        timeout=120,
        check=False,
    )

    check_r = _run(
        f"{cashu_bin} -h {url} -t balance 2>&1",
        timeout=30,
        check=False,
    )
    match = re.search(r"Balance:\s*(\d+)", check_r.stdout or "")
    if match:
        balance = int(match.group(1))

    if balance >= needed:
        log.info("[cashu] Outer wallet funded: balance=%d", balance)
        return True

    log.error("[cashu] Failed to fund outer wallet (balance=%d, needed=%d)", balance, needed)
    return False


def setup_bridge() -> None:
    _run(
        "sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1; "
        "ip link add name tg-poc-br type bridge 2>/dev/null || true; "
        "ip addr add 10.99.99.2/24 dev tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-br up; "
        "ip tuntap add dev tg-poc-tap mode tap user root 2>/dev/null || true; "
        "ip link set tg-poc-tap master tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-tap up; "
        "ip tuntap add dev tg-poc-tap2 mode tap user root 2>/dev/null || true; "
        "ip link set tg-poc-tap2 master tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-tap2 up; "
        "ip tuntap add dev tg-poc-tap3 mode tap user root 2>/dev/null || true; "
        "ip link set tg-poc-tap3 master tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-tap3 up; "
        "iptables -t nat -C POSTROUTING -s 10.99.99.0/24 ! -o tg-poc-br -j MASQUERADE 2>/dev/null || "
        "iptables -t nat -A POSTROUTING -s 10.99.99.0/24 ! -o tg-poc-br -j MASQUERADE; "
        f"mkdir -p {VIRT_LAB_WORKDIR}/run; "
        # Two-router upstream bridge
        f"ip link add name {UPSTREAM_BRIDGE} type bridge 2>/dev/null || true; "
        f"ip link set {UPSTREAM_BRIDGE} up; "
        f"ip tuntap add dev {UPSTREAM_TAP_ALPHA} mode tap user root 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_ALPHA} master {UPSTREAM_BRIDGE} 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_ALPHA} up; "
        f"ip tuntap add dev {UPSTREAM_TAP_BETA} mode tap user root 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_BETA} master {UPSTREAM_BRIDGE} 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_BETA} up; "
        f"ip link add name {MGMT_BRIDGE} type bridge 2>/dev/null || true; "
        f"ip addr add {MGMT_HOST_IP}/24 dev {MGMT_BRIDGE} 2>/dev/null || true; "
        f"ip link set {MGMT_BRIDGE} up; "
        f"ip tuntap add dev {MGMT_TAP_ALPHA} mode tap user root 2>/dev/null || true; "
        f"ip link set {MGMT_TAP_ALPHA} master {MGMT_BRIDGE} 2>/dev/null || true; "
        f"ip link set {MGMT_TAP_ALPHA} up; "
        f"ip tuntap add dev {MGMT_TAP_DEBIAN} mode tap user root 2>/dev/null || true; "
        f"ip link set {MGMT_TAP_DEBIAN} master {MGMT_BRIDGE} 2>/dev/null || true; "
        f"ip link set {MGMT_TAP_DEBIAN} up; "
        f"ip tuntap add dev {MGMT_TAP_BETA} mode tap user root 2>/dev/null || true; "
        f"ip link set {MGMT_TAP_BETA} master {MGMT_BRIDGE} 2>/dev/null || true; "
        f"ip link set {MGMT_TAP_BETA} up; "
        # Beta isolated LAN bridge
        f"ip link add name {BETA_BRIDGE} type bridge 2>/dev/null || true; "
        f"ip addr add {BETA_LAN_HOST_IP}/24 dev {BETA_BRIDGE} 2>/dev/null || true; "
        f"ip link set {BETA_BRIDGE} up; "
        f"ip tuntap add dev {BETA_TAP} mode tap user root 2>/dev/null || true; "
        f"ip link set {BETA_TAP} master {BETA_BRIDGE} 2>/dev/null || true; "
        f"ip link set {BETA_TAP} up; "
        # NAT for Beta's isolated LAN (needed for two-router internet access)
        f"iptables -t nat -C POSTROUTING -s {BETA_LAN_SUBNET} ! -o {BETA_BRIDGE} -j MASQUERADE 2>/dev/null || "
        f"iptables -t nat -A POSTROUTING -s {BETA_LAN_SUBNET} ! -o {BETA_BRIDGE} -j MASQUERADE",
        timeout=20,
    )
def configure_beta_lan(beta_lan_ip: str) -> None:
    log.info("Configuring Beta br-lan to isolated subnet %s", beta_lan_ip)
    inner_ssh(MGMT_BETA_IP, f"""
        uci set network.lan.ipaddr='{beta_lan_ip}'
        uci set network.lan.netmask='255.255.255.0'
        uci set network.lan.gateway=''
        uci set network.lan.dns=''
        uci commit network
        /etc/init.d/network restart
    """, timeout=30)
    time.sleep(8)
    r = inner_ssh(MGMT_BETA_IP, f"ip addr show br-lan | grep '{beta_lan_ip}'", timeout=10)
    if beta_lan_ip in r.stdout:
        log.info("Beta br-lan confirmed at %s", beta_lan_ip)
    else:
        log.warning("Beta br-lan may not have %s: %s", beta_lan_ip, r.stdout.strip()[-200:])

    inner_ssh(MGMT_BETA_IP, f"""
        ip route add {LOCAL_MINT_HOST}/32 via {BETA_LAN_HOST_IP} 2>/dev/null || true
        ip route add 10.99.99.0/24 via {BETA_LAN_HOST_IP} 2>/dev/null || true
        sed -i '/v1\\.testnut\\.lan/d' /etc/hosts
        echo '{LOCAL_MINT_HOST} v1.testnut.nutshell.lan v2.testnut.cdk.lan v2.testnut.nutshell.lan \
testnut.cdk.lan testnut.nutshell.lan testnut.v1.nutshell.lan v1.testnut.lan' >> /etc/hosts
    """, timeout=15)
def configure_beta_upstream(beta_ip: str) -> None:
    log.info("Configuring Beta as upstream DHCP server + NAT gateway")
    inner_ssh(beta_ip, """
        uci set network.upstream=interface
        uci set network.upstream.proto='static'
        uci set network.upstream.device='eth1'
        uci set network.upstream.ipaddr='10.99.98.1'
        uci set network.upstream.netmask='255.255.255.0'
        uci commit network

        uci set dhcp.upstream=dhcp
        uci set dhcp.upstream.interface='upstream'
        uci set dhcp.upstream.start='10'
        uci set dhcp.upstream.limit='50'
        uci set dhcp.upstream.leasetime='2m'
        uci commit dhcp

        # Assign upstream to lan zone so DHCP/DNS traffic is accepted
        uci add_list firewall.@zone[0].network='upstream'
        uci commit firewall

        uci set network.lan.gateway='10.99.96.2'
        uci commit network

        uci add_list dhcp.@dnsmasq[0].server='8.8.8.8'
        uci add_list dhcp.@dnsmasq[0].server='8.8.4.4'
        uci commit dhcp

        /etc/init.d/network restart
        /etc/init.d/firewall restart
        /etc/init.d/dnsmasq restart

        nft add table ip tollgate-nat 2>/dev/null || true
        nft add chain ip tollgate-nat postrouting "{ type nat hook postrouting priority srcnat ; policy accept ; }" 2>/dev/null || true
        nft add rule ip tollgate-nat postrouting ip saddr 10.99.98.0/24 oifname "br-lan" masquerade 2>/dev/null || true
        nft add rule ip filter forward iifname "eth1" accept 2>/dev/null || true
        nft add rule ip filter forward oifname "eth1" ct state established,related accept 2>/dev/null || true
    """, timeout=45)
    time.sleep(8)
    r = inner_ssh(beta_ip, "pgrep -f dnsmasq >/dev/null && echo DHCP_OK", timeout=10)
    if "DHCP_OK" not in r.stdout:
        log.warning("Beta DHCP server may not be running")
    else:
        log.info("Beta DHCP server confirmed running")
def configure_alpha_wan(alpha_ip: str) -> None:
    log.info("Configuring Alpha eth1 as WAN (DHCP from Beta)")
    inner_ssh(alpha_ip, """
        uci set network.wan=interface
        uci set network.wan.proto='dhcp'
        uci set network.wan.device='eth1'
        uci commit network
        /etc/init.d/network restart
    """, timeout=30)
    # Force DHCP renewal — the initial boot attempt may have timed out
    inner_ssh(alpha_ip, "ifdown wan 2>/dev/null; sleep 2; ifup wan", timeout=15)
    time.sleep(12)
    r = inner_ssh(alpha_ip, "ip addr show eth1 2>/dev/null | grep 'inet '", timeout=10)
    if "10.99.98" in r.stdout:
        log.info("Alpha WAN got DHCP lease from Beta")
    else:
        log.warning("Alpha may not have received DHCP lease: %s", r.stdout.strip()[-200:])
def configure_two_router_payment(config: WorkerConfig, chosen_mint_url: str) -> None:
    """Configure Beta as merchant and Alpha as reseller with funded wallet."""
    if not config.two_router:
        return

    log.info("[two-router] Configuring Beta as upstream merchant...")

    beta_config = json.loads(
        inner_ssh(MGMT_BETA_IP, "cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10).stdout.strip()
        or "{}"
    )
    beta_config["accepted_mints"] = [{
        "url": chosen_mint_url,
        "min_balance": 0,
        "balance_tolerance_percent": 0,
        "payout_interval_seconds": 60,
        "min_payout_amount": 0,
        "price_per_step": 1,
        "price_unit": "sats",
        "purchase_min_steps": 0,
    }]
    beta_config["metric"] = "milliseconds"
    beta_config["step_size"] = 60000
    beta_config["margin"] = 0
    beta_config["profit_share"] = [{"factor": 1.0, "identity": "owner"}]

    beta_config_json = json.dumps(beta_config)
    inner_ssh(
        MGMT_BETA_IP,
        f"cat > /etc/tollgate/config.json << 'BETACFG'\n{beta_config_json}\nBETACFG",
        timeout=15,
    )
    inner_ssh(MGMT_BETA_IP, "/etc/init.d/tollgate-wrt restart", timeout=30)
    time.sleep(8)

    for attempt in range(15):
        r = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://{BETA_LAN_IP}:2121/ || true", timeout=10, check=False)
        if "200" in r.stdout:
            log.info("[two-router] Beta backend healthy (attempt %d)", attempt + 1)
            break
        time.sleep(2)
    else:
        log.warning("[two-router] Beta backend may not be healthy — continuing anyway")

    log.info("[two-router] Configuring Alpha as reseller...")

    alpha_config = json.loads(
        inner_ssh(OPENWRT_IP, "cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10).stdout.strip()
        or "{}"
    )
    alpha_config["reseller_mode"] = True

    ignore_ifaces = alpha_config.get("upstream_detector", {}).get("ignore_interfaces", [])
    allowed = {"lo", "docker0", "br-lan", "hostap0"}
    alpha_config.setdefault("upstream_detector", {})["ignore_interfaces"] = [
        iface for iface in ignore_ifaces if iface in allowed
    ]

    alpha_config_json = json.dumps(alpha_config)
    inner_ssh(
        OPENWRT_IP,
        f"cat > /etc/tollgate/config.json << 'ALPHACFG'\n{alpha_config_json}\nALPHACFG",
        timeout=15,
    )
    inner_ssh(OPENWRT_IP, "/etc/init.d/tollgate-wrt restart", timeout=30)
    time.sleep(8)

    for attempt in range(15):
        r = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://{OPENWRT_IP}:2121/ || true", timeout=10, check=False)
        if "200" in r.stdout:
            log.info("[two-router] Alpha backend healthy (attempt %d)", attempt + 1)
            break
        time.sleep(2)
    else:
        log.warning("[two-router] Alpha backend may not be healthy — continuing anyway")

    log.info("[two-router] Funding Alpha's wallet via cashu CLI...")

    mint_url_arg = shlex.quote(chosen_mint_url)
    _ensure_outer_wallet_balance(chosen_mint_url, 100)
    token_r = _run(
        f"/opt/cashu-venv/bin/cashu -h {mint_url_arg} send 100 --legacy 2>&1",
        timeout=60,
        check=False,
    )
    token_lines = [line.strip() for line in (token_r.stdout or "").splitlines() if line.strip().startswith("cashu")]
    if not token_lines:
        log.error("[two-router] Failed to mint token for Alpha wallet: %s", (token_r.stdout or "")[-300:])
        return

    token = token_lines[0]
    log.info("[two-router] Minted token (%d chars), funding Alpha wallet...", len(token))

    fund_r = inner_ssh(
        OPENWRT_IP,
        f"tollgate wallet fund '{token}'",
        timeout=30,
    )
    log.info("[two-router] wallet fund result: %s", fund_r.stdout.strip()[-200:] if fund_r.stdout else "(no output)")

    bal_r = inner_ssh(OPENWRT_IP, "tollgate wallet balance", timeout=10)
    balance = bal_r.stdout.strip() if bal_r.stdout else ""
    if balance and any(c.isdigit() for c in balance):
        log.info("[two-router] Alpha wallet balance: %s", balance)
    else:
        log.warning("[two-router] Could not verify Alpha wallet balance: %s", balance[-200:])


# ── Multi-hop chain topology (N >= 2 routers) ──────────────
# Each router[i] br-lan is on chain_bridge(i) with subnet chain_subnet(i).
# router[i-1]'s eth1 connects to chain_bridge(i) (the upstream link).
# The topmost router gets Host NAT on its bridge for internet.

def setup_chain_bridges(router_count: int) -> None:
    """Create all bridges, TAPs, and NAT rules for an N-router chain.

    Must be called AFTER setup_bridge() (which creates mgmt-br and tg-poc-br).
    """
    parts: list[str] = []

    for i in range(1, router_count):
        bridge = chain_bridge(i)
        host_ip = chain_host_ip(i)
        parts.append(f"ip link add name {bridge} type bridge 2>/dev/null || true")
        parts.append(f"ip addr add {host_ip}/24 dev {bridge} 2>/dev/null || true")
        parts.append(f"ip link set {bridge} up")

    for i in range(router_count):
        bridge = chain_bridge(i)

        lan_tap = chain_lan_tap(i)
        parts.append(f"ip tuntap add dev {lan_tap} mode tap user root 2>/dev/null || true")
        parts.append(f"ip link set {lan_tap} master {bridge} 2>/dev/null || true")
        parts.append(f"ip link set {lan_tap} up")

        if i < router_count - 1:
            wan_tap = chain_wan_tap(i)
            upstream_bridge = chain_bridge(i + 1)
            parts.append(f"ip tuntap add dev {wan_tap} mode tap user root 2>/dev/null || true")
            parts.append(f"ip link set {wan_tap} master {upstream_bridge} 2>/dev/null || true")
            parts.append(f"ip link set {wan_tap} up")

        mgmt_tap = chain_mgmt_tap(i)
        parts.append(f"ip tuntap add dev {mgmt_tap} mode tap user root 2>/dev/null || true")
        parts.append(f"ip link set {mgmt_tap} master {MGMT_BRIDGE} 2>/dev/null || true")
        parts.append(f"ip link set {mgmt_tap} up")

        if i == router_count - 1:
            subnet = chain_subnet(i)
            parts.append(
                f"iptables -t nat -C POSTROUTING -s {subnet} ! -o {bridge} -j MASQUERADE 2>/dev/null || "
                f"iptables -t nat -A POSTROUTING -s {subnet} ! -o {bridge} -j MASQUERADE"
            )

    _run("; ".join(parts), timeout=30)
    log.info("[chain] Created %d bridges + TAPs for %d-router chain", router_count, router_count)


def configure_chain_router_lan(router_index: int, router_count: int) -> str:
    from lib.cloud_lab.constants import chain_lan_mac, chain_mgmt_mac

    lan_ip = chain_lan_ip(router_index)
    host_ip = chain_host_ip(router_index)
    mgmt_ip = chain_mgmt_ip(router_index)
    lan_mac = chain_lan_mac(router_index)
    mgmt_mac = chain_mgmt_mac(router_index)

    log.info("[chain] Configuring router[%d] br-lan=%s mgmt=%s", router_index, lan_ip, mgmt_ip)

    inner_ssh(lan_ip, f"""
        uci set network.lan.ipaddr='{lan_ip}'
        uci set network.lan.netmask='255.255.255.0'
        uci set network.lan.gateway='{host_ip}'
        uci set network.lan.dns='8.8.8.8'
        uci commit network
        /etc/init.d/network restart
    """, timeout=30)
    time.sleep(8)

    if router_index < router_count - 1:
        inner_ssh(lan_ip, f"""
            uci set dhcp.lan.start='10'
            uci set dhcp.lan.limit='50'
            uci set dhcp.lan.leasetime='2m'
            uci commit dhcp
            /etc/init.d/dnsmasq restart 2>/dev/null || true
            nft add table ip tollgate-nat 2>/dev/null || true
            nft add chain ip tollgate-nat postrouting "{{ type nat hook postrouting priority srcnat ; policy accept ; }}" 2>/dev/null || true
            nft add rule ip tollgate-nat postrouting ip saddr {chain_subnet(router_index)} oifname "br-lan" masquerade 2>/dev/null || true
            nft add rule ip filter forward iifname "br-lan" accept 2>/dev/null || true
        """, timeout=30)
        time.sleep(3)

    _configure_chain_mgmt_nic(lan_ip, mgmt_ip, mgmt_mac)
    return mgmt_ip


def configure_chain_router_wan(router_index: int) -> None:
    lan_ip = chain_lan_ip(router_index)
    upstream_subnet = chain_subnet(router_index + 1)
    upstream_gw = chain_lan_ip(router_index + 1)

    log.info("[chain] Configuring router[%d] eth1 as WAN (DHCP from %s)", router_index, upstream_gw)

    inner_ssh(lan_ip, """
        uci set network.wan=interface
        uci set network.wan.proto='dhcp'
        uci set network.wan.device='eth1'
        uci commit network
        /etc/init.d/network restart
    """, timeout=30)
    inner_ssh(lan_ip, "ifdown wan 2>/dev/null; sleep 2; ifup wan", timeout=15)
    time.sleep(12)

    r = inner_ssh(lan_ip, "ip addr show eth1 2>/dev/null | grep 'inet '", timeout=10)
    expected_prefix = f"10.99.{chain_subnet_prefix(router_index + 1)}."
    if expected_prefix in r.stdout:
        log.info("[chain] router[%d] WAN got DHCP lease", router_index)
    else:
        log.warning("[chain] router[%d] WAN may not have DHCP: %s", router_index, r.stdout.strip()[-200:])


def _configure_chain_mgmt_nic(guest_ip: str, mgmt_ip: str, mgmt_mac: str) -> None:
    from lib.cloud_lab.worker.vms import configure_mgmt_nic
    configure_mgmt_nic(guest_ip, mgmt_ip, mgmt_mac)


def configure_chain_payment(config: WorkerConfig, chosen_mint_url: str) -> None:
    """Configure the multi-hop payment chain.

    Topmost router = merchant (direct mint access).
    All lower routers = resellers (pay upstream for access).
    Each router gets its wallet funded so it can pay the one above.
    """
    n = config.effective_router_count
    if n < 2:
        return

    log.info("[chain] Configuring %d-hop payment chain...", n)

    _ensure_outer_wallet_balance(chosen_mint_url, (n - 1) * 100)

    for i in range(n - 1, -1, -1):
        mgmt_ip = chain_mgmt_ip(i)
        lan_ip = chain_lan_ip(i)

        router_config = json.loads(
            inner_ssh(mgmt_ip, "cat /etc/tollgate/config.json 2>/dev/null || echo '{}'", timeout=10).stdout.strip()
            or "{}"
        )

        if i == n - 1:
            router_config["accepted_mints"] = [{
                "url": chosen_mint_url,
                "min_balance": 0,
                "balance_tolerance_percent": 0,
                "payout_interval_seconds": 60,
                "min_payout_amount": 0,
                "price_per_step": 1,
                "price_unit": "sats",
                "purchase_min_steps": 0,
            }]
            router_config["metric"] = "milliseconds"
            router_config["step_size"] = 60000
            router_config["margin"] = 0
            router_config["profit_share"] = [{"factor": 1.0, "identity": "owner"}]
            log.info("[chain] router[%d] = merchant (topmost)", i)
        else:
            router_config["reseller_mode"] = True
            ignore_ifaces = router_config.get("upstream_detector", {}).get("ignore_interfaces", [])
            allowed = {"lo", "docker0", "br-lan", "hostap0"}
            router_config.setdefault("upstream_detector", {})["ignore_interfaces"] = [
                iface for iface in ignore_ifaces if iface in allowed
            ]
            log.info("[chain] router[%d] = reseller (pays upstream)", i)

        config_json = json.dumps(router_config)
        inner_ssh(
            mgmt_ip,
            f"cat > /etc/tollgate/config.json << 'CHAINCFG'\n{config_json}\nCHAINCFG",
            timeout=15,
        )
        inner_ssh(mgmt_ip, "/etc/init.d/tollgate-wrt restart", timeout=30)
        time.sleep(8)

        for attempt in range(15):
            r = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://{lan_ip}:2121/ || true", timeout=10, check=False)
            if "200" in r.stdout:
                log.info("[chain] router[%d] backend healthy (attempt %d)", i, attempt + 1)
                break
            time.sleep(2)
        else:
            log.warning("[chain] router[%d] backend may not be healthy — continuing", i)

        if i < n - 1:
            upstream_mgmt = chain_mgmt_ip(i + 1)
            upstream_lan = chain_lan_ip(i + 1)
            log.info("[chain] Funding router[%d] wallet via upstream router[%d]...", i, i + 1)

            mint_url_arg = shlex.quote(chosen_mint_url)
            token_r = _run(
                f"/opt/cashu-venv/bin/cashu -h {mint_url_arg} send 100 --legacy 2>&1",
                timeout=60,
                check=False,
            )
            token_lines = [line.strip() for line in (token_r.stdout or "").splitlines() if line.strip().startswith("cashu")]
            if not token_lines:
                log.error("[chain] Failed to mint token for router[%d]: %s", i, (token_r.stdout or "")[-300:])
                continue

            token = token_lines[0]
            fund_r = inner_ssh(mgmt_ip, f"tollgate wallet fund '{token}'", timeout=30)
            log.info("[chain] router[%d] wallet fund result: %s", i, fund_r.stdout.strip()[-200:] if fund_r.stdout else "(no output)")
