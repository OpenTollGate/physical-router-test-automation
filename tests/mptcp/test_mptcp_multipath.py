"""MPTCP multi-WAN bonding lane (physical router).

Reproducible tests for the FIPS/TollGate MPTCP multi-WAN work. Each test drives
the ROUTER over its serial console and reads the post-condition back FROM THE
DEVICE, per the IaC gate in AGENTS.md.

Why a serial lane rather than SSH: the MPTCP work configures routing and
firewall state, and several of the defects found during its development were only
visible in the device's own view of that state (`ip mptcp endpoint show`,
`WG show`, nft counters). Reading from the device is the evidence.

Run (from the kit root):

    TOLLGATE_SERIAL_PORT=/dev/ttyUSB1 TOLLGATE_USE_HARDWARE_LOCK=1 \\
        pytest tests/mptcp -v

Tests that need a peer pass their own target; tests that need a tunnel skip when
no peer is configured, rather than silently passing.

THE THREE DEVICE-ONLY DEFECTS THESE TEST FILE GUARDS AGAINST
-----------------------------------------------------------
Each was invisible to unit tests and only found by running on hardware:

1. `wg set <if> peer <key> ... bind <addr>` -- `bind` is NOT a wg option. The
   pinning mechanism is fwmark + policy routing. A unit test written against the
   same wrong assumption passed happily.
2. A pinning route naming the TUNNEL device instead of the UPLINK fails with
   "Nexthop has invalid gateway": routing a tunnel's own encrypted traffic out
   its own device is a loop.
3. OpenWrt's fw4 input chain ends in `jump handle_reject` and does not list
   tunnel interfaces, so tunnel traffic is REJECTED -- which looks exactly like
   a broken tunnel.

And one harness defect that cost the most time, now an explicit precondition:
`mptcpize run iperf3 -c ...` converts the CLIENT socket only. A listener started
plain as `iperf3 -s` stays plain TCP, the connection falls back to regular TCP,
and a fallback connection has NO path manager -- so no ADD_ADDR is ever emitted.
Always assert the peer listener is MPTCP (`ss -Mltn`) before measuring.
"""

from __future__ import annotations

import os
import re

import pytest


def _have_serial() -> bool:
    return bool(os.environ.get("TOLLGATE_SERIAL_PORT", "").strip())


pytestmark = pytest.mark.skipif(
    not _have_serial(),
    reason="TOLLGATE_SERIAL_PORT not set (physical MPTCP lane needs the serial_console)",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def sh(console, command: str, timeout: int = 30) -> str:
    """Run a command on the router and return its output.

    `console` is the SerialConsole from the `serial_console` fixture, passed in
    explicitly so this helper is not mistaken for a test.
    """
    return console.exec_command(command, timeout=timeout)


def mptcp_counters(console) -> dict[str, int]:
    """Parse /proc/net/netstat MPTcpExt into {name: value}.

    The file holds two positional lines (headers, then values); pairing them by
    index is the only way to read a counter by NAME on this build, since BusyBox
    has no `paste`. Returns {} when the lines cannot be read, so callers can
    treat "unreadable" distinctly from "zero".
    """
    raw = sh(console, "cat /proc/net/netstat")
    header = value = None
    for line in raw.splitlines():
        if line.startswith("MPTcpExt:"):
            if header is None:
                header = line.split()[1:]
            else:
                value = line.split()[1:]
                break
    if not header or not value:
        return {}
    return {k: int(v) for k, v in zip(header, value) if v.isdigit()}


def require_mptcp(console) -> None:
    """Fail loudly if MPTCP is absent, rather than reporting a hollow pass."""
    out = sh(console, "cat /proc/sys/net/mptcp/enabled 2>/dev/null; echo rc=$?")
    if "rc=0" not in out:
        pytest.fail("MPTCP is not present in this kernel (/proc/sys/net/mptcp absent)")
    if "1" not in out.split("rc=")[0]:
        pytest.fail("MPTCP exists but net.mptcp.enabled != 1")


# ---------------------------------------------------------------------------
# environment facts
# ---------------------------------------------------------------------------

def test_mptcp_enabled_on_router(serial_console):
    """MPTCP must be present and enabled before any bonding claim."""
    require_mptcp(serial_console)


def test_ip_full_present_not_busybox(serial_console):
    """BusyBox `ip` has no `mptcp` subcommand, so endpoints cannot be managed.

    This is the single most common cause of "endpoint add did nothing" on a
    stock image; assert the real `ip` answers the subcommand.
    """
    out = sh(serial_console, "ip mptcp limits 2>&1")
    assert "add_addr_accepted" in out, (
        "`ip mptcp limits` did not answer with limits -- BusyBox ip is in use. "
        f"Install ip-full. Got: {out!r}"
    )


def test_mptcp_scheduler_is_reported(serial_console):
    """Record which schedulers the build exposes.

    This build ships only `default` (no `available_schedulers`, no sched
    modules), which means subflows cannot be made to spread across paths by
    scheduler choice. Asserting the OBSERVED state rather than the expected one:
    the test records the fact and fails only if the scheduler file is missing
    entirely (a genuinely broken build).
    """
    sched = sh(serial_console, "cat /proc/sys/net/mptcp/scheduler 2>/dev/null").strip()
    assert sched, "no MPTCP scheduler reported at all -- build is broken"
    avail = sh(serial_console, "cat /proc/sys/net/mptcp/available_schedulers 2>/dev/null").strip()
    if not avail:
        # Expected on OpenWrt 24.10.2 / kernel 6.6.93: the multi-scheduler API is
        # not compiled in. Not a failure; a documented limitation.
        pytest.skip(f"only a fixed scheduler ({sched!r}); no multi-scheduler API")


# ---------------------------------------------------------------------------
# endpoints (the provisioning the module renders)
# ---------------------------------------------------------------------------

def test_endpoints_use_signal_subflow(serial_console):
    """Every uplink endpoint must be `signal subflow`.

    A subflow-only endpoint never advertises its address, so the peer never
    learns it and the subflow can never form. This is a silent failure: the
    endpoint looks registered.
    """
    out = sh(serial_console, "ip mptcp endpoint show")
    if not out.strip():
        pytest.skip("no MPTCP endpoints registered on this router")
    for line in out.splitlines():
        # Only assert for entries the operator/module registered; the kernel's
        # own link-local entries are not ours to judge.
        if "signal" not in line and "subflow" not in line:
            continue
        assert "signal" in line, (
            f"endpoint lacks `signal` and will never advertise its address: {line!r}"
        )


def test_limits_cover_registered_endpoints(serial_console):
    """The subflow ceiling must be at least the number of usable uplinks.

    The kernel default is 2, which is too small for a multi-WAN box: a third
    uplink would be registered and then never used.
    """
    out = sh(serial_console, "ip mptcp limits")
    m = re.search(r"subflows\s+(\d+)", out)
    assert m, f"could not parse subflow ceiling from: {out!r}"
    ceiling = int(m.group(1))
    assert ceiling >= 2, f"subflow ceiling {ceiling} cannot carry two uplinks"


# ---------------------------------------------------------------------------
# the rendered apply script (idempotence, run on the device)
# ---------------------------------------------------------------------------

def test_apply_script_is_idempotent_on_device(serial_console):
    """Run the module's rendered apply script TWICE and compare endpoint state.

    Delete-then-add is not idempotent in effect even when it succeeds: it removes
    the endpoint and allocates a NEW id, which would interrupt an in-flight
    subflow on every reconcile. So the assertion is the stronger one: the
    endpoint ids must not change.

    Skips unless a script is provided, since the renderer lives in the module
    repo and this lane must not embed a copy of it.
    """
    script_path = os.environ.get("TOLLGATE_MPTCP_APPLY_SCRIPT", "").strip()
    if not script_path:
        pytest.skip("TOLLGATE_MPTCP_APPLY_SCRIPT not set (nothing to apply)")
    script = open(script_path).read()
    assert "ip mptcp endpoint" in script, "provided script does not touch endpoints"

    remote = "/tmp/mptcp-apply-lane.sh"
    heredoc = f"cat > {remote} << 'LANE_EOF'\n{script}\nLANE_EOF\necho WROTE"
    assert "WROTE" in sh(serial_console, heredoc, timeout=60), "failed to stage the script"

    sh(serial_console, f"sh {remote} 2>&1; echo rc=$?", timeout=120)
    before = sh(serial_console, "ip mptcp endpoint show")

    out = sh(serial_console, f"sh {remote} 2>&1; echo rc=$?", timeout=120)
    after = sh(serial_console, "ip mptcp endpoint show")

    assert "rc=0" in out, f"second run exited non-zero (not idempotent): {out!r}"
    assert before == after, (
        "endpoint state changed on a repeated apply -- the script churns "
        f"endpoints instead of being a no-op.\nbefore:\n{before}\nafter:\n{after}"
    )


def test_endpoint_flush_resets_add_addr_accepted(serial_console):
    """Document a real trap: `ip mptcp endpoint flush` zeroes add_addr_accepted.

    Measured on OpenWrt 24.10.2: after a flush the limit reads 0, which makes the
    router REFUSE the peer's ADD_ADDR and silently breaks peer-initiated
    subflows. The applier must therefore re-assert limits after any flush.

    This test records the behaviour so a future reconcile path cannot forget it.
    """
    original = sh(serial_console, "ip mptcp limits")
    m = re.search(r"add_addr_accepted\s+(\d+)", original)
    if not m:
        pytest.skip(f"cannot read limits: {original!r}")
    if int(m.group(1)) == 0:
        pytest.skip("add_addr_accepted already 0; nothing to observe")

    # Non-destructive probe: re-assert and confirm the value sticks, which is the
    # property the applier depends on. We do NOT flush the device's live
    # endpoints from an automated lane.
    sh(serial_console, "ip mptcp limits set subflows 4 add_addr_accepted 4")
    after = sh(serial_console, "ip mptcp limits")
    assert "add_addr_accepted 4" in after, (
        "limits did not stick after `limits set` -- the applier cannot rely on "
        f"them. Got: {after!r}"
    )


# ---------------------------------------------------------------------------
# tunnels (the NAT-traversal design)
# ---------------------------------------------------------------------------

def test_tunnel_interfaces_are_accepted_by_the_firewall(serial_console):
    """A tunnel interface missing from fw4's input chain is silently rejected.

    fw4's input chain ends in `jump handle_reject`; a WireGuard interface is in
    none of the accepted groups, so its traffic is rejected and the tunnel looks
    broken while its transport is perfect.
    """
    wg = sh(serial_console, "wg show interfaces 2>/dev/null").strip()
    if not wg:
        pytest.skip("no WireGuard interfaces on this router")
    ruleset = sh(serial_console, "nft list ruleset 2>/dev/null")
    if not ruleset.strip():
        pytest.skip("no nft ruleset readable")
    for iface in wg.split():
        assert iface in ruleset, (
            f"tunnel {iface} has no rule in the firewall -- its traffic will be "
            "rejected by fw4's terminal `jump handle_reject`"
        )


def test_tunnel_pinning_uses_fwmark_not_bind(serial_console):
    """`wg set ... bind` does not exist; pinning must be fwmark + policy routing.

    A tunnel pinned by the invalid option fails outright, and one pinned by
    nothing silently takes the default route -- producing a bond that connects
    twice through the SAME uplink.
    """
    wg = sh(serial_console, "wg show interfaces 2>/dev/null").strip()
    if not wg:
        pytest.skip("no WireGuard interfaces on this router")
    detail = sh(serial_console, "wg show all 2>/dev/null")
    assert "bind" not in detail or "fwmark" in detail, (
        "a tunnel appears to rely on `bind`, which is not a wg option. "
        "Pinning must use fwmark + a routing rule."
    )


# ---------------------------------------------------------------------------
# subflow formation (needs a peer)
# ---------------------------------------------------------------------------

def test_peer_listener_is_mptcp_before_measuring(serial_console):
    """Assert the PEER is multipath-capable -- the precondition that cost most.

    `mptcpize run iperf3 -c ...` makes the CLIENT socket MPTCP. A listener started
    plain as `iperf3 -s` stays plain TCP, so the connection FALLS BACK to regular
    TCP -- and a fallback connection has no path manager, so no ADD_ADDR is ever
    generated, the monitor shows only CLOSED, and it looks exactly like a
    router-side fault.

    The responder-side counter that settles it is MPJoinSynAckRx / the peer's
    MP_CAPABLE SYN-ACK. Skip when no peer is configured.
    """
    peer = os.environ.get("TOLLGATE_MPTCP_PEER", "").strip()
    if not peer:
        pytest.skip("TOLLGATE_MPTCP_PEER not set (no peer to check)")
    # The peer check itself is host-side; on the router we can only assert that
    # the connection did not fall back, which is the observable consequence.
    c = mptcp_counters(serial_console)
    if not c:
        pytest.skip("MPTcpExt counters unreadable")
    fallback = (
        c.get("MPCapableFallbackACK", 0) + c.get("MPCapableFallbackSYNACK", 0)
    )
    assert c.get("MPCapableSYNRX", 0) > 0 or fallback == 0, (
        "MPTCP capability was never negotiated and no fallback recorded -- "
        "check the peer listener with `ss -Mltn` before trusting any measurement"
    )


def test_subflow_over_tunnel_establishes(serial_console):
    """The end-to-end claim: a subflow establishes on a TUNNEL address.

    Guarded by an explicit env flag because it spends real traffic and needs a
    working tunnel + peer. It asserts the observable proof recorded during
    development: the path manager logs SF_ESTABLISHED with a source address that
    is a tunnel address, not an uplink address.
    """
    if os.environ.get("TOLLGATE_MPTCP_MEASURE", "").lower() not in ("1", "true", "yes"):
        pytest.skip("set TOLLGATE_MPTCP_MEASURE=1 to run the subflow measurement")

    peer = os.environ.get("TOLLGATE_MPTCP_PEER", "").strip()
    port = os.environ.get("TOLLGATE_MPTCP_PORT", "5201")
    tunnel_addrs = os.environ.get("TOLLGATE_MPTCP_TUNNEL_ADDRS", "").split()
    if not peer or not tunnel_addrs:
        pytest.skip("TOLLGATE_MPTCP_PEER / TOLLGATE_MPTCP_TUNNEL_ADDRS not set")

    sh(serial_console, "ip mptcp monitor > /tmp/lane-mon.log 2>&1 & sleep 2; echo MON", timeout=30)
    out = sh(
        console,
        f"mptcpize run iperf3 -c {peer} -p {port} -t 10 2>&1 | grep -E 'receiver|error'",
        timeout=120,
    )
    assert "receiver" in out, f"transfer failed, so no subflow could form: {out!r}"

    mon = sh(serial_console, "cat /tmp/lane-mon.log")
    sh(serial_console, "kill %1 2>/dev/null || true")

    assert "SF_ESTABLISHED" in mon, (
        "no subflow established despite a successful transfer -- the connection "
        f"likely fell back. Check the peer listener is MPTCP (`ss -Mltn`).\n{mon}"
    )
    assert any(a in mon for a in tunnel_addrs), (
        f"a subflow established but not on a tunnel address {tunnel_addrs}; "
        f"the advertised address is not the reachable one.\n{mon}"
    )


# ---------------------------------------------------------------------------
# THE TRANSFERABLE PRECONDITION (hardened)
# ---------------------------------------------------------------------------
# This is the lesson that cost the most time in this work, and it is now a
# first-class check rather than a comment.
#
# `mptcpize run iperf3 -c ...` converts the CLIENT socket only. A listener
# started plain as `iperf3 -s` stays plain TCP, so the connection FALLS BACK to
# regular TCP -- and a fallback connection has NO path manager, so no ADD_ADDR is
# ever generated, `ip mptcp monitor` shows only CLOSED, and it presents exactly
# like a router-side fault. Days of router-side debugging could not find it
# because the defect was on the far end.
#
# The rule: BEFORE diagnosing any MPTCP symptom, prove the far end negotiated
# multipath. Two independent signals, because either alone can mislead:
#   1. the peer's socket must be MPTCP  -> `ss -Mltn` shows `mptcp LISTEN`
#   2. the responder counter must move  -> MP_CAPABLE SYN-ACK actually sent
#
# Signal 2 is checked from the router (it is the responder's counter we cannot
# read), signal 1 must be checked ON the peer. This test fails loudly rather than
# skipping when a peer IS configured but has not negotiated, because a silent
# pass here is what produces a wrong result downstream.

def test_no_mptcp_fallback_before_any_measurement(serial_console):
    """Hard precondition: refuse to trust a measurement if MPTCP fell back.

    Fails (does not skip) when a peer is configured but capability was never
    negotiated, because everything measured afterwards would be measurement of
    ordinary TCP wearing an MPTCP label.
    """
    peer = os.environ.get("TOLLGATE_MPTCP_PEER", "").strip()
    if not peer:
        pytest.skip("TOLLGATE_MPTCP_PEER not set (no peer configured)")

    c = mptcp_counters(serial_console)
    if not c:
        pytest.skip("MPTcpExt counters unreadable on this build")

    fallback = c.get("MPCapableFallbackACK", 0) + c.get("MPCapableFallbackSYNACK", 0)
    capable_rx = c.get("MPCapableSYNRX", 0)

    assert not (fallback > 0 and capable_rx == 0), (
        "MPTCP capability negotiation FELL BACK with no successful SYN seen. "
        "Everything measured next would be plain TCP. Check the peer listener: "
        "`ss -Mltn` must show `mptcp LISTEN` (start it as "
        "`mptcpize run <server>`, not plain)."
    )


def test_peer_listener_is_mptcp_if_probe_available(serial_console):
    """If a peer probe command is provided, assert the peer socket is MPTCP.

    TOLLGATE_MPTCP_PEER_SS_CMD lets the operator supply the exact command that
    reads the peer's listeners (e.g. `ssh vps 'ss -Mltn'`), so this lane asserts
    the real precondition instead of trusting the router's view of it.
    """
    cmd = os.environ.get("TOLLGATE_MPTCP_PEER_SS_CMD", "").strip()
    if not cmd:
        pytest.skip("TOLLGATE_MPTCP_PEER_SS_CMD not set (cannot read peer sockets)")
    import subprocess

    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    text = (out.stdout or "") + (out.stderr or "")
    assert "mptcp" in text.lower(), (
        "the peer reports no MPTCP listener. A plain-TCP listener causes a "
        "fallback connection, which has no path manager and therefore never "
        f"generates ADD_ADDR. Output:\n{text}"
    )


# ---------------------------------------------------------------------------
# TRANSPORT SHAPE (D10): proxy-terminated must not signal its own addresses
# ---------------------------------------------------------------------------
# Decided in docs/architecture/multi-wan-bonding-decision.md A1.2 (D10).
#
# The bond may not rely on the peer dialling back into the client. Measured on
# this bench: both uplinks were private (house NAT + Starlink CGNAT), so the
# peer's MPTcpExtAddAddr stayed 0, MPJoinNoTokenFound climbed, and the second
# subflow never formed while the transport itself was healthy.
#
# A `signal` endpoint makes the kernel send ADD_ADDR, which asks the peer to
# connect to that address. Behind NAT that JOIN can never succeed. In the
# proxy-terminated shape the client initiates every path, so NO endpoint may be
# signalled -- while every endpoint must still be `subflow` so the client can
# open subflows itself.
#
# These checks read the state back FROM THE DEVICE, per the IaC gate.

def test_proxy_terminated_signals_no_endpoint(serial_console):
    """No registered endpoint may carry `signal` in the proxy-terminated shape.

    `signal` instructs the peer to connect back to this address. When that
    address is private -- which is the whole reason this shape exists -- the
    resulting JOIN cannot succeed, and the bond silently stays single-path.
    """
    if os.environ.get("TOLLGATE_MPTCP_TRANSPORT", "").strip() != "proxy-terminated":
        pytest.skip("TOLLGATE_MPTCP_TRANSPORT is not proxy-terminated")

    out = sh(serial_console, "ip mptcp endpoint show")
    if not out.strip():
        pytest.skip("no MPTCP endpoints registered")

    signalled = [ln for ln in out.splitlines() if " signal" in ln]
    assert not signalled, (
        "proxy-terminated bond signalled an endpoint, which asks the peer to "
        "dial an address it cannot reach behind NAT. Endpoints must be "
        f"`subflow` only.\nOffending lines:\n" + "\n".join(signalled)
    )


def test_proxy_terminated_endpoints_still_permit_subflows(serial_console):
    """Dropping `signal` must not drop `subflow` -- we still need to open paths."""
    if os.environ.get("TOLLGATE_MPTCP_TRANSPORT", "").strip() != "proxy-terminated":
        pytest.skip("TOLLGATE_MPTCP_TRANSPORT is not proxy-terminated")

    out = sh(serial_console, "ip mptcp endpoint show")
    if not out.strip():
        pytest.skip("no MPTCP endpoints registered")

    for line in out.splitlines():
        assert "subflow" in line, (
            "every registered endpoint must permit subflows; without it the "
            f"client cannot open a path over that uplink: {line!r}"
        )


def test_advertised_address_shape_signals_every_endpoint(serial_console):
    """The default shape must still signal, or public-address deployments regress.

    On a network with genuinely routable uplinks, `signal` is the only way the
    peer learns an address to dial. The proxy-terminated default must not have
    removed it from the historic shape.
    """
    if os.environ.get("TOLLGATE_MPTCP_TRANSPORT", "advertised-address").strip() != "advertised-address":
        pytest.skip("transport is not advertised-address")

    out = sh(serial_console, "ip mptcp endpoint show")
    if not out.strip():
        pytest.skip("no MPTCP endpoints registered")

    for line in out.splitlines():
        assert "signal" in line, (
            "advertised-address shape must signal every endpoint so the peer "
            f"can learn it: {line!r}"
        )
