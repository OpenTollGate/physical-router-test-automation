import os

import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab]


def _skip_unless_virtual_lab():
    if os.environ.get("TOLLGATE_SSH_JUMP_HOST", "") == "" and os.environ.get("TOLLGATE_VIRTUAL_HOST", "") == "":
        pytest.skip("set TOLLGATE_SSH_JUMP_HOST=218 (or TOLLGATE_VIRTUAL_HOST) and run scripts/virtual-lab.py start-poc")


def test_portal_screenshot(screenshot_portal, adb, request):
    _skip_unless_virtual_lab()

    client = request.config.getoption("--client", default="adb")
    if client != "container":
        pytest.skip("portal screenshot visual test requires --client=container")

    gateway = os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", "192.168.1.1")
    assert adb.ping(gateway, count=1, timeout=3), "client must reach gateway before screenshot"

    screenshot_portal("portal-home.png")
