"""The installer's step-11 health probe must work on the ROUTER's own shell.

OpenWrt's ``wget`` is uclient-fetch (``/usr/bin/wget -> /bin/uclient-fetch``),
which has **no ``-S``**. The installer's probe used ``wget -S``, which
uclient-fetch rejects with ``unrecognized option: S`` — so the probe read the
tool's usage text instead of the API body, the gate classified a HEALTHY
merchant as ``unknown``, and every install reported "Setup failed" (rc28–rc30,
tollgate-installer #134). ``/api/recheck`` was wrong the same way.

These run the installer's EXACT probe one-liner over SSH against a live router:

  * the installer's probe reads the advertisement (a healthy merchant), and
  * ``-S`` is unsupported here — the negative control that explains the bug and
    would catch a re-introduction.

Default lane: probe-only (fast, non-destructive). The full wizard-deploy smoke
lives in ``test_installer_deploy_smoke.py`` and is opt-in.
"""
from __future__ import annotations

import pytest

# The installer's healthProbeCmd (tollgate-installer internal/app/deploy.go).
HEALTH_PROBE_CMD = (
    "netstat -ltn 2>/dev/null | grep -q ':2121' && echo LISTEN || echo NOLISTEN; "
    "echo '~~'; wget -qO- --timeout=3 http://127.0.0.1:2121/ 2>&1 | head -c 600"
)

pytestmark = [
    pytest.mark.story("O5"),
    pytest.mark.api,
    pytest.mark.hardware,
    pytest.mark.smoke,
]


def _body(out: str) -> str:
    return out.split("~~", 1)[1].strip() if "~~" in out else ""


def test_probe_reads_the_advertisement_not_a_wget_error(router):
    """The installer's probe must return the API body on this router."""
    out = router.ssh(HEALTH_PROBE_CMD, timeout=25)
    assert "LISTEN" in out, f"the TollGate API is not listening on :2121:\n{out}"

    body = _body(out)
    assert "unrecognized option" not in body and "Usage: wget" not in body, (
        "the probe captured a wget usage error, not the API body — the installer "
        "would classify this as 'unknown' and fail the install:\n"
        f"{body[:200]}"
    )
    assert any(k in body for k in ('"kind"', '"metric"', '"pubkey"')) or "starting" in body.lower(), (
        f"probe body is neither the advertisement nor a starting refusal:\n{body[:200]}"
    )


def test_dash_S_is_unsupported_on_this_router(router):
    """The negative control: WHY the probe must not pass ``-S``.

    If a router's wget DOES accept ``-S`` this test fails, which is the signal to
    reconsider the flag choice rather than a real problem — so it is informative,
    not a hard contract. It stays because it is the one assertion that makes the
    incompatibility visible on hardware.
    """
    out = router.ssh(
        "wget -S -qO- --timeout=3 http://127.0.0.1:2121/ 2>&1 | head -c 160", timeout=25
    )
    assert "unrecognized option: S" in out or "Usage: wget" in out, (
        "expected uclient-fetch to reject -S (the bug's cause); this router's wget "
        f"appears to accept it — revisit the probe's flag choice:\n{out}"
    )
