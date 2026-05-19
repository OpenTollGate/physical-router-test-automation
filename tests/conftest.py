import base64
import json
import os
import re
import socket
import subprocess
import time
import logging

import pytest

try:
    from pytest_html import extras as html_extras  # pyright: ignore[reportMissingImports]
except ImportError:
    html_extras = None

from lib.router import Router
from lib.router_lock import RouterLock
from lib.cashu import CashuMint, MintUnavailableError
from lib.clients.adb import ADBDevice
from lib.clients.wifi import WiFi
from lib.clients.desktop import MacWiFiClient, MacAdapter, LinuxWiFiClient, LinuxAdapter
from lib.clients.container import ContainerClient
from lib.constants import DEFAULT_STEP_SIZE_MS
from lib.backend import BackendConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("tollgate.conftest")

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..")
MAX_EMBED_SIZE = 20 * 1024 * 1024


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


# Map inventory fields to environment variables they override (only if not already set).
_INVENTORY_ENV_MAP = {
    "sshHost": "TOLLGATE_SSH_HOST",
    "sshUser": "TOLLGATE_SSH_USER",
    "luciUrl": "TOLLGATE_LUCI_URL",
    "arch": "TOLLGATE_ROUTER_ARCH",
    "wifiInterface": "TOLLGATE_WIFI_INTERFACE",
    "tollgateSsidPrefix": "TOLLGATE_SSID_PREFIX",
    "jumpHost": "TOLLGATE_SSH_JUMP_HOST",
    "sshPort": "TOLLGATE_SSH_PORT",
}


def _load_router_inventory():
    if os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        return
    router_id = os.environ.get("TOLLGATE_ROUTER_ID")
    if not router_id:
        return
    inventory_path = os.environ.get(
        "TOLLGATE_ROUTER_INVENTORY",
        os.path.join(SCRIPT_DIR, "config", "routers.json"),
    )
    if not os.path.isfile(inventory_path):
        log.warning("Router inventory not found: %s", inventory_path)
        return
    with open(inventory_path) as f:
        inventory = json.load(f)
    routers = inventory.get("routers", {})
    if router_id not in routers:
        log.warning("Router ID '%s' not in inventory (available: %s)",
                     router_id, list(routers.keys()))
        return
    entry = routers[router_id]
    for field, env_var in _INVENTORY_ENV_MAP.items():
        value = entry.get(field)
        if value:
            os.environ[env_var] = value
    log.info("Loaded router inventory: %s (%s)", router_id, entry.get("model", "unknown"))


_load_router_inventory()


def _results_dir():
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(SCRIPT_DIR, "results")
    return os.path.join(base, f"test-{ts}")


def _client_mode(request):
    return request.config.getoption("--client")


def _is_publish_mode(config):
    return config.getoption("--publish", default=False)


def _is_container_client(config):
    return config.getoption("--client", default="adb") == "container"


@pytest.hookimpl(optionalhook=True)
def pytest_metadata(metadata):
    """Add virtual lab metadata to pytest-html report."""
    if os.environ.get("TOLLGATE_SSH_JUMP_HOST"):
        metadata["Router"] = "QEMU x86_64 (virtual lab)"
        metadata["Client"] = "Debian 12 QEMU VM"
        metadata["Jump Host"] = os.environ["TOLLGATE_SSH_JUMP_HOST"]


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
                     choices=["adb", "mac", "linux", "container"],
                     help="WiFi client mode: adb (Android phone), mac (macOS), linux (NetworkManager/nmcli), or container (Docker via SSH)")
    parser.addoption("--publish", action="store_true",
                     help="Publish mode: only include screenshots from @pytest.mark.publish_screenshot tests in report")
    parser.addoption("--quick-phone", action="store_true",
                     help="Quick phone mode: skip WiFi reconnect between tests (much faster)")
    parser.addoption("--tollgate-branch", default=None,
                     help="Deploy this branch from CI before tests (e.g. 94-mint-health-rebase-clean)")
    parser.addoption("--tollgate-run-id", default=None,
                     help="Specific CI run ID (implies --tollgate-branch)")
    parser.addoption("--tollgate-force", action="store_true",
                     help="Force redeploy even if router is already healthy")
    parser.addoption("--tollgate-factory-reset", action="store_true",
                     help="Remove TollGate before deploying (clean slate)")
    parser.addoption("--tollgate-arch", default=None,
                     help="Router architecture (default: TOLLGATE_ROUTER_ARCH or aarch64_cortex-a53)")
    parser.addoption("--tollgate-reboot", action="store_true",
                     help="Reboot router after deploy and wait for it to come back")
    parser.addoption("--expected-pr", default=None, type=int,
                     help="PR number being tested. Tests marked @pytest.mark.pr(N) where N != expected_pr are expected to fail/skip.")
    parser.addoption("--backend", default=None, choices=["go", "rust"],
                     help="TollGate backend type: 'go' (Go v1) or 'rust' (Rust v1). Default: TOLLGATE_BACKEND env or 'go'")
    parser.addoption("--lock-phase", default=None,
                     help="Auto-acquire router lock with this phase description. "
                          "Prevents concurrent sessions on the same router.")


@pytest.fixture(scope="session")
def results_dir(request):
    custom = request.config.getoption("--results")
    rd = custom or _results_dir()
    os.makedirs(os.path.join(rd, "raw"), exist_ok=True)
    os.makedirs(os.path.join(rd, "report"), exist_ok=True)
    return rd


@pytest.fixture(scope="session")
def backend(request):
    opt = request.config.getoption("--backend")
    return BackendConfig(backend_type=opt)


@pytest.fixture(scope="session")
def router(request, backend):
    host = os.environ.get("TOLLGATE_SSH_HOST") or os.environ.get("ROUTER_IP")
    identity_file = os.environ.get("TOLLGATE_SSH_KEY", "")
    jump_host = os.environ.get("TOLLGATE_SSH_JUMP_HOST", "")

    # Virtual lab uses password auth (sshpass) through jump host.
    # SSH key auth fails without agent forwarding (-A), so clear identity_file.
    if os.environ.get("TOLLGATE_VIRTUAL_LAB") and jump_host:
        identity_file = ""
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
    elif client == "container":
        phone_ip = phone_ip or "10.99.99.100"
        phone_mac = phone_mac or "02:00:00:00:00:01"
        log.info(f"--client=container: using container IP {phone_ip}, MAC {phone_mac}")
    else:
        phone_ip = phone_ip or ""
        phone_mac = phone_mac or ""
    domain = os.environ.get("TOLLGATE_DOMAIN", "")

    assert host, "TOLLGATE_SSH_HOST or ROUTER_IP not set in .env"

    ssh_port = os.environ.get("TOLLGATE_SSH_PORT", "")

    return Router(
        host=host,
        phone_ip=phone_ip,
        phone_mac=phone_mac,
        domain=domain,
        identity_file=identity_file or None,
        jump_host=jump_host or None,
        port=int(ssh_port) if ssh_port else None,
        backend=backend,
    )


@pytest.fixture(scope="session")
def secondary_router(backend):
    host = os.environ.get("TOLLGATE_SECONDARY_ROUTER_HOST", "")
    if not host:
        yield None
        return

    password = os.environ.get(
        "TOLLGATE_SECONDARY_ROUTER_PASSWORD",
        os.environ.get("TOLLGATE_SSH_PASSWORD", os.environ.get("TOLLGATE_LUCI_PASSWORD", "")),
    )
    port = os.environ.get("TOLLGATE_SECONDARY_ROUTER_PORT", "")
    original_password = os.environ.get("TOLLGATE_SSH_PASSWORD")
    if password:
        os.environ["TOLLGATE_SSH_PASSWORD"] = password
    try:
        secondary = Router(
            host=host,
            phone_ip=os.environ.get("TOLLGATE_SECONDARY_CLIENT_IP", ""),
            phone_mac=os.environ.get("TOLLGATE_SECONDARY_CLIENT_MAC", ""),
            domain=os.environ.get("TOLLGATE_SECONDARY_DOMAIN", ""),
            identity_file=os.environ.get("TOLLGATE_SECONDARY_ROUTER_SSH_KEY", "") or None,
            jump_host=os.environ.get("TOLLGATE_SECONDARY_ROUTER_JUMP_HOST", "") or None,
            port=int(port) if port else None,
            backend=backend,
        )
    finally:
        if original_password is None:
            os.environ.pop("TOLLGATE_SSH_PASSWORD", None)
        else:
            os.environ["TOLLGATE_SSH_PASSWORD"] = original_password

    yield secondary
    secondary.close()


@pytest.fixture(scope="session", autouse=True)
def deploy_session(request, router, backend):
    from lib import deploy as deploy_lib

    binary = request.config.getoption("--binary")
    restore = request.config.getoption("--restore")
    tg_branch = request.config.getoption("--tollgate-branch")
    tg_run_id = request.config.getoption("--tollgate-run-id")
    tg_force = request.config.getoption("--tollgate-force")
    tg_reset = request.config.getoption("--tollgate-factory-reset")
    tg_arch = request.config.getoption("--tollgate-arch")
    tg_reboot = request.config.getoption("--tollgate-reboot")
    no_deploy = request.config.getoption("--no-deploy")

    # Unit tests don't need router connectivity. Skip deployment when running
    # only tests under tests/unit/ and no explicit deploy source is specified.
    _no_deploy_source = not binary and not tg_branch and not tg_run_id
    _args = getattr(request.config, "args", []) or []
    _unit_only = bool(_args) and all("unit" in str(a).replace(os.sep, "/") for a in _args)
    if no_deploy or (_no_deploy_source and _unit_only):
        log.info("Skipping deployment (unit tests only or --no-deploy)")
        yield
        return

    if binary:
        subprocess.run(
            ["bash", os.path.join(SCRIPT_DIR, "scripts", "deploy.sh"), binary, "--restart"],
            check=False,
        )
    elif tg_branch or tg_run_id:
        if tg_reset:
            log.info("Factory resetting router before deploy")
            deploy_lib.factory_reset(router, reboot=tg_reboot)

        branch = tg_branch or "main"
        result = deploy_lib.deploy_branch(
            router, branch,
            arch=tg_arch,
            run_id=tg_run_id,
            force=tg_force,
            reboot=tg_reboot,
            backend=backend,
        )
        if not result["success"]:
            pytest.exit(
                f"Deploy failed: version={result.get('installed_version')}, "
                f"health={result.get('health_code')}",
                returncode=1,
            )
        log.info("Deployed: version=%s", result.get("installed_version"))

    if not no_deploy:
        code = router.api_status("/")
        if code != 200:
            pytest.exit(f"Backend not reachable at {router.host}:2121 (HTTP {code})", returncode=1)

        router.enable_debug_portal()
        router.ensure_test_mint()
        router.replace_mints()
        for _ in range(20):
            if router.api_status("/") == 200:
                break
            time.sleep(1)
        else:
            pytest.exit(f"Backend not reachable after test mint setup at {router.host}:2121", returncode=1)

        if _is_container_client(request.config) and os.environ.get("TOLLGATE_CLIENT_MAC"):
            client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "10.99.99.100")
            client_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")
            router.ssh(
                f"grep -qi ' {client_mac} {client_ip} ' /tmp/dhcp.leases 2>/dev/null || "
                f"echo '4102444800 {client_mac} {client_ip} gcp-client 01:{client_mac}' >> /tmp/dhcp.leases",
                timeout=10,
            )

    if _is_container_client(request.config) and os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        container_host = os.environ.get("TOLLGATE_CONTAINER_HOST", "")
        client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "10.99.99.100")
        client_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")
        jump_host = os.environ.get("TOLLGATE_SSH_JUMP_HOST", container_host or "")
        if jump_host == client_ip:
            jump_host = ""
        password = os.environ.get("TOLLGATE_SSH_PASSWORD",
                                  os.environ.get("TOLLGATE_LUCI_PASSWORD", "tollgate"))
        request.session._tollgate_adb = ContainerClient(
            host=container_host or None,
            jump_host=jump_host or None,
            client_ip=client_ip,
            client_mac=client_mac or None,
            password=password,
        )
        log.info("Pre-created ContainerClient for auto-screenshots")

    yield

    try:
        if restore:
            print("\n[deploy] Restoring previous binary")
    finally:
        if not no_deploy:
            router.disable_debug_portal()
        router.close()


@pytest.fixture(scope="session")
def adb(request, router):
    client = _client_mode(request)
    if client == "mac":
        mac = MacWiFiClient()
        return MacAdapter(mac, router_domain=router.domain)
    if client == "linux":
        linux = LinuxWiFiClient()
        return LinuxAdapter(linux, router_domain=router.domain)
    if client == "container":
        container_host = os.environ.get("TOLLGATE_CONTAINER_HOST", "")
        client_ip = os.environ.get("TOLLGATE_CLIENT_IP", "10.99.99.100")
        client_mac = os.environ.get("TOLLGATE_CLIENT_MAC", "")
        jump_host = os.environ.get("TOLLGATE_SSH_JUMP_HOST", container_host or "")
        if jump_host == client_ip:
            jump_host = ""
        password = os.environ.get("TOLLGATE_SSH_PASSWORD",
                                  os.environ.get("TOLLGATE_LUCI_PASSWORD", "tollgate"))
        client = ContainerClient(
            host=container_host or None,
            jump_host=jump_host or None,
            client_ip=client_ip,
            client_mac=client_mac or None,
            password=password,
        )
        request.session._tollgate_adb = client
        return client
    serial = os.environ.get("PHONE_SERIAL", "")
    pin = os.environ.get("PHONE_PIN", "")
    return ADBDevice(serial=serial, pin=pin)


@pytest.fixture(scope="session")
def cashu():
    try:
        mint = CashuMint()
        mint.ensure_mint_available(timeout=10)
        return mint
    except MintUnavailableError as exc:
        pytest.skip(f"cashu mint unavailable: {exc}")


@pytest.fixture(scope="session")
def all_routers(backend):
    identity_file = os.environ.get("TOLLGATE_SSH_KEY", "")
    inventory_path = os.environ.get(
        "TOLLGATE_ROUTER_INVENTORY",
        os.path.join(SCRIPT_DIR, "config", "routers.json"),
    )
    if not os.path.isfile(inventory_path):
        return {}
    with open(inventory_path) as f:
        inventory = json.load(f)
    routers = {}
    for router_id, entry in inventory.get("routers", {}).items():
        host = entry.get("sshHost")
        if not host:
            continue
        routers[router_id] = Router(
            host=host,
            phone_ip="",
            phone_mac="",
            domain="",
            identity_file=identity_file or None,
            jump_host=entry.get("jumpHost") or None,
            port=int(entry["sshPort"]) if entry.get("sshPort") else None,
            backend=backend,
        )
    return routers


@pytest.fixture(scope="session")
def wifi(adb, router):
    ssid = os.environ.get("TOLLGATE_SSID", "TollGate")
    return WiFi(adb=adb, router=router, ssid=ssid)


@pytest.fixture(autouse=True)
def attach_results(request, results_dir):
    request.node._results_dir = results_dir


def _get_pay_via_marker(item):
    """Extract pay_via value from @pytest.mark.pay_via(X) marker."""
    for marker in item.iter_markers("pay_via"):
        if marker.args:
            return marker.args[0]
    return "portal"  # default: open portal


@pytest.fixture
def connected_wifi(router, wifi, adb, request):
    quick = request.config.getoption("--quick-phone", default=False)
    pay_via = _get_pay_via_marker(request.node)
    skip_portal = pay_via == "skip"
    router.resolve_phone_client(adb)
    router.reset_state(adb=adb)
    if quick:
        if not wifi.is_connected():
            assert wifi.reconnect(skip_portal=skip_portal), "WiFi reconnect failed — portal did not render"
    else:
        assert wifi.reconnect(skip_portal=skip_portal), "WiFi reconnect failed — portal did not render"
    router.resolve_phone_client(adb)
    yield


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
    container_mode = _is_container_client(request.config)

    def take(name: str):
        raw_path = os.path.join(results_dir, "raw", name)
        adb.screenshot_portal(raw_path, report_dir=report_dir)

        if publish_mode and not can_publish and not container_mode:
            return

        try:
            with open(raw_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            if b64 and html_extras is not None:
                extras_list = getattr(request.node, "_screenshot_extras", [])
                extras_list.append(html_extras.image(b64, name))
                request.node._screenshot_extras = extras_list
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
    backend_type = item.config.getoption("--backend", default=None) or os.environ.get("TOLLGATE_BACKEND", "go")

    if "go_only" in item.keywords and backend_type == "rust":
        pytest.skip("Go-only test (LuCI, CLI socket, or sessions.json)")

    if "rust_only" in item.keywords and backend_type == "go":
        pytest.skip("Rust-only test")

    if (
        "reseller_scenario" in item.keywords
        and os.environ.get("TOLLGATE_ENABLE_RESELLER_SCENARIOS") != "1"
    ):
        pytest.skip("set TOLLGATE_ENABLE_RESELLER_SCENARIOS=1 to run reseller scenarios")

    client_mode = item.config.getoption("--client")
    if client_mode in ("mac", "linux", "container"):
        if "android_only" in item.keywords:
            pytest.skip("Android-only test (requires physical device)")
        if "requires_wifi" in item.keywords:
            pytest.skip("requires WiFi adapter (--client=container has no WiFi)")
        return

    if "phone" in item.keywords:
        serial = os.environ.get("PHONE_SERIAL", "")
        if not serial:
            pytest.skip("PHONE_SERIAL not set, phone tests require ADB device")

    expected_pr = item.config.getoption("--expected-pr")
    if expected_pr:
        pr_num = _get_pr_marker(item)
        if pr_num is not None and pr_num != expected_pr:
            pytest.skip(
                f"PR-specific test for #{pr_num} (testing #{expected_pr})"
            )


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


def _get_pr_marker(item):
    """Extract PR number from @pytest.mark.pr(N) marker."""
    for marker in item.iter_markers("pr"):
        if marker.args:
            return marker.args[0]
    return None


def _pr_label_for_item(item):
    pr_num = _get_pr_marker(item)
    return f" [PR#{pr_num}]" if pr_num else ""


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    yield
    if hasattr(item, "_excinfo") and item._excinfo:
        exc_info = item._excinfo[-1]
        if exc_info and issubclass(exc_info[0], MintUnavailableError):
            pytest.skip(f"cashu mint unavailable: {exc_info[1]}")


def pytest_runtest_logreport(report):
    if report.when == "call" and hasattr(report, "nodeid"):
        report._pr_label = ""


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
        try:
            nds_clients = router.ssh("timeout 5 ndsctl clients 2>/dev/null || true", timeout=10)
            mac = router.phone_mac
            if mac and mac in nds_clients:
                for line in nds_clients.split("\n"):
                    if "state=" in line:
                        lines.append(f"ndsctl client detail: {line.strip()}")
                        break
        except Exception:
            pass
        try:
            ipv6 = router.ssh("ip -6 addr show br-lan scope global 2>/dev/null | wc -l", timeout=5)
            lines.append(f"global IPv6 on br-lan: {ipv6.strip()}")
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


def _auto_portal_screenshot(item, report, results_dir, adb):
    if not _is_container_client(item.config):
        return
    if not os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        return
    if not results_dir or not adb:
        return
    if getattr(item, "_screenshot_extras", None):
        return

    raw = os.path.join(results_dir, "raw")
    os.makedirs(raw, exist_ok=True)

    if report.failed:
        status = "failed"
    elif report.skipped:
        status = "skipped"
    else:
        status = "passed"
    safe_name = re.sub(r'[^\w\-.]', '_', item.name)
    img_path = os.path.join(raw, f"{safe_name}-{status}.png")

    try:
        adb.screenshot(img_path)
    except Exception as exc:
        log.debug("auto portal screenshot capture failed: %s", exc)
        return

    if not os.path.isfile(img_path):
        return

    if html_extras is not None:
        try:
            with open(img_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            if b64:
                extras = list(getattr(report, "extras", []))
                extras.append(html_extras.image(b64, f"{safe_name}-{status}"))
                report.extras = extras
        except Exception as exc:
            log.debug("auto portal screenshot embed failed: %s", exc)


def _auto_portal_video(item, report, results_dir, adb):
    if not _is_container_client(item.config):
        return
    if not os.environ.get("TOLLGATE_VIRTUAL_LAB"):
        return
    if not results_dir or not adb or not hasattr(adb, "record_portal_video"):
        return
    if report.failed and os.environ.get("TOLLGATE_AUTO_VIDEO_ON_FAILURE") != "1":
        return
    if not report.failed and not (report.passed and os.environ.get("TOLLGATE_RECORD_ALL") == "1"):
        return
    if report.passed and "virtual_lab" not in item.keywords and "publish_screenshot" not in item.keywords:
        return

    raw = os.path.join(results_dir, "raw")
    os.makedirs(raw, exist_ok=True)

    status = "failed" if report.failed else "passed"
    safe_name = re.sub(r'[^\w\-.]', '_', item.name)
    video_path = os.path.join(raw, f"{safe_name}-{status}.webm")

    try:
        ok = adb.record_portal_video(video_path)
    except Exception as exc:
        log.debug("auto portal video capture failed: %s", exc)
        return

    if not ok or not os.path.isfile(video_path):
        return

    if html_extras is None:
        return

    try:
        if os.path.getsize(video_path) > MAX_EMBED_SIZE:
            return
        with open(video_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        if b64:
            extras = list(getattr(report, "extras", []))
            extras.append(
                html_extras.video(
                    b64,
                    name=f"{safe_name}-{status}",
                    mime_type="video/webm",
                    extension="webm",
                )
            )
            report.extras = extras
    except Exception as exc:
        log.debug("auto portal video embed failed: %s", exc)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    screenshot_extras = getattr(item, "_screenshot_extras", None)
    if screenshot_extras and report.when == "call" and html_extras is not None:
        extras = list(getattr(report, "extras", []))
        extras.extend(screenshot_extras)
        report.extras = extras

    if report.when == "call":
        results_dir = getattr(item, "_results_dir", None)
        adb = item.funcargs.get("adb")
        router = item.funcargs.get("router")
        safe_name = re.sub(r'[^\w\-.]', '_', item.name)

        if not adb:
            adb = getattr(item.session, "_tollgate_adb", None)

        _auto_portal_screenshot(item, report, results_dir, adb)
        _auto_portal_video(item, report, results_dir, adb)

        if report.failed:
            raw = os.path.join(results_dir, "raw") if results_dir else None
            if raw:
                os.makedirs(raw, exist_ok=True)

            if adb and raw:
                try:
                    img_path = os.path.join(raw, f"{safe_name}-failed-full.png")
                    adb.screenshot(img_path)

                    if os.path.isfile(img_path) and html_extras is not None:
                        with open(img_path, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode()
                        if b64:
                            extras = list(getattr(report, "extras", []))
                            extras.append(html_extras.image(b64, f"{safe_name}-failed-full"))
                            report.extras = extras
                except Exception:
                    pass
                if hasattr(adb, "ui_xml") and raw:
                    try:
                        xml_path = os.path.join(raw, f"{safe_name}-ui.xml")
                        xml = adb.ui_xml()
                        with open(xml_path, "w") as f:
                            f.write(xml)
                        if html_extras is not None:
                            texts = re.findall(r'text="([^"]{3,})"', xml)
                            sm = re.search(r'data-sm="([^"]*)"', xml)
                            summary = f"Phone UI texts: {texts[:15]}"
                            if sm:
                                summary += f"\nPortal state: {sm.group(1)}"
                            extras = list(getattr(report, "extras", []))
                            extras.append(html_extras.text(summary, name="phone-ui"))
                            report.extras = extras
                    except Exception:
                        pass
            if router and "unit" not in str(item.fspath).replace(os.sep, "/"):
                try:
                    router.collect_logs(results_dir, adb=adb, bundle=safe_name)
                except Exception:
                    pass
                try:
                    report.longrepr = str(report.longrepr) + _debug_summary(adb, router)
                except Exception:
                    pass


_session_lock: RouterLock | None = None


def pytest_sessionstart(session):
    global _session_lock
    lock_phase = session.config.getoption("--lock-phase", default=None)
    if not lock_phase:
        return
    from lib.router_lock import RouterLock
    router_id = os.environ.get("TOLLGATE_ROUTER_ID", "default")
    branch = os.environ.get("TOLLGATE_BRANCH", "unknown")
    lock = RouterLock()
    try:
        lock.acquire(router_id=router_id, phase=lock_phase, branch=branch)
    except RuntimeError as exc:
        pytest.exit(f"Cannot acquire router lock: {exc}", returncode=1)
    _session_lock = lock


def pytest_sessionfinish(session, exitstatus):
    global _session_lock
    if _session_lock is not None:
        _session_lock.release()
        _session_lock = None
