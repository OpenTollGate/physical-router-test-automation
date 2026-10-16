"""rust-v2 admission and lifetime limits: allowance, refusal cap, rollover.

Each topology is its own pair with its own terms, mirroring tollgate-rs's
own topology-per-directory shape (testing/allowance, testing/refusal,
testing/rollover) — the same claims, asserted from the control socket on
host-run daemons instead of containers.

Run:  pytest tests/backends/test_tollgate_rs_v2_limits.py --backend rust-v2
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lib.tollgate_rs import TollgateRsPair, wait_for

pytestmark = [pytest.mark.api, pytest.mark.slow, pytest.mark.timeout(420)]

EXPECTED_RATE = 2_500_000  # 2 MB/s demand at 125% headroom
MINIMUM_FLOW = 4096


def _backend_type(request) -> str:
    return (
        request.config.getoption("--backend", default=None)
        or os.environ.get("TOLLGATE_BACKEND", "go")
    )


def _pair(request, tmp_path, **kwargs) -> TollgateRsPair:
    if _backend_type(request) != "rust-v2":
        pytest.skip("rust-v2 lane: run with --backend rust-v2")
    bin_dir = Path(
        os.environ.get("TOLLGATE_RS_BIN_DIR", str(Path.home() / "src/tollgate-rs/target/release"))
    )
    missing = [b for b in ("tollgated", "mintd", "merchantd") if not (bin_dir / b).exists()]
    if missing:
        pytest.skip(f"tollgate-rs binaries not in {bin_dir} (missing: {', '.join(missing)})")
    pair = TollgateRsPair(bin_dir=bin_dir, workdir=tmp_path / "pair", **kwargs)
    try:
        pair.start()
        yield pair
    except Exception as exc:
        pytest.fail(f"{pair.dump_state()}\n\noriginal error: {exc!r}")
    finally:
        pair.stop()


@pytest.fixture
def allowance_pair(request, tmp_path):
    """A peer that buys nothing: carried at the minimum flow allowance."""
    yield from _pair(request, tmp_path, demand=0)


@pytest.fixture
def refusal_pair(request, tmp_path):
    """A gateway that caps total reservation; a buyer that wants more."""
    yield from _pair(
        request, tmp_path, demand=8_000_000, gateway_grants_extra={"max_rate": 3_000_000}
    )


@pytest.fixture
def rollover_pair(request, tmp_path):
    """Channels sized to exhaust in seconds; buying must cross boundaries."""
    yield from _pair(
        request,
        tmp_path,
        demand=2_000_000,
        client_channels={
            "initial_capacity": 5_000_000,
            "min_capacity": 5_000_000,
            "max_capacity": 5_000_000,
            "capacity_growth_factor": 1.0,
            "rollover_threshold_pct": 80,
        },
    )


def test_unpaid_peer_is_carried_at_minimum_flow(allowance_pair):
    # US-E1: the stranger is held at the allowance — alive, able to reach
    # the mint — not blocked. No purchase was made.
    wait_for(
        "the gateway to hold a session with the buyer",
        lambda: bool(allowance_pair.gateway.peer()),
        timeout=180,
    )
    peer = allowance_pair.gateway.peer()
    assert peer.get("shaped_rate") == MINIMUM_FLOW, peer
    assert peer.get("budget") == 0, "a buyer with demand zero must not be holding budget"


def test_buyer_is_shaped_at_the_operator_cap_not_blocked(refusal_pair):
    # US-E4: the buyer wants 10 MB/s reserved (8 MB/s demand at 125%); the
    # operator will promise 3 MB/s. The result is refusal of the excess
    # with the peer still carried at what remains free.
    wait_for(
        "the gateway to shape the buyer to the cap",
        lambda: refusal_pair.gateway.peer_field("shaped_rate") == 3_000_000,
        timeout=180,
    )
    assert refusal_pair.gateway.peer_field("shaped_rate") == 3_000_000


def test_channels_roll_over_and_buying_continues(rollover_pair):
    # US-R3: three 5 MB channels are 15 MB — about six seconds at the
    # bought rate. Watch the client's outgoing channel id change as each
    # fills, then confirm the bought rate is still enforced afterwards.
    seen: list[str] = []
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline and len(seen) < 3:
        channel = rollover_pair.client.peer().get("outgoing_channel") or {}
        if channel.get("id") and channel["id"] not in seen:
            seen.append(channel["id"])
        time.sleep(0.5)
    assert len(seen) >= 3, f"expected at least three rollovers, saw {seen}"

    wait_for(
        "the gateway to still shape at the bought rate after the rollovers",
        lambda: rollover_pair.gateway.peer_field("shaped_rate") == EXPECTED_RATE,
        timeout=120,
    )
