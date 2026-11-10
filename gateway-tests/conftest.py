import os
import subprocess
import json
import time
import pytest

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
env_file = os.path.join(SCRIPT_DIR, ".env")
if os.path.isfile(env_file):
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

GATEWAY_HOST = os.environ.get("TOLLGATE_GATEWAY_HOST", "nodns.shop")
GATEWAY_SSH_USER = os.environ.get("TOLLGATE_GATEWAY_SSH_USER", "root")
GATEWAY_SSH_KEY = os.environ.get("TOLLGATE_GATEWAY_SSH_KEY", "")
GATEWAY_RADIUS_SECRET = os.environ.get("TOLLGATE_RADIUS_SECRET", "tollgate")
GATEWAY_HTTP_PORT = os.environ.get("TOLLGATE_HTTP_PORT", "8091")
GATEWAY_ADMIN_TOKEN = os.environ.get("TOLLGATE_ADMIN_TOKEN", "")
GATEWAY_SETTLE_NPUB = os.environ.get("TOLLGATE_OPERATOR_NPUB", "")


@pytest.fixture(scope="session")
def gateway_host():
    return GATEWAY_HOST


@pytest.fixture(scope="session")
def gateway_ssh():
    def _ssh(cmd, timeout=30):
        args = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
        ]
        if GATEWAY_SSH_KEY:
            args += ["-i", GATEWAY_SSH_KEY]
        args += [f"{GATEWAY_SSH_USER}@{GATEWAY_HOST}", cmd]
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r
    return _ssh


@pytest.fixture(scope="session")
def service_state(gateway_ssh):
    """Init-system-agnostic service probe.

    Returns a callable(name) -> (state, detail) where state is one of
    "active", "inactive", "failed", "absent". Handles both systemd hosts
    (the VM/cloud gateways these tests were written against) and OpenWrt
    procd (physical routers: /etc/init.d/<name> status; "running" = active).
    "absent" lets species-specific tests SKIP instead of failing on
    platforms that legitimately never ship the service.
    """
    def _probe(name):
        r = gateway_ssh(
            f'if command -v systemctl >/dev/null 2>&1; then '
            f'systemctl is-active "{name}" 2>/dev/null; '
            f'elif [ -x "/etc/init.d/{name}" ]; then '
            f'/etc/init.d/{name} status 2>/dev/null || true; '
            f'else echo ABSENT; fi', timeout=15)
        out = (r.stdout or "").strip().lower()
        if out == "absent" or (r.returncode == 127 and not out):
            return "absent", (r.stdout or r.stderr).strip()[:120]
        if "active" in out and "inactive" not in out and "activating" not in out:
            return "active", out
        if "running" in out:
            return "active", out
        if "inactive" in out or "stopped" in out or "dead" in out:
            return "inactive", out
        return "failed", (r.stdout or r.stderr).strip()[:120]
    return _probe


@pytest.fixture(scope="session")
def gateway_http():
    import urllib.request
    import urllib.error

    base = f"http://{GATEWAY_HOST}:{GATEWAY_HTTP_PORT}"

    def _request(method, path, data=None, headers=None):
        url = base + path
        h = headers or {}
        if GATEWAY_ADMIN_TOKEN and path == "/metrics":
            h["Authorization"] = f"Bearer {GATEWAY_ADMIN_TOKEN}"
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
        except Exception as e:
            return 0, str(e)

    return _request


def _radtest(username, password, mac="AA-BB-CC-DD-EE-FF", nas_id="", nas_ip=""):
    lines = [f'User-Name = "{username}"']
    if password:
        lines.append(f'User-Password = "{password}"')
    if mac:
        lines.append(f'Calling-Station-Id = "{mac}"')
    if nas_ip:
        lines.append(f'NAS-IP-Address = "{nas_ip}"')
    if nas_id:
        lines.append(f'NAS-Identifier = "{nas_id}"')
    packet = "\n".join(lines)
    cmd = ["radclient", f"{GATEWAY_HOST}:1812", "auth", GATEWAY_RADIUS_SECRET]
    try:
        r = subprocess.run(cmd, input=packet, capture_output=True, text=True, timeout=15)
        return r
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, -1, "", "radclient not found")


@pytest.fixture(scope="session")
def radtest():
    return _radtest


@pytest.fixture
def unique_mac():
    import random
    return (
        f"02:{random.randint(0, 255):02X}:{random.randint(0, 255):02X}:"
        f"{random.randint(0, 255):02X}:{random.randint(0, 255):02X}:"
        f"{random.randint(0, 255):02X}"
    )


@pytest.fixture(scope="session", autouse=True)
def gateway_reachable(gateway_ssh):
    r = gateway_ssh("echo ok", timeout=10)
    if r.returncode != 0:
        pytest.skip(f"gateway server unreachable via SSH: {r.stderr[:200]}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    _saved = {}
    for key in ("router", "adb"):
        if key in item.funcargs:
            _saved[key] = item.funcargs.pop(key)
    outcome = yield
    item.funcargs.update(_saved)
    outcome.get_result()
