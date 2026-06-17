"""#95: self-signed certificate / HTTPS for captive-portal camera (QR) access.

Browsers only allow camera access (getUserMedia, for QR scanning) inside a
secure context — HTTPS (or localhost / a `.local`-style hostname). Android's
captive-portal WebView blocks the camera by design (see
tests/phone/test_camera_captive.py), so for QR-based payment the portal must be
opened in a *real* browser over HTTPS — which requires the self-signed cert
delivered by the Go SSL rewrite (#123 / #142).

This test validates that deliverable end-to-end on a physical router:
after `tollgate ssl apply`, the captive portal is served over HTTPS and the
QR-scanner element (the camera-dependent feature) is present in the response.

Skips cleanly when the `tollgate ssl` subcommand isn't available or in the
virtual lab (uhttpd HTTPS in QEMU is unreliable — see test_ssl_apply_remove_lifecycle.py).
"""

import re

import pytest

from lib.helpers import skip_if_no_ssl_cli, ssl_is_applied

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.timeout(120)]


def _skip_virtual_lab():
    import os

    if os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        pytest.skip("SSL/camera test requires a physical router (uhttpd HTTPS in QEMU is unreliable)")


def _https_portal_body(router) -> tuple[str, str]:
    """Fetch the captive portal splash over HTTPS (trusting the self-signed cert).

    Returns (http_code, body_snippet).
    """
    gateway = router.gateway_ip
    out = router.ssh(
        f"curl -sk -o /tmp/_portal_https_body --max-time 15 "
        f"-w '%{{http_code}}' 'https://{gateway}/' 2>&1",
        timeout=25,
    )
    code = ""
    if out.strip():
        code = out.strip().splitlines()[-1].strip("'")
    body = router.ssh("cat /tmp/_portal_https_body 2>/dev/null | head -c 8000", timeout=10)
    return code, body


def test_self_signed_cert_serves_portal_over_https_for_qr_scanner(router):
    """#95: `tollgate ssl apply` (self-signed) must serve the captive portal over
    HTTPS with the QR-scanner element present — the secure context a real browser
    needs to grant camera access for QR scanning.
    """
    _skip_virtual_lab()
    skip_if_no_ssl_cli(router)

    try:
        apply_out = router.ssh("tollgate ssl apply --yes 2>&1", timeout=90)
        assert ssl_is_applied(router), (
            f"`tollgate ssl apply --yes` did not leave SSL applied: {apply_out[:300]}"
        )

        code, body = _https_portal_body(router)
        assert code == "200", (
            f"Captive portal not reachable over HTTPS (http_code={code!r}); "
            f"self-signed cert not serving — QR/camera secure context broken (#95)."
        )

        assert re.search(r"TollGate|cashu|Cashu", body, re.IGNORECASE), (
            f"HTTPS response is not the TollGate portal: {body[:200]!r}"
        )

        # The QR scanner is the camera-dependent feature #95 is about.
        has_qr = bool(
            re.search(r"qr[-_ ]?scanner|scanner|getUserMedia|mediaDevices", body, re.IGNORECASE)
        )
        assert has_qr, (
            "Portal served over HTTPS but no QR-scanner / camera element found — "
            "the secure-context capability #95 enables isn't reachable in the UI."
        )

    finally:
        # Leave the router on plain HTTP for the rest of the suite.
        router.ssh("tollgate ssl remove --yes 2>&1 || true", timeout=90)
