# Backend Restart Resilience
#
# nodogsplash maintains firewall rules independently of the Go backend.

import time
import pytest
from lib.helpers import pay_and_wait, assert_internet
from lib.constants import TOKEN_SMALL

pytestmark = [pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120), pytest.mark.extended]


def test_backend_restart(router, adb, cashu, connected_wifi, screenshot_raw):
    token = cashu.mint(TOKEN_SMALL)

    pay_and_wait(router, adb, token)

    assert assert_internet(adb), "No internet before restart"

    router.ssh("service tollgate-wrt restart")
    time.sleep(5)

    health_code = router.api_status("/")
    assert health_code == 200, f"Backend not responsive after restart (HTTP {health_code})"

    session_after = router.get_session()

    if session_after.get("session_active"):
        assert assert_internet(adb), "Session active but no internet after restart"
    else:
        state = router.get_nds_state()
        if state == "Authenticated":
            assert assert_internet(adb), \
                "nodogsplash still Authenticated but no internet — session lost after restart"

    screenshot_raw("br-final.png")
