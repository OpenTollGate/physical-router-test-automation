"""MPTCP endpoint contract lane (physical router).

Pins the *kernel contract* the module's MPTCP endpoint code depends on, read back
from the device itself. Each assertion here was established on a live GL-MT6000
(OpenWrt 24.10.2, tollgate-wrt 0.6.0_rc6-r1) while reviewing #978, which fixes
three defects in `src/mptcp_bonding`:

  * a CIDR in `uplinks[].address` rendered `ip mptcp endpoint add 10.0.1.1/30 ...`
    and the kernel rejected it, silently losing the endpoint;
  * a failed endpoint add aborted the others under `set -e`, leaving the bond
    half-applied;
  * the disable path did not flush, so a previous enabled boot's endpoints
    survived a disable.

WHY THIS LANE EXISTS SEPARATELY FROM tests/mptcp/test_mptcp_multipath.py

That lane tests the multi-WAN *bonding* behaviour (subflows, ADD_ADDR, peer
negotiation) and needs a peer or a tunnel, so most of it skips on a lone router.
The contract below needs only the router, and it is the part that unit tests can
get wrong: #978's own tests assert *rendered script text*, which cannot tell you
what the kernel does with that text. A unit test written against the wrong
assumption passes happily -- the same failure mode the multipath lane documents
for `wg ... bind`.

The specific trap this guards: `ip mptcp endpoint add` prints its error on
**stderr** and the shell status seen through a pipeline (`... 2>&1 | head`) is
the *pipeline's*, so a naive check reads `rc=0` next to a kernel error. Assert on
the message AND on an unpiped exit status.

Run:

    TOLLGATE_SERIAL_PORT=/dev/ttyUSB1 TOLLGATE_USE_HARDWARE_LOCK=1 \\
        pytest tests/mptcp/test_mptcp_endpoint_contract.py -v

Every test reads its post-condition back FROM THE DEVICE and restores the
endpoint table before returning, so the lane is safe to run against a bench
router that is doing something else.

Nothing here mutates router configuration files; the only state touched is the
kernel endpoint table, which each test restores.
"""

from __future__ import annotations

import os
import re

import pytest

# The endpoint tests need a router but not a peer, so they gate on the serial
# console rather than on TOLLGATE_MPTCP_PEER.
pytestmark = [
    pytest.mark.regression,
    pytest.mark.skipif(
        not os.environ.get("TOLLGATE_SERIAL_PORT", "").strip(),
        reason="TOLLGATE_SERIAL_PORT not set (physical lane needs the serial_console)",
    ),
]

# An address the kernel will accept but that no uplink owns. Used to exercise the
# add/remove path without depending on this router's own addressing.
_PROBE_ADDR = "192.0.2.10"
_PROBE_IFACE = "br-private"


def sh(console, command: str, timeout: int = 30) -> str:
    """Run a command on the router and return its output."""
    return console.exec_command(command, timeout=timeout)


def endpoints(console) -> list[str]:
    """Current MPTCP endpoints, as the device reports them.

    Returns the raw lines so a failure message shows exactly what the router
    said rather than a parsed approximation.
    """
    out = sh(console, f"ip mptcp endpoint show dev {_PROBE_IFACE} 2>/dev/null")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def require_mptcp(console) -> None:
    """Fail loudly if MPTCP is absent, rather than reporting a hollow pass."""
    out = sh(console, "cat /proc/sys/net/mptcp/enabled 2>/dev/null; echo rc=$?")
    if "rc=0" not in out:
        pytest.fail("MPTCP is not present in this kernel (/proc/sys/net/mptcp absent)")
    if "1" not in out.split("rc=")[0]:
        pytest.fail("MPTCP exists but net.mptcp.enabled != 1")


def exit_status(console, command: str) -> int:
    """Exit status of `command`, read on the device.

    Deliberately NOT piped: `cmd 2>&1 | head; echo $?` reports head's status, so
    a kernel rejection can read as success. This is the trap the lane exists for.
    """
    out = sh(console, f"{command} >/dev/null 2>&1; echo rc=$?")
    match = re.search(r"rc=(\d+)", out)
    if not match:
        pytest.fail(f"could not read exit status of {command!r} from the router: {out!r}")
    return int(match.group(1))


@pytest.fixture(autouse=True)
def restore_endpoints(serial_console):
    """Restore the endpoint table around every test in this module.

    The lane runs against a bench router that may be mid-experiment for someone
    else, so leaving a probe endpoint behind would corrupt their run.
    """
    require_mptcp(serial_console)
    before = endpoints(serial_console)
    yield
    sh(serial_console, "ip mptcp endpoint flush >/dev/null 2>&1 || true")
    for line in before:
        # Re-add each pre-existing endpoint by its id/addr/dev triple.
        parts = line.split()
        if len(parts) >= 5:
            sh(
                serial_console,
                f"ip mptcp endpoint add {parts[0]} dev {parts[4]} "
                f">/dev/null 2>&1 || true",
            )


def test_mptcp_kernel_support_present(serial_console):
    """The kernel exposes MPTCP, so the module's endpoint code has something to drive.

    A router without MPTCP makes every other assertion in this lane meaningless,
    so this fails rather than skips.
    """
    require_mptcp(serial_console)
    limits = sh(serial_console, "ip mptcp limits")
    assert "subflows" in limits, (
        f"`ip mptcp limits` did not report a subflows limit, so the mptcp "
        f"subcommand may be unavailable:\n{limits}"
    )


def test_ip_mptcp_endpoint_add_rejects_a_cidr(serial_console):
    """A prefixed address must be refused by the kernel, loudly.

    This is the defect #978 fixes at config-load time. The point of the assertion
    is that the failure is REAL and detectable: if a future kernel accepted a
    CIDR, the module's rejection would be over-strict and this test should be
    revisited rather than silently kept.
    """
    status = exit_status(serial_console, f"ip mptcp endpoint add 10.0.1.1/30 dev {_PROBE_IFACE}")
    assert status != 0, (
        "the kernel accepted a CIDR address as an MPTCP endpoint; #978's "
        "rejection in Validate() would then be over-strict"
    )
    # And nothing was registered -- a rejection that still half-adds would be worse.
    assert not any("10.0.1.1" in ln for ln in endpoints(serial_console)), (
        "a rejected CIDR endpoint still appears in the endpoint table"
    )


def test_ip_mptcp_endpoint_add_accepts_a_bare_address(serial_console):
    """A bare address is what the kernel wants -- the form #978 requires."""
    sh(serial_console, f"ip mptcp endpoint flush dev {_PROBE_IFACE} >/dev/null 2>&1 || true")
    status = exit_status(serial_console, f"ip mptcp endpoint add {_PROBE_ADDR} dev {_PROBE_IFACE}")
    assert status == 0, (
        f"the kernel refused a bare address {_PROBE_ADDR} on {_PROBE_IFACE}; the "
        f"module's accepted form is therefore wrong"
    )
    assert any(_PROBE_ADDR in ln for ln in endpoints(serial_console)), (
        "the endpoint was accepted but does not appear in `ip mptcp endpoint show`"
    )


def test_a_failed_endpoint_does_not_abort_the_others(serial_console):
    """One bad endpoint must not stop the good ones from being added.

    This is the device lesson behind #978's FAILED=0 / exit 1 mechanism: under
    `set -e` a rejected endpoint used to abort the loop and leave the bond
    half-applied. The script must instead add everything it can and then fail
    the reconcile so the failure is visible.
    """
    sh(serial_console, f"ip mptcp endpoint flush dev {_PROBE_IFACE} >/dev/null 2>&1 || true")

    script = (
        "FAILED=0; "
        f"for spec in '10.0.1.1/30 dev {_PROBE_IFACE}' '{_PROBE_ADDR} dev {_PROBE_IFACE}'; do "
        "  if ! ip mptcp endpoint add $spec >/dev/null 2>&1; then FAILED=1; fi; "
        "done; echo FAILED=$FAILED"
    )
    out = sh(serial_console, script)
    assert "FAILED=1" in out, (
        f"a rejected endpoint did not mark the reconcile as failed:\n{out}"
    )
    assert any(_PROBE_ADDR in ln for ln in endpoints(serial_console)), (
        "the good endpoint was not added after a preceding failure -- the loop "
        "aborted, which is exactly the half-applied bond #978 fixes"
    )


def test_endpoint_flush_clears_and_is_idempotent(serial_console):
    """The disable path's flush must clear endpoints and be a no-op when empty.

    A non-zero exit on an already-empty table would make every boot of a
    stock node log a warning, which is why the module's FlushScript exits 0.
    """
    sh(serial_console, f"ip mptcp endpoint add {_PROBE_ADDR} dev {_PROBE_IFACE} >/dev/null 2>&1 || true")
    assert endpoints(serial_console), "setup failed: no endpoint to flush"

    status = exit_status(serial_console, f"ip mptcp endpoint flush dev {_PROBE_IFACE}")
    assert status == 0, "flushing a populated endpoint table failed"
    assert not endpoints(serial_console), "flush reported success but endpoints remain"

    again = exit_status(serial_console, f"ip mptcp endpoint flush dev {_PROBE_IFACE}")
    assert again == 0, (
        "flushing an already-empty endpoint table is non-zero; a stock node "
        "without the bond would warn on every boot"
    )
