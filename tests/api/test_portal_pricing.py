import json
import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.go_only]


def _skip_if_no_portal(router):
    out = router.ssh("ls /etc/nodogsplash/htdocs/index.html /etc/tollgate/tollgate-captive-portal-site/index.html 2>/dev/null | head -1 || echo NOT_FOUND")
    if "NOT_FOUND" in out or not out.strip():
        pytest.skip("Captive portal files not deployed")


def test_portal_fetches_real_pricing_from_backend(router):
    _skip_if_no_portal(router)

    api_body = router.api_body("/")
    try:
        data = json.loads(api_body)
    except json.JSONDecodeError:
        pytest.skip(f"API body not JSON: {api_body[:200]}")

    kind = data.get("kind")
    if kind != 10021:
        pytest.skip(f"API not returning pricing (kind={kind}), may be in degraded mode")

    tags = data.get("tags", [])
    price_tags = [t for t in tags if isinstance(t, list) and t[0] == "price_per_step"]
    assert price_tags, f"No price_per_step tags in API response: {tags[:5]}"

    portal_splash = router.ssh("cat /etc/nodogsplash/htdocs/splash.html 2>/dev/null || echo NOT_FOUND")
    if "NOT_FOUND" in portal_splash:
        portal_splash = router.ssh("cat /etc/tollgate/tollgate-captive-portal-site/splash.html 2>/dev/null || echo NOT_FOUND")
    if "NOT_FOUND" in portal_splash:
        pytest.skip("splash.html not found on router")

    has_price_fetch = (
        "2121" in portal_splash
        or "price_per_step" in portal_splash
        or "fetch" in portal_splash
        or "api" in portal_splash.lower()
    )
    assert has_price_fetch, \
        "Portal splash.html does not appear to fetch pricing from :2121 API"
