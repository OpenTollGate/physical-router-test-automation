"""Phone-tier tests for PR #117: hostname resolution and captive portal.

HTTPS is opt-in (verified by API tests). Phone tests only confirm the
user-facing behaviour: captive portal opens over HTTP and DNS resolves
the TollGate hostname.
"""

import pytest

pytestmark = [pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120),
              pytest.mark.critical, pytest.mark.pr(117)]


def _is_pr117_installed(router):
    hostname = router.ssh("uci get system.@system[0].hostname 2>/dev/null").strip()
    return hostname.lower() == "tollgate"


def _skip_if_no_pr117(router):
    if not _is_pr117_installed(router):
        pytest.skip("PR #117 not installed (hostname is not 'TollGate')")


def test_captive_portal_opens_on_phone(router, adb, connected_wifi, screenshot_portal):
    _skip_if_no_pr117(router)

    screenshot_portal("hostname-portal.png")

    xml = adb.ui_xml()
    found = any(keyword in xml.lower() for keyword in
                ["tollgate", "portal_ready", "token_typing", "data-sm="])
    assert found, \
        f"Captive portal did not load on phone after WiFi connect. UI text: {xml[:300]}"


def test_phone_can_resolve_tollgate_hostname(router, adb, connected_wifi):
    _skip_if_no_pr117(router)

    result = adb.shell("ping -c 1 -W 3 tollgate.lan 2>&1", timeout=15)
    failed = any(s in result.lower() for s in
                 ["unknown host", "cannot resolve", "not found",
                  "no address", "bad address", "name or service not known"])
    assert not failed, \
        f"Phone cannot resolve 'tollgate.lan' via DNS. ping output: {result[:300]}"
