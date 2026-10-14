"""Installer endpoint contract coverage beyond scan/identify/deploy/status.

Fills the gaps in PRTA's installer lane so the whole wizard surface is covered:

  * ``POST /api/prestage``          -> a job id (the selection-time pre-download)
  * ``POST /api/clean-slate-info``  -> the published board + checksum for a version
  * ``POST /api/wifi-scan``         -> the upstream-SSID list (or a bounded error)

``/api/scan``, ``/api/identify``, ``/api/deploy``, ``/api/status`` and
``/api/recheck`` are covered by ``test_installer_e2e`` / ``test_installer_identify``
/ ``test_installer_health_gate``. The wizard's host-key trust behaviour is pinned
end to end by the installer repo's ``tests/ui/deploy-trust-ui.mjs`` (real binary +
fixture SSH + trust store); PRTA drives the same wizard against the QEMU lab.

Run explicitly: ``pytest installer/ -v`` (needs the QEMU lab: KVM + creds).
"""
from __future__ import annotations

import pytest

from lab_vm import VM_IP

LN_ADDRESS = "tollgate@minibits.cash"

pytestmark = [
    pytest.mark.story("O5"),
    pytest.mark.installer,
    pytest.mark.virtual_lab,
    pytest.mark.slow,
]


@pytest.mark.timeout(120)
def test_prestage_returns_a_job(installer_service, installer_lab):
    """The selection-time pre-download starts a job the wizard can poll.

    A job id (not a bare 200) is the contract: the UI polls /api/status/<id> for
    progress, so a body without one leaves the prestage spinner stuck.
    """
    resp = installer_service.post_json(
        "/api/prestage",
        {"ip": VM_IP, "password": installer_lab.password, "forceFlash": False},
        timeout=120,
    )
    assert resp.get("job_id"), f"/api/prestage must return a job_id, got: {resp}"


@pytest.mark.timeout(120)
def test_clean_slate_info_answers_for_a_version(installer_service, installer_lab):
    """The clean-slate card resolves a board + published checksum for a version."""
    resp = installer_service.post_json(
        "/api/clean-slate-info",
        {"ip": VM_IP, "password": installer_lab.password, "version": "25.12.5"},
        timeout=120,
    )
    # The answer is the info card; a hard error surfaces as "error".
    assert not resp.get("error"), f"/api/clean-slate-info errored: {resp}"


@pytest.mark.timeout(180)
def test_wifi_scan_answers_with_a_bounded_result(installer_service, installer_lab):
    """The upstream SSID scan answers with a list or a bounded, non-crashing error.

    In WAN mode there is no radio uplink to scan, so the honest answer may be an
    empty list or a classified error — but it must be JSON with a stable shape,
    never a 500 the wizard cannot render.
    """
    resp = installer_service.post_json(
        "/api/wifi-scan", {"ip": VM_IP, "mac": "", "password": installer_lab.password},
        timeout=180,
    )
    assert isinstance(resp, dict), f"/api/wifi-scan must answer JSON, got: {resp!r}"
    assert "ssids" in resp or "error" in resp, (
        f"/api/wifi-scan must carry 'ssids' or a classified 'error': {resp}"
    )
