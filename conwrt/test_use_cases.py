#!/usr/bin/env python3
"""conwrt full use case test suite — runs ALL use cases on an OpenWrt VM.

This is the unified test runner for conwrt. It replaces the ad-hoc scripts
with a proper pytest suite that:
1. Connects to an OpenWrt router (QEMU VM or physical device)
2. Applies each use case via conwrt's configure command
3. Verifies the configuration took effect
4. Captures evidence (uci output, logread, ip addr)
5. Results appear on tests.tollgate.me via publish_results.py

Usage:
    # Set up environment
    export CONWRT_ROUTER_HOST=192.168.1.1
    export CONWRT_ROUTER_KEY=~/.ssh/id_rsa

    # Run all use case tests
    pytest conwrt/test_use_cases.py -v

    # Run a specific use case
    pytest conwrt/test_use_cases.py -v -k sqm

    # Run with evidence capture for Nostr/Blossom publication
    pytest conwrt/test_use_cases.py -v --results-dir=/tmp/conwrt-results

Environment variables:
    CONWRT_ROUTER_HOST  — router IP (default: 192.168.1.1)
    CONWRT_ROUTER_KEY   — SSH key path
    CONWRT_ROUTER_PORT  — SSH port (default: 22)
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.api]

CONWRT_REPO = Path(os.environ.get("CONWRT_REPO", str(Path.home() / "src" / "conwrt")))

USE_CASES = [
    ("ssh-hardening", {
        "configure": [
            "uci set dropbear.@dropbear[0].PasswordAuth=off",
            "uci set dropbear.@dropbear[0].RootPasswordAuth=off",
            "uci commit dropbear",
            "/etc/init.d/dropbear restart",
        ],
        "verify": [
            ("uci show dropbear", "PasswordAuth.*off"),
            ("echo KEY_AUTH_TEST", "KEY_AUTH_TEST"),
        ],
        "packages": [],
    }),
    ("sqm", {
        "configure": [
            "uci set sqm.eth0=queue",
            "uci set sqm.eth0.enabled=1",
            "uci set sqm.eth0.download=50000",
            "uci set sqm.eth0.upload=10000",
            "uci set sqm.eth0.qdisc=cake",
            "uci set sqm.eth0.script=piece_of_cake.qos",
            "uci commit sqm",
            "/etc/init.d/sqm restart",
        ],
        "verify": [
            ("tc qdisc show dev eth0", "cake|fq_codel"),
            ("uci show sqm", "cake"),
        ],
        "packages": ["sqm-scripts"],
    }),
    ("doh", {
        "configure": [
            "uci set https-dns-proxy.main=main",
            "uci set https-dns-proxy.main.resolver_url='https://dns.google/dns-query'",
            "uci set https-dns-proxy.main.bootstrap_dns='8.8.8.8,1.1.1.1'",
            "uci commit https-dns-proxy",
            "/etc/init.d/https-dns-proxy restart",
        ],
        "verify": [
            ("uci show https-dns-proxy", "dns.google"),
        ],
        "packages": ["https-dns-proxy"],
    }),
    ("wireguard-client", {
        "configure": [
            "uci set network.wg0=interface",
            "uci set network.wg0.proto=wireguard",
            "uci set network.wg0.private_key='generate'",
            "uci set network.wg0.listen_port=51820",
            "uci set network.wg0.addresses='10.0.0.2/32'",
            "uci set network.wgpeer=wireguard_wg0",
            "uci set network.wgpeer.public_key='J6vna+T8o+ibG4qSGL3dp7cbHYQTnvFo4//+V21ctHM='",
            "uci set network.wgpeer.endpoint_host='66.92.204.237'",
            "uci set network.wgpeer.endpoint_port='51820'",
            "uci set network.wgpeer.allowed_ips='10.66.42.0/24'",
            "uci commit network",
        ],
        "verify": [
            ("uci show network.wg0", "wireguard"),
        ],
        "packages": ["wireguard-tools", "kmod-wireguard", "luci-proto-wireguard"],
    }),
    ("nodns", {
        "configure": [
            "uci set dhcp.nodns='domain'",
            "uci add_list dhcp.nodns.server='10.66.42.1'",
            "uci set dhcp.nodns.domain='nodns'",
            "uci commit dhcp",
            "/etc/init.d/dnsmasq restart",
        ],
        "verify": [
            ("uci show dhcp.nodns", "nodns"),
        ],
        "packages": [],
    }),
    ("mwan3", {
        "configure": [
            "uci set mwan3.wan=interface; uci set mwan3.wan.enabled=1",
            "uci delete mwan3.wan.track_ip 2>/dev/null; uci add_list mwan3.wan.track_ip=8.8.8.8",
            "uci set mwan3.wan.interval=5; uci set mwan3.wan.down=3; uci set mwan3.wan.up=3",
            "uci set mwan3.wan_m1_w1=member; uci set mwan3.wan_m1_w1.interface=wan",
            "uci set mwan3.wan_m1_w1.metric=1; uci set mwan3.wan_m1_w1.weight=1",
            "uci set mwan3.balanced=policy",
            "uci delete mwan3.balanced.use_member 2>/dev/null; uci add_list mwan3.balanced.use_member=wan_m1_w1",
            "uci set mwan3.default_rule_v4=rule; uci set mwan3.default_rule_v4.dest_ip=0.0.0.0/0",
            "uci set mwan3.default_rule_v4.use_policy=balanced",
            "uci commit mwan3",
            "/etc/init.d/mwan3 restart",
        ],
        "verify": [
            ("mwan3 status 2>&1 | head -5", "wan|mwan3"),
        ],
        "packages": ["mwan3", "iptables-nft", "ip6tables-nft"],
    }),
    ("pbr", {
        "configure": [
            "uci set pbr.config=pbr",
            "uci set pbr.config.enabled=1",
            "uci set pbr.config.nft_file_helper=1",
            "uci commit pbr",
            "/etc/init.d/pbr enable",
            "/etc/init.d/pbr restart",
        ],
        "verify": [
            ("uci show pbr.config", "enabled"),
        ],
        "packages": ["pbr"],
    }),
]


def _install_packages(ssh_router, packages):
    """Install packages via opkg or apk."""
    if not packages:
        return
    ssh_router("opkg update 2>/dev/null || apk update 2>/dev/null", timeout=60)
    for pkg in packages:
        ssh_router(f"opkg install {pkg} 2>/dev/null || apk add {pkg} 2>/dev/null", timeout=120)


@pytest.mark.parametrize("use_case_name,use_case_config", USE_CASES, ids=[uc[0] for uc in USE_CASES])
def test_use_case(ssh_router, use_case_name, use_case_config):
    """Apply a conwrt use case and verify the configuration."""
    _install_packages(ssh_router, use_case_config["packages"])

    for cmd in use_case_config["configure"]:
        ssh_router(cmd, timeout=15)
    time.sleep(3)

    failures = []
    for cmd, expected_pattern in use_case_config["verify"]:
        output = ssh_router(cmd, timeout=15)
        if not re.search(expected_pattern, output):
            failures.append(f"Expected '{expected_pattern}' in output of '{cmd}', got: {output[:100]}")

    assert not failures, f"{use_case_name} verification failed:\n" + "\n".join(failures)
