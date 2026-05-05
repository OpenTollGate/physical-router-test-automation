# TIP-03: Token Delivery via URL Parameter
#
# Portal JS reads ?token= from URL and submits to TIP-03 POST /.

import urllib.parse
import pytest
from lib.helpers import assert_session_active
from lib.constants import TOKEN_DEFAULT

pytestmark = [pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120), pytest.mark.extended]


def test_url_param_token(router, adb, cashu, connected_wifi, screenshot_portal):
    assert router.domain, "TOLLGATE_DOMAIN not set in .env — required for URL param test"
    token = cashu.mint(TOKEN_DEFAULT)
    encoded = urllib.parse.quote(token)
    portal_url = f"http://{router.domain}:8080/?token={encoded}"

    adb.start_activity(action="android.intent.action.VIEW", data_uri=portal_url)

    assert router.wait_for_auth(timeout=60), "Auth not detected after URL param"

    screenshot_portal("url-param-final.png")
    assert_session_active(router)
