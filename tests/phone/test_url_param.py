# TIP-03: Token Delivery via URL Parameter
#
# Portal JS reads ?token= from URL and submits to TIP-03 POST /.
# Falls back to router LAN IP when TOLLGATE_DOMAIN is not configured.

import urllib.parse
import pytest
from lib.helpers import assert_session_active
from lib.constants import TOKEN_DEFAULT

pytestmark = [pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120), pytest.mark.extended]


def test_url_param_token(router, adb, cashu, connected_wifi, screenshot_portal):
    portal_host = router.domain or "192.168.1.1"
    token = cashu.mint(TOKEN_DEFAULT)
    encoded = urllib.parse.quote(token)
    portal_url = f"http://{portal_host}:8080/?token={encoded}"

    adb.start_activity(action="android.intent.action.VIEW", data_uri=portal_url)

    assert router.wait_for_auth(timeout=60), "Auth not detected after URL param"

    screenshot_portal("url-param-final.png")
    assert_session_active(router)
