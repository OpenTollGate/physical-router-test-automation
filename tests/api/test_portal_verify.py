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


def _nds_gateway_responsive(router):
    """Check if NDS is running via multiple detection methods.

    Primary: ndsctl status (canonical NDS health check).
    Fallback 1: pidof nodogsplash (process alive).
    Fallback 2: netstat shows NDS listening on gatewayport.

    We use multiple methods because ndsctl can fail if:
    - The Unix socket path is wrong or not yet created
    - ndsctl returns unexpected output format
    - BusyBox ndsctl has different behavior than expected
    """
    # Method 1: ndsctl status
    resp = router.ssh("ndsctl status 2>/dev/null | head -1", timeout=5)
    if resp and ("nodogsplash" in resp.lower() or "version" in resp.lower()):
        return True
    # Method 2: process check
    pid = router.ssh("pidof nodogsplash 2>/dev/null", timeout=5)
    if pid and pid.strip().isdigit():
        return True
    # Method 3: port listening
    port = router.ssh(
        "netstat -tlnp 2>/dev/null | grep nodogsplash | head -1", timeout=5
    )
    if port and "nodogsplash" in port:
        return True
    return False


def _skip_unless_nds_responsive(router):
    """Skip test if nodogsplash is not running.

    Uses multiple detection methods because ndsctl status alone is
    unreliable across NDS versions and OpenWrt builds.
    """
    if not _nds_gateway_responsive(router):
        pytest.skip("nodogsplash not running (ndsctl/pidof/netstat all failed)")


def _nds_serves(router, path):
    """Fetch `path` from the local NDS gateway via busybox wget; return the body.

    Non-empty body means NDS answered (covers 200/302/500/511 — NDS returns a
    body for all of these, including the pre-auth 511 page). Replaces the
    previous curl probe (curl is absent from the minimal GL-MT3000 image, and
    busybox wget here has no -S/--server-response option).
    """
    return router.ssh(f"wget -q -O - 'http://127.0.0.1:2050{path}' 2>/dev/null", timeout=10)


class TestBuiltinPortal:
    """Checks that run when the builtin portal is expected."""

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "builtin", reason="only for builtin portal")
    def test_builtin_portal_html_exists(self, router):
        html = router.ssh("cat /etc/nodogsplash/htdocs/splash.html 2>/dev/null | head -5")
        assert html.strip(), "builtin portal splash.html missing from htdocs"

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "builtin", reason="only for builtin portal")
    def test_builtin_portal_served_via_nds(self, router):
        _skip_unless_nds_responsive(router)
        body = _nds_serves(router, "/splash.html")
        assert body.strip(), "nodogsplash gateway not serving splash.html on :2050"

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "builtin", reason="only for builtin portal")
    def test_builtin_portal_has_spa_assets(self, router):
        ls = router.ssh("ls /etc/nodogsplash/htdocs/assets/*.js 2>/dev/null")
        assert ls.strip(), "builtin portal missing SPA JS bundles in htdocs/assets/"


class TestNet4satsPortal:
    """Checks that run when the net4sats/configurationwizzard portal is expected."""

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_package_installed(self, router):
        pkgs = router.ssh("opkg list-installed | grep configurationwizzard", timeout=10)
        assert "configurationwizzard" in pkgs, (
            f"configurationwizzard not in opkg list-installed. Got: {pkgs!r}"
        )

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_portal_html_exists(self, router):
        html = router.ssh("ls /etc/nodogsplash/htdocs/portal.html 2>/dev/null")
        assert "portal.html" in html, "net4sats portal.html missing from htdocs"

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_portal_served_via_nds(self, router):
        _skip_unless_nds_responsive(router)
        body = _nds_serves(router, "/portal.html")
        assert body.strip(), "nodogsplash gateway not serving portal.html on :2050"

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_portal_has_payment_elements(self, router):
        html = router.ssh("cat /etc/nodogsplash/htdocs/portal.html 2>/dev/null")
        lower = html.lower()
        has_cashu = "cashu" in lower or "token" in lower or "ecash" in lower
        has_ln = "lightning" in lower or "lninvoice" in lower or "invoice" in lower
        assert has_cashu or has_ln, (
            f"net4sats portal missing payment elements (cashu={has_cashu}, ln={has_ln})"
        )

    @pytest.mark.smoke
    @pytest.mark.skipif(PORTAL_TYPE != "net4sats", reason="only for net4sats portal")
    def test_net4sats_manifest_exists(self, router):
        manifest = router.ssh("cat /etc/nodogsplash/htdocs/manifest.json 2>/dev/null")
        assert manifest.strip(), "net4sats portal manifest.json missing"


class TestPortalAgnostic:
    """Checks that should pass regardless of which portal is installed."""

    @pytest.mark.smoke
    def test_nds_htdocs_directory_exists(self, router):
        ls = router.ssh("ls /etc/nodogsplash/htdocs/ 2>/dev/null")
        assert ls.strip(), "/etc/nodogsplash/htdocs/ is empty or missing"

    @pytest.mark.smoke
    def test_nds_gateway_responds(self, router):
        _skip_unless_nds_responsive(router)
        body = _nds_serves(router, "/")
        assert body.strip(), "nodogsplash gateway not responding on :2050"

    @pytest.mark.smoke
    def test_tollgate_backend_healthy(self, router):
        # Probe from the host via Router.api_status (uses the host's curl, so no
        # curl/wget is required on the router).
        assert router.api_status("/") == 200, (
            f"tollgate backend not healthy at {router.backend_url('/')} (got non-200)"
        )
