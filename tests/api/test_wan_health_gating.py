"""WAN-health endpoint exposure on the guest dataplane (#1146).

tollgate-module-basic-go#1146 adds an intentionally ungated
``GET /wan-health`` handler (upstream Wi-Fi snapshot + DFS toast hint).
The source-level security pass (PR review, 2026-11-12) verified the
handler itself — GET-only, fixed path, no secrets in the struct — and put
one decision to the product layer: the response carries the upstream
``ssid``, reachable by any client that can reach the port.

This suite answers the other half on the real dataplane: can a PRE-AUTH
guest on ``br-lan`` reach the endpoint at all? Two outcomes are
legitimate, and the test pins which one the artifact actually has:

* ``refused`` — the enforce/admin-scope nft rules reject the guest
  connection before the handler runs (the #754/#782 ordering). The ssid
  exposure is then de-facto br-private-only.
* ``reachable`` — the guest population can read the snapshot. That makes
  the upstream-ssid question a LIVE product decision; the test fails
  with the exposure named so the verdict table carries it, after first
  asserting the payload contains no credential material.

Until an artifact carrying #1146 ships, the suite skips with the PR
named — a skip is NOT-VERIFIED, never a vacuous pass (engineering
policy §2 L4).

Staging mirrors test_train_topology_scenarios.py: a netns + veth client
slaved into br-lan, torn down in finally. The destination is the router's
own br-lan address — an INPUT-path probe, not a forward probe.
"""

import re

import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]

PREREQ = ("wan-health ships with tollgate-module-basic-go#1146; this suite "
          "skips (NOT-VERIFIED) on artifacts predating it")

_CREDENTIAL_KEYS = ("password", "passwd", "secret", "nsec", "apikey",
                    "api_key", "psk", "privkey", "private_key")


def _stage_guest(router):
    """netns + veth pre-auth client on br-lan; returns (client_ip, gw_ip)."""
    tools = router.ssh(
        "test -d /sys/module/veth && test -x /usr/libexec/ip-full && echo yes"
    ).strip()
    if tools != "yes":
        pytest.skip(
            "DUT lacks kmod-veth / ip-full — run the image-doctor preflight "
            "(lab-preflight.sh section 9) and provision before this scenario"
        )
    gw_ip = router.ssh("uci -q get network.lan.ipaddr || ip -4 addr show br-lan | "
                       "grep -oE 'inet [0-9.]+' | head -1 | cut -d' ' -f2").strip()
    if not re.match(r"\d+\.\d+\.\d+\.\d+$", gw_ip):
        pytest.skip(f"cannot resolve the router's br-lan address: {gw_ip!r}")
    router.ssh(
        "IP=/usr/libexec/ip-full; "
        "$IP netns del tg-wanhealth 2>/dev/null; ip link del vh_s 2>/dev/null; "
        "$IP link add vh_s type veth peer name vh_c; "
        "ip link set vh_s master br-lan; ip link set vh_s up; "
        "$IP netns add tg-wanhealth; "
        "$IP link set vh_c netns tg-wanhealth; "
        "$IP -n tg-wanhealth addr add 192.0.2.99/24 dev vh_c; "
        "$IP -n tg-wanhealth link set vh_c up; "
        "$IP -n tg-wanhealth route add default via " + gw_ip,
        timeout=30,
    )
    return "192.0.2.99", gw_ip


def _teardown_guest(router):
    router.ssh(
        "IP=/usr/libexec/ip-full; "
        "$IP netns del tg-wanhealth 2>/dev/null; "
        "ip link del vh_s 2>/dev/null; true"
    )


def _probe(router, port, gw_ip):
    """One pre-auth GET /wan-health from the netns guest toward the router.

    Returns (outcome, body) with outcome in {refused, reachable, absent,
    other}. `absent` = the port answers but the path 404s (pre-#1146
    artifact).
    """
    out = router.ssh(
        f"ip netns exec tg-wanhealth curl -m 4 -s "
        f"-w '\\n__HTTP=%{{http_code}}__' http://{gw_ip}:{port}/wan-health 2>&1; "
        f"echo rc=$?",
        timeout=20,
    )
    m = re.search(r"rc=(\d+)", out)
    rc = int(m.group(1)) if m else -1
    hm = re.search(r"__HTTP=(\d+)__", out)
    code = int(hm.group(1)) if hm else None
    body = out[: hm.start()] if hm else out[: m.start() if m else len(out)]

    if "Operation not permitted" in out or "Connection refused" in out:
        # nft reject on the input path, or no listener: guests never
        # reach the handler.
        return "refused", body
    if code == 404:
        return "absent", body
    if rc == 0 and code == 200:
        return "reachable", body
    if rc == 28:
        # Timeout on the input path: packets silently dropped — treat as
        # refused-with-different-verdict, still not guest-reachable.
        return "refused", body
    return "other", f"rc={rc} code={code} body={body[:200]}"


def _find_admin_ports(router):
    """Ports the daemon's HTTP mux listens on, from netstat."""
    out = router.ssh("netstat -tlnp 2>/dev/null | awk '{print $4}' | "
                     "grep -oE '[0-9]+$' | sort -un | tr '\\n' ' '", timeout=15)
    return [p for p in (int(x) for x in out.split()) if p > 1024]


def test_wan_health_preauth_gating(router):
    """A pre-auth guest either cannot reach /wan-health (refused by the
    dataplane) or — if reachable — must not see credential material, and
    the exposure itself is surfaced as a failure, not silently passed."""
    ports = _find_admin_ports(router)
    if not ports:
        pytest.skip(f"no daemon HTTP listeners found on this DUT — {PREREQ}")

    client_ip, gw_ip = _stage_guest(router)
    try:
        seen_absent = False
        for port in ports:
            outcome, body = _probe(router, port, gw_ip)
            if outcome == "absent":
                seen_absent = True
                continue
            if outcome == "refused":
                continue
            if outcome == "reachable":
                keys = sorted(re.findall(r'"([A-Za-z_0-9]+)"\\s*:', body))
                leaked = [k for k in keys
                          if k.lower() in _CREDENTIAL_KEYS]
                assert not leaked, (
                    f"/wan-health leaked credential-keyed fields to a "
                    f"pre-auth guest on :{port}: {leaked}"
                )
                pytest.fail(
                    f"/wan-health is reachable pre-auth from br-lan on "
                    f":{port}; the ssid-exposure decision from #1146's "
                    f"review is therefore LIVE on this artifact — response "
                    f"keys: {keys}"
                )
            pytest.skip(
                f"indeterminate probe outcome on :{port}: {body} — recorded "
                f"NOT-VERIFIED; {PREREQ}"
            )
        if seen_absent:
            pytest.skip(f"/wan-health 404s on every listener — {PREREQ}")
        # Every probe refused: the strong outcome. Guests never reach the
        # handler on any daemon port.
    finally:
        _teardown_guest(router)
