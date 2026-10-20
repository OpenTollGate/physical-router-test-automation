"""Host-venue integration runs against OpenTollGate/tollgate-rs (rust-v2).

The first PRTA runs of the *current* tollgate-rs node: real ``tollgated``,
``mintd`` and ``merchantd`` processes on the host, real Spilman channel
funding against an auto-accept mint, and assertions against the
control-socket snapshot — the same claims, and the same assertion surface,
as tollgate-rs's own ``testing/purchase`` Docker topology:

  * demand becomes a purchase, and a purchase becomes a shaped rate
    (the client wants 2 MB/s, reserves 125% of it);
  * the budget is renewed, not forfeited;
  * real bytes move through the data plane;
  * the budget outlives the session and a restart of the gateway.

Needs the release binaries built first (tollgated, mintd, merchantd):

    git clone https://github.com/OpenTollGate/tollgate-rs ~/src/tollgate-rs
    cargo build --release --bins -p tollgate-net   # in that checkout

Run:

    pytest tests/backends/test_tollgate_rs_v2_host.py --backend rust-v2

Env:
    TOLLGATE_RS_BIN_DIR   where the release binaries live
                         (default ~/src/tollgate-rs/target/release)

Runs as a user who may write /run/tollgate-prta-* and the state directory
(/var/lib/tollgate): root on the lab host, or a dedicated CI user.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lib.tollgate_rs import TollgateRsPair, wait_for

# The whole protocol loop (peering, funding, first purchase) can legitimately
# take minutes on a loaded lab host; the suite default is 60s.
pytestmark = [pytest.mark.api, pytest.mark.slow, pytest.mark.timeout(420)]

#: 2 MB/s demanded at 125% headroom, as in tollgate-rs testing/purchase.
EXPECTED_RATE = 2_500_000


def _backend_type(request) -> str:
    return (
        request.config.getoption("--backend", default=None)
        or os.environ.get("TOLLGATE_BACKEND", "go")
    )


@pytest.fixture(scope="module")
def rs_pair(request, tmp_path_factory):
    if _backend_type(request) != "rust-v2":
        pytest.skip("rust-v2 lane: run with --backend rust-v2")

    bin_dir = Path(
        os.environ.get("TOLLGATE_RS_BIN_DIR", str(Path.home() / "src/tollgate-rs/target/release"))
    )
    missing = [b for b in ("tollgated", "mintd", "merchantd") if not (bin_dir / b).exists()]
    if missing:
        pytest.skip(
            f"tollgate-rs binaries not found in {bin_dir} (missing: {', '.join(missing)}); "
            "build them with: cargo build --release --bins"
        )

    workdir = tmp_path_factory.mktemp("prta-rs-v2")
    pair = TollgateRsPair(bin_dir=bin_dir, workdir=workdir)
    try:
        pair.start()
        # The whole loop in one wait, as in the Docker topology: peering,
        # channel funding and the first purchase, ending in a shaped rate.
        wait_for(
            "the gateway to shape the client to what it bought",
            lambda: pair.gateway.peer_field("shaped_rate") == EXPECTED_RATE,
            timeout=180,
        )
        yield pair
    except Exception as exc:
        pytest.fail(f"{pair.dump_state()}\n\noriginal error: {exc!r}")
    finally:
        pair.stop()


def test_gateway_shapes_the_client_to_what_it_bought(rs_pair):
    peer = rs_pair.gateway.peer()
    assert peer.get("shaped_rate") == EXPECTED_RATE
    assert peer.get("reserved_rate") == EXPECTED_RATE


def test_client_agrees_on_the_rate_it_bought(rs_pair):
    # The payer's own view of the gateway's session, computed independently.
    peer = rs_pair.client.peer()
    assert peer.get("bought_reserved_rate") == EXPECTED_RATE


def test_budget_is_renewed_not_forfeit(rs_pair):
    expires = rs_pair.gateway.peer_field("budget_expires_in_ms")
    assert expires and expires > 0, "the budget ran out instead of being renewed"
    # The client's view of the same session, reported in a Balance.
    balance = rs_pair.client.peer().get("reported_balance") or {}
    assert balance.get("remaining", 0) > 0, "the client should hold a Balance with room in it"


def test_bytes_move_through_the_data_plane(rs_pair):
    to_payer = rs_pair.gateway.peer_field("to_payer")
    assert to_payer and to_payer > 1_000_000, (
        f"only {to_payer} bytes sent to the client; the data plane is not carrying traffic"
    )


def test_unit_is_declared(rs_pair):
    # US-I3: the metered unit is declared by the node, not assumed by the
    # tester. Both ends of this lane sell and buy bytes.
    assert rs_pair.gateway.snapshot().get("unit") == "byte"
    assert rs_pair.client.snapshot().get("unit") == "byte"


def test_client_restart_comes_back_to_its_budget(rs_pair):
    # US-R1's mirror side and the reconnect half of US-R2: the client goes
    # away entirely (process death, not a link blip) and comes back. The
    # gateway hands back the budget it kept and carries it again at what it
    # reserves. Channel-id resume under a pure link blip is covered
    # upstream (two_node.rs); a full process restart is the harsher path.
    from lib.tollgate_rs import wait_for

    rs_pair.client.stop()
    rs_pair.client.start()

    wait_for(
        "the gateway to carry the returning client again",
        lambda: rs_pair.gateway.peer_field("shaped_rate") == EXPECTED_RATE,
        timeout=180,
    )
    expires = rs_pair.gateway.peer_field("budget_expires_in_ms")
    assert expires and expires > 0, "the returning client should hold a live budget"


def test_budget_survives_the_gateway_restart(rs_pair):
    # The client goes away, and the gateway restarts while it is away. Its
    # budget is thirty seconds of its reserved rate, so it is still there
    # when it comes back.
    budget_file = rs_pair.gateway.budget_file()
    assert budget_file.exists(), f"no budget file at {budget_file}"
    budgets = json.loads(budget_file.read_text())
    assert any(entry.get("remaining", 0) > 0 for entry in budgets.values()), (
        f"the gateway kept no budget on disk for the client: {budgets}"
    )

    rs_pair.client.stop()
    rs_pair.gateway.restart()
    rs_pair.client.start()

    wait_for(
        "the gateway to hand the client back its budget",
        lambda: rs_pair.gateway.peer_field("shaped_rate") == EXPECTED_RATE,
        timeout=180,
    )
