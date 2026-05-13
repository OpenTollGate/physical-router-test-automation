import re

import pytest

from lib.helpers import parse_json_or_fail

pytestmark = [pytest.mark.api, pytest.mark.extended]


@pytest.fixture(scope="module")
def backend_logs(router):
    return router.get_tollgate_logs(lines=1000)


@pytest.fixture(scope="module")
def discovery(router):
    return parse_json_or_fail(router.api_body("/"), "discovery response")


def test_logs_show_mint_health_tracking(backend_logs):
    health_signals = re.findall(
        r"(health|reachable|unreachable|mint.*check)",
        backend_logs, re.IGNORECASE,
    )
    if not health_signals:
        pytest.skip("No mint health tracking signals found in backend logs")
    assert len(health_signals) > 0


@pytest.mark.pr(118)
def test_logs_show_dynamic_rebuild(backend_logs):
    rebuild_signals = re.findall(
        r"(rebuilding merchant|reachable mint set changed|merchant rebuilt)",
        backend_logs, re.IGNORECASE,
    )
    if not rebuild_signals:
        pytest.skip("No dynamic merchant rebuild signals in recent logs")
    assert len(rebuild_signals) > 0


@pytest.mark.pr(118)
def test_discovery_excludes_unhealthy_mints(backend_logs, discovery):
    unreachable = set(re.findall(
        r"mint (\S+) .*(?:unreachable|failed)",
        backend_logs, re.IGNORECASE,
    ))
    if not unreachable:
        pytest.skip("No unreachable mints found in logs")

    discovery_tags = discovery.get("tags", [])
    price_tags = [t for t in discovery_tags if isinstance(t, list) and t[0] == "price_per_step"]
    discovery_urls = {t[4] for t in price_tags if len(t) >= 5}

    stale = unreachable & discovery_urls
    assert not stale, \
        f"Unreachable mints still in discovery ad: {stale}"


def test_wallet_info_shows_mint_count(router):
    info = router.get_wallet_info()
    data = info.get("data", {})
    mint_count = data.get("mint_count", -1)
    assert isinstance(mint_count, int)
    assert mint_count >= 0


def test_status_command_works(router):
    status = router.get_tollgate_status()
    assert status.get("success") is True, f"Status command failed: {status}"


def test_version_matches_installed(router):
    version = router.get_tollgate_version()
    assert version.get("success") is True
    msg = version.get("message", "")
    assert "version:" in msg, f"No version line in message: {msg}"
