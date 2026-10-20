"""Bounded mint registration (module #940).

Before #940 the Go backend registered each configured mint by calling gonuts'
``AddMint`` unconditionally. ``AddMint`` fetches and validates the mint's
keysets with no timeout, so a mint that ACCEPTS the TCP connection but never
answers (a blackhole, a hung reverse proxy, a wedged Cashu mint) parked the
merchant's startup goroutine indefinitely — the router came up with no
merchant advertisement at all, and the operator saw a dead box.

#940 gates the registration behind a bounded ``mintServesKeysets`` probe
(8 s, using our own client): an unresponsive mint is left UNREGISTERED after
the bound and retried later via ``ensureMintRegistered``, so startup always
completes. This module pins that bound on a live router.

The distinguishing fault is a HANG, not a refusal: ``/etc/hosts`` blocking
(covered by test_mint_health) fails fast with NXDOMAIN, so it never exercised
the unbounded path. We use an iptables DROP of outbound traffic to the mint's
address, which leaves the TCP connect hanging in SYN_SENT exactly like the
real-world fault.

All interaction goes through ``router.ssh()`` and the router API. Marked
``destructive`` — it rewrites the firewall and restarts the backend.
"""
from __future__ import annotations

import json
import logging
import re
import time
from urllib.parse import urlparse

import pytest

from lib.constants import TEST_MINT_URL

log = logging.getLogger("tollgate.scenarios.mint_registration_bound")

pytestmark = [
    pytest.mark.story("O2"),
    pytest.mark.api,
    pytest.mark.extended,
    pytest.mark.destructive,
    pytest.mark.hardware,
    pytest.mark.timeout(900),
]

# The bounded registration probe is 8 s (#940). Startup must complete well
# inside this ceiling even with every mint hanging: allow ample slack for
# packet retransmit timeouts on the hanging connects.
STARTUP_BOUND_SECONDS = 180
POLL_INTERVAL = 5


def _mint_hostport(mint_url: str) -> tuple[str, int]:
    u = urlparse(mint_url)
    return (u.hostname or mint_url, u.port or (443 if u.scheme == "https" else 80))


def _resolve_mint_ip(router, host: str) -> str | None:
    out = router.ssh(f"nslookup {host} 127.0.0.1 2>/dev/null || getent hosts {host} || true")
    for line in out.splitlines():
        for tok in line.split():
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", tok) and not tok.startswith("127."):
                return tok
    return None


def _drop_mint_outbound(router, ip: str, port: int) -> None:
    """DROP outbound packets to the mint so the TCP connect HANGS (no RST)."""
    router.ssh(
        f"iptables -C OUTPUT -p tcp -d {ip} --dport {port} -j DROP 2>/dev/null || "
        f"iptables -I OUTPUT 1 -p tcp -d {ip} --dport {port} -j DROP"
    )
    log.info("Blackholed mint %s:%d (outbound DROP -> hanging connect)", ip, port)


def _undrop_mint_outbound(router, ip: str, port: int) -> None:
    router.ssh(f"iptables -D OUTPUT -p tcp -d {ip} --dport {port} -j DROP 2>/dev/null || true")
    # Undo a duplicate that the idempotent add above may have left.
    router.ssh(f"iptables -D OUTPUT -p tcp -d {ip} --dport {port} -j DROP 2>/dev/null || true")
    log.info("Un-blackholed mint %s:%d", ip, port)


def _merchant_advertised(router) -> bool:
    if router.api_status("/") != 200:
        return False
    try:
        data = json.loads(router.api_body("/"))
    except json.JSONDecodeError:
        return False
    return data.get("kind") in (10021, 21023)


def _wait_bounded_startup(router, timeout: int) -> tuple[bool, float]:
    started = time.time()
    deadline = started + timeout
    while time.time() < deadline:
        if _merchant_advertised(router):
            return True, time.time() - started
        time.sleep(POLL_INTERVAL)
    return False, time.time() - started


@pytest.fixture
def mint_blackhole(router):
    """Blackhole the configured test mint for the duration of a test."""
    host, port = _mint_hostport(TEST_MINT_URL)
    ip = _resolve_mint_ip(router, host)
    if not ip:
        pytest.skip(f"could not resolve test mint {host!r} from the router")
    _drop_mint_outbound(router, ip, port)
    try:
        yield (ip, port)
    finally:
        _undrop_mint_outbound(router, ip, port)


def test_startup_is_bounded_when_mint_hangs(router, mint_blackhole):
    """A hanging mint must NOT block startup beyond the bound (#940).

    Restart the backend with the mint blackholed and assert the discovery
    advertisement appears within STARTUP_BOUND_SECONDS. Before the bound the
    merchant goroutine parked on AddMint and no advertisement ever appeared.
    """
    router.restart_backend()
    ok, elapsed = _wait_bounded_startup(router, STARTUP_BOUND_SECONDS)
    assert ok, (
        f"merchant did not advertise within {STARTUP_BOUND_SECONDS}s while a mint "
        f"was hanging — the mint registration is unbounded (#940 regression): "
        f"waited {elapsed:.0f}s"
    )
    log.info("Merchant advertised %.0fs after a hanging-mint restart", elapsed)
    assert elapsed < STARTUP_BOUND_SECONDS, f"startup took {elapsed:.0f}s"


def test_hung_mint_registers_after_it_recovers(router, mint_blackhole):
    """The mint deferred by the bound registers once it answers again.

    ``ensureMintRegistered`` retries; after the blackhole is lifted and the
    backend restarted the mint must be registered and the merchant reach full
    (kind 10021) with a price step — i.e. the deferral is not permanent.
    """
    ip, port = mint_blackhole
    router.restart_backend()
    _wait_bounded_startup(router, STARTUP_BOUND_SECONDS)

    _undrop_mint_outbound(router, ip, port)

    deadline = time.time() + 240
    while time.time() < deadline:
        if router.api_status("/") == 200:
            try:
                data = json.loads(router.api_body("/"))
            except json.JSONDecodeError:
                data = {}
            if data.get("kind") == 10021:
                return
        time.sleep(POLL_INTERVAL)
    pytest.fail(
        "mint never registered after the blackhole was lifted — the deferred "
        "mint from the bounded path did not recover (#940)"
    )
