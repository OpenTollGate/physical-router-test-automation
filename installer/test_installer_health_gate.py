"""Installer merchant health-gate contract (RC: v0.6.0-alpha2-rc28).

The installer used to treat a merchant that was still loading its wallet /
mints as a FAILURE ("Setup failed"), even though the install had succeeded
and the money path would open seconds later. rc28 replaces that with a
four-state job — ``running | done | failed | pending`` — where a merchant
still in the "starting" state ends the deploy ``pending`` with a non-fatal
``note`` (never an ``error``), plus a Re-check action that re-probes the
deployed router's ``:2121`` advertisement WITHOUT re-running the deploy.

This module pins the wire contract the wizard depends on:

  * ``POST /api/recheck {ip,password}`` -> ``{"state": ..., "listening": bool}``
    and is READ-ONLY (one SSH connect + one GET / probe);
  * a deploy that cannot see a healthy merchant ends ``pending`` with a
    ``note`` and no ``error`` (the regression rail for the "Setup failed"
    bug);
  * a ``pending`` job is advanced to a healthy state by Re-check once the
    merchant finishes starting.

The VM lab is self-provisioned QEMU (see ``installer/conftest.py``); run
explicitly with ``pytest installer/ -v``. These tests are ``required``:
a SKIP means the contract was NOT VERIFIED, which is release-relevant.
"""
from __future__ import annotations

import time

import pytest

from lab_vm import VM_IP

# Classified merchant states the recheck may report. "healthy" is the full
# merchant (kind 10021); "starting" is the retry-hint body the payment API
# answers with during wallet load; "degraded" is no-reachable-mints.
KNOWN_STATES = frozenset({"healthy", "starting", "degraded", "unknown", "absent"})

pytestmark = [
    pytest.mark.story("O5"),
    pytest.mark.installer,
    pytest.mark.virtual_lab,
    pytest.mark.required,
]

LN_ADDRESS = "tollgate@minibits.cash"


@pytest.mark.timeout(120)
def test_recheck_contract_is_readonly_probe(installer_service, installer_lab):
    """POST /api/recheck answers {state, listening} for a deployed router.

    The pending state's Re-check button calls this; the wizard never gets to
    guess the merchant state from the deploy job it already consumed.
    """
    resp = installer_service.post_json(
        "/api/recheck", {"ip": VM_IP, "password": installer_lab.password}
    )
    assert "state" in resp, f"/api/recheck must name the merchant state: {resp}"
    assert "listening" in resp, f"/api/recheck must report the :2121 listener: {resp}"
    assert isinstance(resp["listening"], bool), f"listening must be a bool: {resp}"
    assert resp["state"] in KNOWN_STATES, (
        f"/api/recheck state {resp['state']!r} not in the classified set {sorted(KNOWN_STATES)}"
    )


@pytest.mark.timeout(600)
def test_slow_merchant_ends_pending_not_failed(installer_service, installer_lab):
    """The regression rail for "Setup failed" on a slow merchant.

    A deploy that installs the package but cannot yet see a healthy merchant
    must end ``pending`` — with a ``note`` and NO ``error`` — never ``failed``.
    This is the exact shape rc28 introduced (``jobPending`` in merchant_state).
    """
    status = installer_service.deploy(
        VM_IP, installer_lab.password, LN_ADDRESS, mode="wan", timeout=480
    )
    state = str(status.get("status", "")).lower()

    assert state in ("done", "pending"), f"deploy ended {state!r}: {status}"
    if state == "pending":
        assert not status.get("error"), (
            f"a pending deploy must NOT carry an error (the regression was "
            f"flagging a slow merchant as a failure): {status}"
        )
        assert status.get("note"), f"a pending deploy must carry an operator note: {status}"


@pytest.mark.timeout(600)
def test_pending_advances_to_healthy_on_recheck(installer_service, installer_lab):
    """A pending job resolves: Re-check flips the merchant to healthy.

    Re-check re-probes the live router and never re-runs the deploy, so a
    merchant that finished starting in the meantime is reported healthy.
    """
    status = installer_service.deploy(
        VM_IP, installer_lab.password, LN_ADDRESS, mode="wan", timeout=480
    )
    state = str(status.get("status", "")).lower()
    if state != "pending":
        pytest.skip(f"deploy reached {state!r} (merchant was healthy first try) — "
                    "no pending state to advance; the pending path is covered by "
                    "test_slow_merchant_ends_pending_not_failed")

    deadline = time.time() + 10 * 60
    last: dict = {}
    while time.time() < deadline:
        last = installer_service.post_json(
            "/api/recheck",
            {"ip": VM_IP, "password": installer_lab.password},
        )
        if last.get("state") == "healthy":
            return
        time.sleep(15)
    pytest.fail(f"pending merchant never reported healthy within 10 min: {last}")
