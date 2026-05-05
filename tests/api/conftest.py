import os
import re
import time
import logging
import pytest

try:
    from pytest_html import extras as html_extras
except ImportError:
    html_extras = None

from lib.router import Router
from lib.cashu import CashuMint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("tollgate.conftest")

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")


def load_env():
    for candidate in [
        os.path.join(SCRIPT_DIR, ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
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


def pytest_addoption(parser):
    parser.addoption("--client", default="adb",
                     choices=["adb", "mac", "linux"],
                     help="WiFi client mode: adb (Android phone), mac (macOS), or linux (NetworkManager/nmcli)")
    parser.addoption("--results", default=None,
                     help="Custom results directory path")


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
    password = os.environ.get("TOLLGATE_LUCI_PASSWORD") or os.environ.get("ROUTER_PASSWORD")
    domain = os.environ.get("TOLLGATE_DOMAIN", "")

    assert host, "TOLLGATE_SSH_HOST or ROUTER_IP not set in .env"
    assert password, "TOLLGATE_LUCI_PASSWORD or ROUTER_PASSWORD not set in .env"

    return Router(host=host, password=password, phone_ip="",
                  phone_mac="", domain=domain)


@pytest.fixture(scope="session", autouse=True)
def deploy_session(request, router):
    code = router.api_status("/")
    if code != 200:
        pytest.exit(f"Backend not reachable at {router.host}:2121 (HTTP {code})", returncode=1)

    router.ensure_test_mint()

    yield


@pytest.fixture(scope="session")
def cashu():
    return CashuMint()


@pytest.fixture(autouse=True)
def attach_results(request, results_dir):
    request.node._results_dir = results_dir


def pytest_runtest_setup(item):
    client_mode = item.config.getoption("--client", "adb")
    if client_mode in ("mac", "linux"):
        if "android_only" in item.keywords:
            pytest.skip("Android-only test (requires physical device)")
        return

    if "phone" in item.keywords:
        serial = os.environ.get("PHONE_SERIAL", "")
        if not serial:
            pytest.skip("PHONE_SERIAL not set, phone tests require ADB device")


def pytest_collection_modifyitems(items):
    for item in items:
        if "smoke" in item.keywords:
            item.add_marker(pytest.mark.critical)
            item.add_marker(pytest.mark.extended)
        elif "critical" in item.keywords:
            item.add_marker(pytest.mark.extended)

    for item in items:
        if "phone" in item.keywords:
            item.add_marker(pytest.mark.flaky(reruns=1, reruns_delay=5))

    api = [t for t in items if "api" in t.keywords]
    phone = [t for t in items if "phone" in t.keywords]
    other = [t for t in items if "api" not in t.keywords and "phone" not in t.keywords]
    items[:] = api + other + phone


def _debug_summary(router) -> str:
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
    return "\n".join(lines)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        results_dir = getattr(item, "_results_dir", None)
        if not results_dir:
            return
        router = item.funcargs.get("router")
        raw = os.path.join(results_dir, "raw")
        os.makedirs(raw, exist_ok=True)

        if router:
            try:
                router.collect_logs(results_dir)
            except Exception:
                pass
            report.longrepr = str(report.longrepr) + _debug_summary(router)
