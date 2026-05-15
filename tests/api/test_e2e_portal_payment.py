import base64
import os
import subprocess
import threading
from typing import Any

import pytest

from lib.constants import TOKEN_DEFAULT
from lib.helpers import is_session_event

pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab, pytest.mark.timeout(180)]

try:
    from pytest_html import extras as html_extras
except ImportError:
    html_extras = None


def _skip_unless_virtual_lab():
    if os.environ.get("TOLLGATE_SSH_JUMP_HOST", "") == "" and os.environ.get("TOLLGATE_VIRTUAL_HOST", "") == "":
        pytest.skip("set TOLLGATE_SSH_JUMP_HOST and run scripts/virtual-lab.py start-poc")


def test_e2e_portal_payment(adb, cashu, router, results_dir, request):
    """Full e2e with video: portal loads → payment → session active → internet.

    A Playwright session on the Debian VM records video of the entire flow.
    Signal files synchronize: Playwright loads portal and signals readiness,
    the test pays via backend API, then tells Playwright to continue recording
    the authenticated portal and internet access.
    """
    _skip_unless_virtual_lab()

    client = request.config.getoption("--client", default="adb")
    if client != "container":
        pytest.skip("e2e visual flow requires --client=container")

    gateway = os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", "192.168.1.1")
    assert adb.ping(gateway, count=1, timeout=3), "client must reach gateway"

    output_dir = os.path.join(results_dir, "raw", "e2e")
    os.makedirs(output_dir, exist_ok=True)

    adb.start_portal_recording()

    recording_result: list[dict[str, Any] | None] = [None]

    def run_recording():
        recording_result[0] = adb.finish_portal_recording(output_dir, timeout=120)

    recording_thread = threading.Thread(target=run_recording, daemon=True)
    recording_thread.start()

    assert adb.wait_for_portal_ready(timeout=30), "Playwright did not load portal in time"

    token = cashu.mint(TOKEN_DEFAULT)
    assert token, "cashu mint failed"

    resp = router.pay_direct_mac(token)
    assert is_session_event(resp), f"payment failed: {str(resp)[:200]}"

    assert router.wait_for_auth(timeout=30), "not authenticated after payment"

    adb.signal_paid()

    recording_thread.join(timeout=120)
    assert recording_result[0] is not None, "recording thread did not complete"
    artifacts: dict[str, Any] = recording_result[0]
    assert artifacts["ok"], f"portal recording failed: {artifacts}"

    extras_list = getattr(request.node, "_screenshot_extras", [])

    screenshots = artifacts.get("screenshots", [])
    if screenshots:
        for path in screenshots:
            if not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            if b64 and html_extras is not None:
                step = os.path.basename(path).replace(".png", "")
                extras_list.append(html_extras.image(b64, step))

    video_path = artifacts.get("video")
    if video_path and os.path.exists(video_path):
        video_size = os.path.getsize(video_path)
        if video_size < 20 * 1024 * 1024 and html_extras is not None:
            with open(video_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            extras_list.append(
                html_extras.video(b64, name="Portal Flow", mime_type="video/webm", extension="webm")
            )

    request.node._screenshot_extras = extras_list
