"""Hardware tests for robust profit-share payouts (firmware PR #168).

Validates the behaviour introduced by the owner-first payout redesign:
  * A recipient whose LNURL can't be resolved (no invoice) is skipped and the
    share stays in the wallet — the mint is NOT marked unreachable.
  * The owner (identity "owner") must be reachable before any dev-split payout;
    an unreachable owner aborts the whole cycle.
  * Payout failures (payee-side) never call mintHealthTracker.MarkUnreachable
    (resolves issue #27).

These tests are robust to the absence of a payable Lightning recipient: they
assert on the router's payout logs (the new processPayout messages) and the
absence of the old "Marking unreachable" fault, so they pass without real
Lightning routing. A funded wallet IS required (so processPayout runs past the
balance gate); tests skip cleanly when the cashu harness / funding / CLI payout
are unavailable.

Requires the firmware from PR #168 (owner-first payouts) — skips on older builds.
"""

import json
import logging
import re

import pytest

from lib.helpers import require_client_identity

log = logging.getLogger("tollgate.profit_share_payout_robustness")

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.config]

# A deliberately-unresolvable Lightning address: LNURL-p invoice fetch fails
# (no such host), so the reachability probe marks it unreachable.
DEAD_LNURL = "dead-recipient@nonexistent-host.invalid"

# Minimum funding (sats) so processPayout proceeds past the balance gate. The
# test config sets min_payout_amount/min_balance low so this is enough.
FUND_SATS = 4


def _has_owner_first_payout(router) -> bool:
    """True if the firmware on the router has the owner-first payout code (#168)."""
    try:
        out = router.ssh(
            "grep -ac 'owner is unreachable' /usr/bin/tollgate-wrt 2>/dev/null || echo 0"
        )
        return int(out.strip()) > 0
    except Exception:
        return False


def _read_json(router, path: str):
    raw = router.ssh(f"cat {path} 2>/dev/null")
    return json.loads(raw) if raw.strip() else None


def _write_json(router, path: str, payload):
    router.write_remote_json(path, payload)


@pytest.fixture(scope="module")
def funded_router(router, cashu):
    """Ensure a funded wallet so processPayout actually runs. Skips if not possible."""
    if not _has_owner_first_payout(router):
        pytest.skip("firmware lacks owner-first payout logic (PR #168) — skipping")
    if not cashu.is_available():
        pytest.skip("cashu venv not available — run scripts/setup-cashu.sh")
    require_client_identity(router)
    try:
        token = cashu.mint(FUND_SATS)
        router.pay_direct(token)
    except Exception as exc:
        pytest.skip(f"could not fund wallet ({FUND_SATS} sats): {exc}")
    return router


@pytest.fixture()
def config_guard(router, funded_router):
    """Back up + restore config.json and identities.json around each test."""
    paths = ["/etc/tollgate/config.json", "/etc/tollgate/identities.json"]
    backups = {}
    for p in paths:
        try:
            backups[p] = router.ssh(f"cat {p} 2>/dev/null")
        except Exception:
            backups[p] = ""
    yield
    for p, data in backups.items():
        try:
            router.ssh(f"cat > {p} <<'__PRG_EOF__'\n{data}\n__PRG_EOF__")
        except Exception as exc:
            log.warning("failed to restore %s: %v", p, exc)
    router.ssh("service tollgate-wrt restart || true", timeout=30)
    import time

    time.sleep(5)


def _configure(router, *, owner_lnurl, maint_lnurl, maint2_lnurl=None):
    """Write profit_share + identities (owner, maint, [maint2]) and a low payout
    threshold so processPayout runs with a small balance."""
    cfg = _read_json(router, "/etc/tollgate/config.json") or {}

    # Low thresholds so a small funded balance triggers a payout.
    for m in cfg.get("accepted_mints", []):
        m["min_payout_amount"] = 1
        m["min_balance"] = 0
        m["balance_tolerance_percent"] = 50

    shares = [
        {"factor": 0.86, "identity": "owner"},
        {"factor": 0.07, "identity": "maint1"},
    ]
    if maint2_lnurl is not None:
        shares.append({"factor": 0.07, "identity": "maint2"})
        # renormalise to 1.0 (0.86 + 0.07 + 0.07)
    cfg["profit_share"] = shares
    _write_json(router, "/etc/tollgate/config.json", cfg)

    identities = _read_json(router, "/etc/tollgate/identities.json") or {}
    pub = identities.get("public_identities", [])
    by_name = {entry.get("name"): entry for entry in pub}
    overrides = {
        "owner": owner_lnurl,
        "maint1": maint_lnurl,
    }
    if maint2_lnurl is not None:
        overrides["maint2"] = maint2_lnurl
    for name, addr in overrides.items():
        if name in by_name:
            by_name[name]["lightning_address"] = addr
        else:
            pub.append({"name": name, "pubkey": "not currently used", "lightning_address": addr})
    identities["public_identities"] = list(by_name.values()) if by_name else pub
    _write_json(router, "/etc/tollgate/identities.json", identities)

    router.ssh("service tollgate-wrt restart", timeout=30)
    import time

    time.sleep(5)  # let the merchant come back up


def _trigger_payout(router):
    """Invoke the CLI payout and return the recent logs."""
    try:
        router.cli_command("wallet", args=["payout"], timeout=60)
    except Exception as exc:
        log.warning("wallet payout CLI call raised: %s", exc)
    import time

    time.sleep(5)  # allow the async payout goroutine to run
    return router.get_tollgate_logs(lines=600) or ""


def test_dead_recipient_is_skipped_and_mint_not_faulted(router, config_guard):
    """A maintainer with a dead LNURL is skipped; the mint is NOT marked unreachable.

    (owner valid-format, maint1 dead, maint2 valid-format) — regardless of whether
    the valid recipients' Lightning payments ultimately succeed, the dead one must
    be logged as unreachable-and-skipped and the mint must stay reachable.
    """
    _configure(
        router,
        owner_lnurl="tollgate@minibits.cash",  # resolves an invoice
        maint_lnurl=DEAD_LNURL,
        maint2_lnurl="tollgate@minibits.cash",
    )
    logs = _trigger_payout(router)

    # maint1's dead LNURL is unreachable -> skipped, share stays in wallet.
    assert re.search(r"maint1 unreachable \(no invoice after \d+ attempts", logs), (
        "expected maint1 (dead LNURL) to be logged unreachable-and-skipped; "
        f"logs tail:\n{logs[-1200:]}"
    )
    # The #168/#27 fix: payouts never fault the mint on a payee failure.
    assert "Marking unreachable" not in logs, (
        "payout path marked the mint unreachable on a payee-side failure "
        "(regression of #168 / #27)"
    )


def test_dead_owner_lnurl_aborts_all_payouts(router, config_guard):
    """An unreachable owner aborts the entire payout cycle — no payouts, no mint fault."""
    _configure(
        router,
        owner_lnurl=DEAD_LNURL,
        maint_lnurl="tollgate@minibits.cash",
    )
    logs = _trigger_payout(router)

    assert re.search(r"owner is unreachable .* aborting all payouts", logs), (
        "expected 'owner is unreachable — aborting all payouts' when the owner "
        f"LNURL is dead; logs tail:\n{logs[-1200:]}"
    )
    # No recipient payout should have been attempted.
    assert "Processing payout for mint" not in logs, (
        "a payout was attempted despite the owner being unreachable"
    )
    assert "Marking unreachable" not in logs
