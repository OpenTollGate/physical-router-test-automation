import base64
import os
import re
import socket
import subprocess
import time
import logging

import pytest

try:
    from pytest_html import extras as html_extras
except ImportError:
    html_extras = None

from lib.router import Router
from lib.cashu import CashuMint
from lib.clients.adb import ADBDevice
from lib.clients.wifi import WiFi
from lib.clients.desktop import MacWiFiClient, MacAdapter, LinuxWiFiClient, LinuxAdapter
from lib.constants import DEFAULT_STEP_SIZE_MS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("tollgate.conftest")

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..")


def load_env():
    for candidate in [
        os.path.join(SCRIPT_DIR, ".env"),
    ]:
        if os.path.isfile(candidate):
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, val = line.partition("=")
                        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
            return


load_env()


def _results_dir():
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(SCRIPT_DIR, "results")
    return os.path.join(base, f"test-{ts}")


def _client_mode(request):
    return request.config.getoption("--client")


def _is_publish_mode(config):
    return config.getoption("--publish", default=False)


def pytest_addoption(parser):
    parser.addoption("--binary", default=None,
                     help="Install .ipk file on router before tests")
    parser.addoption("--restore", action="store_true",
                     help="Restore previous binary after tests")
    parser.addoption("--no-deploy", action="store_true",
                     help="Skip portal deploy before phone tests")
    parser.addoption("--results", default=None,
                     help="Custom results directory path")
    parser.addoption("--client", default="adb",
                     choices=["adb", "mac", "linux"],
                     help="WiFi client mode: adb (Android phone), mac (macOS), or linux (NetworkManager/nmcli)")
    parser.addoption("--publish", action="store_true",
                     help="Publish mode: only include screenshots from @pytest.mark.publish_screenshot tests in report")


@pytest.fixture(scope="session")
def results_dir(request):
    custom = request.config.getoption("--results")
    rd = custom or _results_dir()
    os.makedirs(os.path.join(rd, "raw"), exist_ok=True)
    os.makedirs(os.path.join(rd, "report"), exist_ok=True)
    return rd


@pytest.fixture(scope="session")
def router(request):
    host = os.environ.get("TOLLGATE_SSH_HOST") or os.environ.get("ROUTER_IP")
    identity_file = os.environ.get("TOLLGATE_SSH_KEY", "")
    client = _client_mode(request)
    phone_ip = os.environ.get("TOLLGATE_CLIENT_IP", "")
    phone_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")

    if client in ("mac", "linux"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            phone_ip = phone_ip or s.getsockname()[0]
        except Exception:
            phone_ip = phone_ip or ""
        finally:
            s.close()
        if client == "mac":
            phone_mac = phone_mac or MacWiFiClient().mac_address
        else:
            phone_mac = phone_mac or LinuxWiFiClient().mac_address
        log.info(f"--client={client}: auto-detected WiFi MAC {phone_mac}")
    else:
        phone_ip = phone_ip or ""
        phone_mac = phone_mac or ""
    domain = os.environ.get("TOLLGATE_DOMAIN", "")

    assert host, "TOLLGATE_SSH_HOST or ROUTER_IP not set in .env"

    return Router(host=host, phone_ip=phone_ip,
                  phone_mac=phone_mac, domain=domain, identity_file=identity_file or None)


@pytest.fixture(scope="session", autouse=True)
def deploy_session(request, router):
    binary = request.config.getoption("--binary")
    restore = request.config.getoption("--restore")

    if binary:
        subprocess.run(
            ["bash", os.path.join(SCRIPT_DIR, "scripts", "deploy.sh"), binary, "--restart"],
            check=False,
        )

    code = router.api_status("/")
    if code != 200:
        pytest.exit(f"Backend not reachable at {router.host}:2121 (HTTP {code})", returncode=1)

    router.enable_debug_portal()
    router.ensure_test_mint()

    yield

    try:
        if restore:
            print("\n[deploy] Restoring previous binary")
    finally:
        router.disable_debug_portal()


@pytest.fixture(scope="session")
def adb(request, router):
    client = _client_mode(request)
    if client == "mac":
        mac = MacWiFiClient()
        return MacAdapter(mac, router_domain=router.domain)
    if client == "linux":
        linux = LinuxWiFiClient()
        return LinuxAdapter(linux, router_domain=router.domain)
    serial = os.environ.get("PHONE_SERIAL", "")
    pin = os.environ.get("PHONE_PIN", "")
    return ADBDevice(serial=serial or None, pin=pin or None)


@pytest.fixture(scope="session")
def cashu():
    return CashuMint()


@pytest.fixture(scope="session")
def wifi(adb, router):
    ssid = os.environ.get("TOLLGATE_SSID", "TollGate")
    return WiFi(adb=adb, router=router, ssid=ssid)


@pytest.fixture(autouse=True)
def attach_results(request, results_dir):
    request.node._results_dir = results_dir


@pytest.fixture
def connected_wifi(router, wifi, adb):
    router.resolve_phone_client(adb)
    router.reset_state(adb=adb)
    assert wifi.reconnect(), "WiFi reconnect failed — portal did not render"
    router.resolve_phone_client(adb)
    yield
    router.reset_state(adb=adb)


@pytest.fixture
def test_pricing(router):
    router.ssh("cp /etc/tollgate/config.json /etc/tollgate/config.json.test-backup")

    def apply(step_size: int = DEFAULT_STEP_SIZE_MS, metric: str = "milliseconds"):
        router.apply_pricing(step_size=step_size, metric=metric)

    yield apply

    router.restore_pricing()


@pytest.fixture
def screenshot_portal(adb, results_dir, request):
    report_dir = os.path.join(results_dir, "report")
    publish_mode = _is_publish_mode(request.config)
    can_publish = "publish_screenshot" in request.keywords

    def take(name: str):
        raw_path = os.path.join(results_dir, "raw", name)
        adb.screenshot_portal(raw_path, report_dir=report_dir)

        if publish_mode and not can_publish:
            return

        try:
            with open(raw_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            if b64 and html_extras is not None:
                extra = getattr(request.node, "extra", [])
                extra.append(html_extras.image(b64, name))
                request.node.extra = extra
        except Exception as exc:
            log.debug(f"screenshot embed skipped: {exc}")

    return take


@pytest.fixture
def screenshot_raw(adb, results_dir):
    def take(name: str):
        path = os.path.join(results_dir, "raw", name)
        adb.screenshot(path)
        return path

    return take


def pytest_runtest_setup(item):
    client_mode = item.config.getoption("--client")
    if client_mode in ("mac", "linux"):
        if "android_only" in item.keywords:
            pytest.skip("Android-only test (requires physical device)")
        return

    if "phone" in item.keywords:
        serial = os.environ.get("PHONE_SERIAL", "")
        if not serial:
            pytest.skip("PHONE_SERIAL not set, phone tests require ADB device")


def pytest_collection_modifyitems(items):
    # Tier hierarchy: smoke ⊂ critical ⊂ extended
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(pytest.mark.critical)
            item.add_marker(pytest.mark.extended)
        elif "critical" in item.keywords:
            item.add_marker(pytest.mark.extended)

    for item in items:
        if "phone" in item.keywords:
            item.add_marker(pytest.mark.flaky(reruns=1, reruns_delay=5))
            item.add_marker(pytest.mark.timeout(300))

    api = [t for t in items if "api" in t.keywords]
    phone = [t for t in items if "phone" in t.keywords]
    other = [t for t in items if "api" not in t.keywords and "phone" not in t.keywords]
    items[:] = api + other + phone


def _debug_summary(adb, router) -> str:
    lines = ["\n\n=== DEBUG ON FAILURE ==="]
    if router:
        try:
            state = router.get_nds_state()
            lines.append(f"ndsctl state: {state}")
        except Exception:
            pass
        try:
            portal_log = router.get_portal_log()
            if portal_log:
                lines.append(f"portal log (last 500 chars): {portal_log[-500:]}")
        except Exception:
            pass
        try:
            session = router.get_session()
            lines.append(f"session: {str(session)[:300]}")
        except Exception:
            pass
    if adb and hasattr(adb, "ui_xml"):
        try:
            xml = adb.ui_xml()
            texts = re.findall(r'text="([^"]{3,})"', xml)
            if texts:
                lines.append(f"phone UI text: {texts[:15]}")
            sm = re.search(r'data-sm="([^"]*)"', xml)
            if sm:
                lines.append(f"portal state machine: {sm.group(1)}")
        except Exception:
            pass
    return "\n".join(lines)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        results_dir = getattr(item, "_results_dir", None)
        if not results_dir:
            return
        adb = item.funcargs.get("adb")
        router = item.funcargs.get("router")
        raw = os.path.join(results_dir, "raw")
        os.makedirs(raw, exist_ok=True)

        publish_mode = _is_publish_mode(item.config)
        can_publish = "publish_screenshot" in item.keywords

        if adb:
            try:
                img_path = os.path.join(raw, f"{item.name}-failed.png")
                adb.screenshot(img_path)

                should_embed = not publish_mode or can_publish
                if should_embed and os.path.isfile(img_path) and html_extras is not None:
                    with open(img_path, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode()
                    if b64:
                        extra = getattr(report, "extra", [])
                        extra.append(html_extras.image(b64, f"{item.name}-failed"))
                        report.extra = extra
            except Exception:
                pass
            if hasattr(adb, "ui_xml"):
                try:
                    xml_path = os.path.join(raw, f"{item.name}-ui.xml")
                    xml = adb.ui_xml()
                    with open(xml_path, "w") as f:
                        f.write(xml)
                    if html_extras is not None:
                        texts = re.findall(r'text="([^"]{3,})"', xml)
                        sm = re.search(r'data-sm="([^"]*)"', xml)
                        summary = f"Phone UI texts: {texts[:15]}"
                        if sm:
                            summary += f"\nPortal state: {sm.group(1)}"
                        extra = getattr(report, "extra", [])
                        extra.append(html_extras.text(summary, name="phone-ui"))
                        report.extra = extra
                except Exception:
                    pass
        if router:
            try:
                router.collect_logs(results_dir, adb=adb)
            except Exception:
                pass
            report.longrepr = str(report.longrepr) + _debug_summary(adb, router)
