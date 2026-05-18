import base64
import os
import threading
import time
from typing import Any

import pytest

from lib.constants import NDS_PORTAL_PORT

pytestmark = [pytest.mark.api, pytest.mark.smoke, pytest.mark.virtual_lab, pytest.mark.publish_screenshot, pytest.mark.timeout(180)]

try:
    from pytest_html import extras as html_extras
except ImportError:
    html_extras = None


def _skip_unless_virtual_lab():
    if not (os.environ.get("TOLLGATE_SSH_JUMP_HOST") or os.environ.get("TOLLGATE_VIRTUAL_HOST") or os.environ.get("TOLLGATE_VIRTUAL_LAB")):
        pytest.skip("set TOLLGATE_VIRTUAL_LAB=1 and run scripts/virtual-lab.py start-poc")


def _embed_screenshot(path: str, name: str, request):
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    if b64 and html_extras is not None:
        extras_list = getattr(request.node, "_screenshot_extras", [])
        extras_list.append(html_extras.image(b64, name))
        request.node._screenshot_extras = extras_list


def _embed_video(path: str, request):
    if not os.path.exists(path):
        return
    video_size = os.path.getsize(path)
    if video_size > 20 * 1024 * 1024:
        return
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    if b64 and html_extras is not None:
        extras_list = getattr(request.node, "_screenshot_extras", [])
        extras_list.append(
            html_extras.video(b64, name="Portal Happy Path", mime_type="video/webm", extension="webm")
        )
        request.node._screenshot_extras = extras_list


def test_visual_happy_path(adb, router, results_dir, request):
    _skip_unless_virtual_lab()

    client = request.config.getoption("--client", default="adb")
    if client != "container":
        pytest.skip("visual test requires --client=container")

    gateway = os.environ.get("TOLLGATE_VIRTUAL_GATEWAY", "10.99.99.1")
    client_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")
    output_dir = os.path.join(results_dir, "raw", "visual")
    os.makedirs(output_dir, exist_ok=True)

    print(f"[visual] gateway={gateway} client_mac={client_mac}")

    portal_url = f"http://{gateway}:{NDS_PORTAL_PORT}/"
    code = ""
    for _ in range(20):
        code = adb.curl(portal_url, o="/dev/null", w="%{http_code}", s=True).strip()
        if code.startswith("2") or code in ("404", "500"):
            break
        time.sleep(1)
    assert code.startswith("2") or code in ("404", "500"), f"client must reach portal, got HTTP {code}"
    print(f"[visual] client can reach portal ({code})")

    # Step 0.5: deauth client so NDS intercepts and shows portal
    if client_mac:
        try:
            router.ssh(f"ndsctl deauth {client_mac} 2>/dev/null || true", timeout=10)
            time.sleep(1)
        except Exception:
            pass

    # Step 1: start recording thread (video + screenshots)
    adb.start_portal_recording()
    recording_result: list[dict[str, Any] | None] = [None]

    def run_recording():
        recording_result[0] = adb.finish_portal_recording(output_dir, timeout=120)

    recording_thread = threading.Thread(target=run_recording, daemon=True)
    recording_thread.start()

    # Step 2: wait for portal to load and unpaid screenshot
    assert adb.wait_for_portal_ready(timeout=30), "Playwright did not load portal in time"
    print("[visual] portal loaded, unpaid screenshot taken by recording thread")

    # Step 3: authenticate via ndsctl (bypass cashu)
    assert client_mac, "TOLLGATE_CLIENT_MAC required for ndsctl auth"
    print(f"[visual] authenticating {client_mac} via ndsctl...")
    router.ssh(f"ndsctl auth {client_mac} 2>&1", timeout=10)

    # Step 4: wait for authentication to take effect
    authenticated = router.wait_for_auth(timeout=15, mac=client_mac)
    if not authenticated:
        router.ssh(f"ndsctl auth {client_mac} 2>&1", timeout=10)
        authenticated = router.wait_for_auth(timeout=10, mac=client_mac)
    print(f"[visual] authenticated={authenticated}")

    # Step 5: signal paid so recording thread continues to paid screenshots
    adb.signal_paid()

    # Step 6: wait for recording to finish
    recording_thread.join(timeout=120)
    artifacts = recording_result[0]
    print(f"[visual] recording done, artifacts={artifacts}")

    # Step 7: embed screenshots from recording
    if artifacts:
        for path in artifacts.get("screenshots", []):
            step = os.path.basename(path).replace(".png", "")
            _embed_screenshot(path, step, request)
            print(f"[visual] embedded screenshot: {step}")

        video_path = artifacts.get("video")
        if video_path:
            _embed_video(video_path, request)
            print(f"[visual] embedded video")

    # Step 8: standalone screenshot of the portal (in case recording failed)
    portal_shot = os.path.join(output_dir, "portal-standalone.png")
    try:
        adb.screenshot_portal(portal_shot)
        _embed_screenshot(portal_shot, "portal-standalone", request)
    except Exception as exc:
        print(f"[visual] standalone screenshot skipped: {exc}")

    assert authenticated, "client should be authenticated after ndsctl auth"
