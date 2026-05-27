"""Portal deployment verification tests.

Validates that the correct captive portal assets are installed on the router
based on the TOLLGATE_PORTAL env var (builtin or net4sats).  Runs after the
cloud lab worker deploys the portal overlay, so these tests confirm the
deploy step actually worked.
"""

import os

import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab]

PORTAL_TYPE = os.environ.get("TOLLGATE_PORTAL", "builtin").lower()


class TestBuiltinPortal:
    """Checks that run when the builtin portal is expected."""

    @pytest.mark.skipif(PORTAL_TYPE != "builtin", reason="only for builtin portal")
    def test_builtin_portal_html_exists(self, router):
        html = router.ssh("cat /etc/nodogsplash/htdocs/splash.html 2>/dev/null | head -5")
        assert html.strip(), "builtin portal splash.html missing from htdocs"

    @pytest.mark.skipif(PORTAL_TYPE != "builtin", reason="only for builtin portal")
    def test_builtin_portal_served_via_nds(self, router):
        curl = router.ssh(
            "curl -s -o /dev/null -w '%{http_code}' "
            "http://127.0.0.1:2050/splash.html 2>/dev/null",
            timeout=10,
        )
        assert "200" in curl, f"nodogsplash not serving splash.html (got {curl})"

    @pytest.mark.skipif(PORTAL_TYPE != "builtin", reason="only for builtin portal")
    def test_builtin_portal_has_cashu_form(self, router):
        html = router.ssh("cat /etc/nodogsplash/htdocs/splash.html 2>/dev/null")
        assert "cashu" in html.lower(), "builtin portal missing cashu form element"


class TestNet4satsPortal:
    """Checks that run when the net4sats/configurationwizzard portal is expected."""

    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_package_installed(self, router):
        pkgs = router.ssh("opkg list-installed | grep configurationwizzard", timeout=10)
        assert "configurationwizzard" in pkgs, (
            f"configurationwizzard not in opkg list-installed. Got: {pkgs!r}"
        )

    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_portal_html_exists(self, router):
        html = router.ssh("ls /etc/nodogsplash/htdocs/portal.html 2>/dev/null")
        assert "portal.html" in html, "net4sats portal.html missing from htdocs"

    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_portal_served_via_nds(self, router):
        curl = router.ssh(
            "curl -s -o /dev/null -w '%{http_code}' "
            "http://127.0.0.1:2050/portal.html 2>/dev/null",
            timeout=10,
        )
        assert "200" in curl, f"nodogsplash not serving portal.html (got {curl})"

    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_portal_has_payment_elements(self, router):
        html = router.ssh("cat /etc/nodogsplash/htdocs/portal.html 2>/dev/null")
        lower = html.lower()
        has_cashu = "cashu" in lower or "token" in lower or "ecash" in lower
        has_ln = "lightning" in lower or "lninvoice" in lower or "invoice" in lower
        assert has_cashu or has_ln, (
            f"net4sats portal missing payment elements (cashu={has_cashu}, ln={has_ln})"
        )

    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_manifest_exists(self, router):
        manifest = router.ssh("cat /etc/nodogsplash/htdocs/manifest.json 2>/dev/null")
        assert manifest.strip(), "net4sats portal manifest.json missing"


class TestPortalAgnostic:
    """Checks that should pass regardless of which portal is installed."""

    def test_nds_htdocs_directory_exists(self, router):
        ls = router.ssh("ls /etc/nodogsplash/htdocs/ 2>/dev/null")
        assert ls.strip(), "/etc/nodogsplash/htdocs/ is empty or missing"

    def test_nds_gateway_responds(self, router):
        curl = router.ssh(
            "curl -s -o /dev/null -w '%{http_code}' "
            "http://127.0.0.1:2050/ 2>/dev/null",
            timeout=10,
        )
        assert curl.strip() in ("200", "302", "301"), (
            f"nodogsplash gateway not responding on :2050 (got {curl})"
        )

    def test_tollgate_backend_healthy(self, router):
        curl = router.ssh(
            "curl -s -o /dev/null -w '%{http_code}' "
            f"http://[::1]:{os.environ.get('TOLLGATE_BACKEND_PORT', '2121')}/ 2>/dev/null",
            timeout=10,
        )
        assert "200" in curl, f"tollgate backend not healthy on :2121 (got {curl})"
