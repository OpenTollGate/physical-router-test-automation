"""Stop-ship train topology scenarios (#754/#755/#756) as standing suite cases.

These scenarios were bench-verified by hand on 2026-10-09 (ai-legion overlay
VM + mac80211_hwsim, see the tollgate-module-basic-go 0.6.0 stop-ship lane).
This file turns them into suite cases so they stop living in /tmp.

They exercise the DEPLOYED package's behavior — the nft fragments, the
renderers and 99-tollgate-setup as shipped on the DUT — so they are green
only when the package under test carries the stop-ship train
(#782 → #786). Against a pre-train package (e.g. rc1) they skip loudly:
that skip IS drift detection — the campaign report names the missing train.

Venue-safety design (all three scenarios):
- Topology staging stays in **uci staging** (uci set/add/delete, never
  commit+reload): assertions read the staging area, a finally-block
  ``uci revert``s everything. The fixture's network never moves.
- The #754 triad needs a real FORWARD path with oif != portal bridge; the
  poc venue is single-armed (br-lan carries both clients and the uplink),
  which neuters ``oifname != $tg_portal_if``. A dummy WAN device plus a
  static neighbor gives a genuine forward leg without touching the lab's
  fabric.
"""

import os
import re

import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]

TRAIN_MARKER = "/usr/local/bin/tollgate-nds-preauth-render"
ENFORCE = "/etc/nftables.d/20-nds-enforce.nft"
SETUP = "/etc/uci-defaults/99-tollgate-setup"

# An external-but-routable-nowhere test destination (TEST-NET-2). The dummy
# WAN + static neighbor make the forward leg real; no listener is needed —
# the nft VERDICT decides the outcome (reject = fast ICMP refusal, accept =
# egress into the dummy and a timeout).
TEST_DST = "198.51.100.7"
TEST_PORT = 8878


def _train_present(router):
    """The stop-ship train's renderers/fragments exist on the DUT."""
    return router.ssh(f"test -x {TRAIN_MARKER} && test -f {ENFORCE} && echo yes").strip() == "yes"


def _train_skip():
    pytest.skip(
        "stop-ship train not deployed on this DUT (no tollgate-nds-preauth-render / "
        "20-nds-enforce.nft). Deploy the train package (#782-#786) to run the "
        "topology scenarios — this skip is the drift signal."
    )


def _uci_revert_all(router):
    for cfg in ("network", "wireless", "dhcp", "firewall", "nodogsplash"):
        router.ssh(f"uci revert {cfg} 2>/dev/null || true")


# ---------------------------------------------------------------------------
# Scenario 1 — #756 class 1: the AP rebind is existence-checked
# ---------------------------------------------------------------------------


def test_splitplane_ap_adoption_preserves_operator_section(router):
    """network.lan absent → setup adopts the portal plane and never binds an
    AP to a network that does not exist; operator SSID and credentials on the
    adopted section are preserved (the rc1 damage was: rebind to deleted
    'lan' + silent SSID rewrite)."""
    if not _train_present(router):
        _train_skip()
    has_resolver = router.ssh(f"grep -c resolve_portal_network {SETUP} 2>/dev/null").strip() not in ("", "0")
    if not has_resolver:
        pytest.skip(f"{SETUP} predates the #756 guard (no resolve_portal_network)")

    try:
        # Stage the bench-verified split-plane shape — STAGING ONLY.
        router.ssh(
            "uci set network.portal_dev='device'; uci set network.portal_dev.type='bridge'; "
            "uci set network.portal_dev.name='br-portal'; "
            "uci set network.portal='interface'; uci set network.portal.device='br-portal'; "
            "uci set network.portal.proto='static'; "
            "uci delete network.lan; "
            "uci set wireless.bench_ap='wifi-iface'; uci set wireless.bench_ap.device='radio0'; "
            "uci set wireless.bench_ap.mode='ap'; uci set wireless.bench_ap.ssid='BenchPortal'; "
            "uci set wireless.bench_ap.network='portal'; "
            "uci set wireless.bench_ap.encryption='psk2'; "
            "uci set wireless.bench_ap.key='benchportalpass'; "
            "uci set nodogsplash.@nodogsplash[0].gatewayinterface='br-portal'"
        )
        # Drive the DEPLOYED setup's own function (the ssid-harness discipline:
        # source above the driver marker).
        out = router.ssh(
            f"awk '/^# -- driver/{{exit}} {{print}}' {SETUP} > /tmp/train-lib.sh && "
            ". /tmp/train-lib.sh; CODE=9C3F; GATEWAY_NAME=TollGate-9C3F; "
            "LOGFILE=/tmp/train-scenario.log; : > $LOGFILE; "
            "setup_band_ap radio0 tollgate_2g_open >/dev/null 2>&1; "
            "echo binding=$(uci -q get wireless.bench_ap.network 2>/dev/null); "
            "echo ssid=$(uci -q get wireless.bench_ap.ssid 2>/dev/null); "
            "echo enc=$(uci -q get wireless.bench_ap.encryption 2>/dev/null); "
            "echo key=$(uci -q get wireless.bench_ap.key 2>/dev/null)"
        )
        facts = dict(
            line.split("=", 1) for line in out.strip().splitlines() if "=" in line
        )
        assert facts.get("binding") == "portal", (
            f"AP must adopt the portal plane, never the deleted 'lan' (#756): {facts}"
        )
        assert facts.get("ssid") == "BenchPortal", (
            f"operator SSID must survive the one-way rewrite: {facts}"
        )
        assert facts.get("enc") == "psk2" and facts.get("key") == "benchportalpass", (
            f"operator credentials must be preserved, never forced open: {facts}"
        )
    finally:
        _uci_revert_all(router)


# ---------------------------------------------------------------------------
# Scenario 2 — #754: the pre-auth forward allowlist actually executes
# ---------------------------------------------------------------------------


def _forward_probe(router, client_ip):
    """One pre-auth forward attempt; returns 'refused' | 'timeout' | other."""
    out = router.ssh(
        f"ip netns exec tg-scenario curl -m 4 -s -o /dev/null -w '%{{http_code}}' "
        f"--interface {client_ip} http://{TEST_DST}:{TEST_PORT}/ 2>&1; echo rc=$?",
        timeout=20,
    )
    m = re.search(r"rc=(\d+)", out)
    rc = int(m.group(1)) if m else -1
    if rc == 7:
        return "refused"  # ICMP port-unreachable: the enforce reject
    if rc == 28:
        return "timeout"  # egressed into the dummy: an accept let it pass
    return f"other(rc={rc})"


def _scenario_client(router):
    """netns + veth client on the portal bridge + dummy WAN forward path.

    Returns the client IP, or skips when the DUT lacks the tools the
    image-doctor gates (veth, ip-full).
    """
    tools = router.ssh(
        "test -d /sys/module/veth && test -x /usr/libexec/ip-full && echo yes"
    ).strip()
    if tools != "yes":
        pytest.skip(
            "DUT lacks kmod-veth / ip-full — run the image-doctor preflight "
            "(lab-preflight.sh section 9) and provision before this scenario"
        )
    router.ssh(
        "IP=/usr/libexec/ip-full; "
        "$IP netns del tg-scenario 2>/dev/null; ip link del veth_s 2>/dev/null; "
        "$IP link add veth_s type veth peer name veth_c; "
        "ip link set veth_s master br-lan; ip link set veth_s up; "
        "$IP link set veth_c netns tg-scenario 2>/dev/null || "
        "$IP netns add tg-scenario && $IP link set veth_c netns tg-scenario; "
        "$IP netns exec tg-scenario ip addr add 10.99.99.201/24 dev veth_c; "
        "$IP netns exec tg-scenario ip link set veth_c up; "
        "$IP netns exec tg-scenario ip link set lo up; "
        "$IP netns exec tg-scenario ip route add default via 10.99.99.1; "
        "ip link del wan0 2>/dev/null; ip link add wan0 type dummy; ip link set wan0 up; "
        f"ip route replace {TEST_DST}/32 dev wan0; "
        f"ip neigh replace {TEST_DST} lladdr 02:00:00:00:00:07 dev wan0 nud permanent"
    )
    return "10.99.99.201"


def _teardown_scenario_client(router):
    router.ssh(
        "IP=/usr/libexec/ip-full; $IP netns del tg-scenario 2>/dev/null; "
        "ip link del veth_s 2>/dev/null; ip link del wan0 2>/dev/null; "
        f"ip route del {TEST_DST}/32 dev wan0 2>/dev/null; true"
    )


def test_preauth_allowlist_triad_counters(router):
    """empty allowlist → pre-auth forward REJECTED (reject counter climbs);
    entry + render → ACCEPTED (generated in-chain accept counter climbs);
    entry removed → rejected again. On rc1 this scenario cannot pass — the
    allowlist compiled at priority 0 was dead letter (#754)."""
    if not _train_present(router):
        _train_skip()
    client_ip = _scenario_client(router)
    saved_enforce = None
    try:
        saved_enforce = router.ssh(f"cat {ENFORCE} 2>/dev/null")
        entry = f"allow tcp port {TEST_PORT} to {TEST_DST}"

        def render(entry_present: bool):
            router.ssh(
                f"uci {'add' if entry_present else 'del'}_list "
                f"nodogsplash.@nodogsplash[0].preauthenticated_users='{entry}'; "
                f"{TRAIN_MARKER}; fw4 reload >/dev/null 2>&1"
            )

        def counter(needle: str) -> int:
            out = router.ssh(f"nft list chain inet fw4 nds_enforce_forward 2>/dev/null")
            total = 0
            for line in out.splitlines():
                if needle in line:
                    m = re.search(r"counter packets (\d+)", line)
                    if m:
                        total += int(m.group(1))
            return total

        # A: empty allowlist → the reject owns the flow.
        router.ssh(
            "uci -q delete nodogsplash.@nodogsplash[0].preauthenticated_users; "
            f"{TRAIN_MARKER}; fw4 reload >/dev/null 2>&1"
        )
        rej0 = counter("reject")
        assert _forward_probe(router, client_ip) == "refused", (
            "empty allowlist must leave the pre-auth forward flow rejected (#754)"
        )
        assert counter("reject") > rej0, "the enforce reject counter must climb"

        # B: allowlisted → the generated in-chain accept owns the flow.
        render(True)
        acc_rule = f"dport {{ {TEST_PORT} }}"
        assert acc_rule.replace(" ", "") in router.ssh(
            f"nft list chain inet fw4 nds_enforce_forward"
        ).replace(" ", ""), "the renderer must place the accept INSIDE the enforce chain"
        acc0 = counter(acc_rule)
        assert _forward_probe(router, client_ip) == "timeout", (
            "the allowlisted flow must pass the enforce chain (accept executes)"
        )
        assert counter(acc_rule) > acc0, "the generated accept counter must climb"

        # C: entry removed → rejected again (convergence is reversible).
        render(False)
        assert _forward_probe(router, client_ip) == "refused", (
            "removing the entry must return the flow to rejected"
        )
    finally:
        _teardown_scenario_client(router)
        router.ssh(
            "uci -q delete nodogsplash.@nodogsplash[0].preauthenticated_users 2>/dev/null; true"
        )
        _uci_revert_all(router)
        if saved_enforce:
            router.ssh(f"cat > {ENFORCE} <<'FRAG'\n{saved_enforce}FRAG")
        router.ssh("fw4 reload >/dev/null 2>&1; true")


# ---------------------------------------------------------------------------
# Scenario 3 — #755: a skipped firewall zone ref is never silent
# ---------------------------------------------------------------------------


def test_zone_ref_skip_is_loud(router):
    """A tollgate_in.src naming a nonexistent zone must be named in syslog by
    the deployed assert (fw4 itself skips the section with exit 0 and no
    logread trace — the silent-enforcement-loss class)."""
    if not _train_present(router):
        _train_skip()
    has_assert = router.ssh(
        f"grep -c assert_firewall_zone_refs {SETUP} 2>/dev/null"
    ).strip() not in ("", "0")
    if not has_assert:
        pytest.skip(f"{SETUP} predates the #755 fail-loudly assert")

    try:
        router.ssh("uci set firewall.tollgate_in.src='zone-that-does-not-exist'")
        out = router.ssh(
            f"awk '/^# -- driver/{{exit}} {{print}}' {SETUP} > /tmp/train-lib.sh && "
            ". /tmp/train-lib.sh; LOGFILE=/tmp/train-scenario.log; : > $LOGFILE; "
            "assert_firewall_zone_refs >/dev/null 2>&1; "
            "grep -c 'does not exist' $LOGFILE; "
            "logread | grep -c 'references missing zone' || true"
        )
        lines = [l for l in out.strip().splitlines() if l.strip().isdigit()]
        setup_hits, syslog_hits = (int(lines[0]), int(lines[1])) if len(lines) >= 2 else (0, 0)
        assert setup_hits >= 1, "the missing zone must be named in the setup log"
        assert syslog_hits >= 1, "the missing zone must reach syslog (the logread line)"
    finally:
        _uci_revert_all(router)
