"""Autonomous cloud lab worker — runs on the GCP outer VM."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import (
    ALPHA_WAN_MAC,
    BETA_WAN_IP,
    BETA_WAN_MAC,
    CDK_MINT_DIR,
    CDK_MINT_PORT,
    CDK_MINT_URL,
    CDK_VERSION,
    CLOUD_ARCH,
    DEBIAN_IP,
    DEBIAN_MAC,
    LOCAL_MINT_HOST,
    NUTSHELL_V1_MINT_PORT,
    NUTSHELL_V1_MINT_URL,
    NUTSHELL_V1_MINT_LAN,
    NUTSHELL_V2_MINT_PORT,
    NUTSHELL_V2_MINT_URL,
    V1_TESTNUT_NUTSHELL_LAN,
    V2_TESTNUT_CDK_LAN,
    V2_TESTNUT_NUTSHELL_LAN,
    OPENWRT_IP,
    RESULTS_ROOT,
    SELLER_OPENWRT_IP,
    SELLER_OPENWRT_MAC,
    SUITE_REPO_URL,
    TEST_DIR,
    UPSTREAM_BRIDGE,
    UPSTREAM_TAP_ALPHA,
    UPSTREAM_TAP_BETA,
    VIRT_LAB_PASSWORD,
    VIRT_LAB_WORKDIR,
    WORKER_LOG,
)

log = logging.getLogger("tollgate.cloud_worker")

_REDACT_PATTERNS = [
    # GitHub tokens
    r"(gho_|ghp_|github_pat_)[A-Za-z0-9_]+",
    r"(GH_TOKEN=|gh-token=)[^\s,]+",
    # Passwords
    r"(password|passwd|sshpass\s+-p)\s+[^\s,]+",
    # SSH private keys (match BEGIN line and onward)
    r"(-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE KEY-----).*",
    # GCP service account keys (PEM blocks)
    r"(-----BEGIN\s+\w+\s+(?:PRIVATE\s+)?KEY-----).*",
    # GCP OAuth2 access tokens
    r"(ya29\.)[A-Za-z0-9_-]+",
    # Bearer tokens in Authorization headers
    r"(Bearer\s+)[A-Za-z0-9._-]+",
    # Generic API keys (api.key=..., apikey=..., api_key=...)
    r"(api[\._]?key\s*[:=]\s*)[A-Za-z0-9_-]{20,}",
    # Generic base64-encoded secrets labelled as token/secret/key/credential/password
    r"((?:token|secret|key|credential|password|passwd)\s*[:=]\s*)[A-Za-z0-9+/=]{40,}",
    # Passwords in config/env format (password=value, shorter than 40 chars)
    r"((?:password|passwd)\s*[:=]\s*)[^\s,]{4,}",
]


def _redact(text: str) -> str:
    import re as _re

    for pat in _REDACT_PATTERNS:
        text = _re.sub(pat, r"\1***", text)
    return text

METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/attributes"


@dataclass
class WorkerConfig:
    run_id: str
    sut_branch: str
    sut_commit: str
    sut_pr: str
    artifact_run_id: str
    artifact_repo: str
    suite_ref: str
    backend: str
    reseller_scenarios: bool
    two_router: bool
    secondary_router_host: str
    secondary_router_port: str
    keep_vm_on_failure: bool
    publish: bool
    project: str
    zone: str
    vm_name: str
    gh_token: str
    mint: str
    portal: str
    quick: bool
    hwsim_enabled: bool
    vwifi_enabled: bool
    smoke: bool
    wifi_plane: str


def _metadata_get(key: str) -> str:
    req = urllib.request.Request(
        f"{METADATA_URL}/{key}",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode().strip()


def _metadata_get_optional(key: str, default: str = "") -> str:
    try:
        return _metadata_get(key)
    except Exception:
        return default


def load_config_from_metadata() -> WorkerConfig:
    cfg = WorkerConfig(
        run_id=_metadata_get("tollgate-run-id"),
        sut_branch=_metadata_get("tollgate-sut-branch"),
        sut_commit=_metadata_get("tollgate-sut-commit"),
        sut_pr=_metadata_get("tollgate-pr"),
        artifact_run_id=_metadata_get("tollgate-artifact-run-id"),
        artifact_repo=_metadata_get("tollgate-artifact-repo"),
        suite_ref=_metadata_get("tollgate-suite-ref"),
        backend=_metadata_get("tollgate-backend"),
        reseller_scenarios=_metadata_get_optional("tollgate-reseller-scenarios").lower() in ("true", "1", "yes"),
        two_router=_metadata_get_optional("tollgate-two-router").lower() in ("true", "1", "yes"),
        secondary_router_host=_metadata_get_optional("tollgate-secondary-router-host"),
        secondary_router_port=_metadata_get_optional("tollgate-secondary-router-port"),
        keep_vm_on_failure=_metadata_get_optional("tollgate-keep-vm-on-failure").lower() not in ("false", "0", "no"),
        publish=_metadata_get("tollgate-publish").lower() in ("true", "1", "yes"),
        project=_metadata_get("tollgate-project"),
        zone=_metadata_get("tollgate-zone"),
        vm_name=_metadata_get("tollgate-vm-name"),
        gh_token=_metadata_get("tollgate-gh-token"),
        mint=_metadata_get_optional("tollgate-mint", "auto"),
        portal=_metadata_get_optional("tollgate-portal", "builtin"),
        hwsim_enabled=_metadata_get_optional("tollgate-hwsim").lower() in ("true", "1", "yes"),
        vwifi_enabled=_metadata_get_optional("tollgate-vwifi").lower() in ("true", "1", "yes"),
        quick=_metadata_get_optional("tollgate-quick").lower() in ("true", "1", "yes"),
        smoke=_metadata_get_optional("tollgate-smoke").lower() in ("true", "1", "yes"),
        wifi_plane=_metadata_get_optional("tollgate-wifi-plane", "tap"),
    )
    log.info(
        "Config: run=%s branch=%s repo=%s backend=%s pr=%s publish=%s keep_on_fail=%s mint=%s portal=%s hwsim=%s vwifi=%s quick=%s smoke=%s wifi_plane=%s",
        cfg.run_id, cfg.sut_branch, cfg.artifact_repo, cfg.backend,
        cfg.sut_pr or "(none)", cfg.publish, cfg.keep_vm_on_failure, cfg.mint, cfg.portal, cfg.hwsim_enabled, cfg.vwifi_enabled, cfg.quick, cfg.smoke, cfg.wifi_plane,
    )
    log.info(
        "Artifact: run_id=%s suite_ref=%s reseller=%s secondary=%s",
        cfg.artifact_run_id, cfg.suite_ref[:7], cfg.reseller_scenarios, cfg.secondary_router_host or "(none)",
    )
    return cfg


def _run(cmd: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    redacted = _redact(cmd[:300])
    log.debug("run: %s", redacted)
    t0 = time.monotonic()
    r = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    elapsed = time.monotonic() - t0
    if r.returncode != 0:
        err = _redact((r.stderr or r.stdout or "").strip()[-500:])
        log.info("cmd failed (%.1fs, rc=%d): %s | stderr: %s", elapsed, r.returncode, redacted[:120], err[:300])
        if check:
            raise RuntimeError(f"Command failed ({r.returncode}): {cmd[:120]}\n{err}")
    else:
        log.debug("cmd ok (%.1fs): %s", elapsed, redacted[:120])
    return r



def _virt_lab_workdir() -> Path:
    return Path(os.path.expandvars(VIRT_LAB_WORKDIR))


def _launch_qemu(
    *,
    name: str,
    memory_mb: int,
    cpus: int,
    disk_name: str,
    tap_name: str,
    mac: str,
    wan_tap: str | None = None,
    wan_mac: str | None = None,
    vsock_cid: int | None = None,
) -> subprocess.Popen[str]:
    workdir = _virt_lab_workdir()
    run_dir = workdir / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    serial_sock = run_dir / f"{name}.serial.sock"
    monitor_sock = run_dir / f"{name}.monitor.sock"
    pidfile = run_dir / f"{name}.pid"
    qemu_log = Path(f"/tmp/{name}-qemu.log")
    for path in (serial_sock, monitor_sock, pidfile):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    cmd = [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-m",
        str(memory_mb),
        "-smp",
        str(cpus),
        "-display",
        "none",
        "-serial",
        f"unix:{serial_sock},server=on,wait=off",
        "-monitor",
        f"unix:{monitor_sock},server=on,wait=off",
        "-drive",
        f"file={workdir / 'overlays' / disk_name},format=qcow2,if=virtio",
        "-netdev",
        f"tap,id=net0,ifname={tap_name},script=no,downscript=no",
        "-device",
        f"virtio-net-pci,netdev=net0,mac={mac}",
        "-pidfile",
        str(pidfile),
    ]
    if vsock_cid is not None:
        cmd += [
            "-device", f"vhost-vsock-pci,id=vsock0,guest-cid={vsock_cid}",
        ]
    if wan_tap:
        cmd += [
            "-netdev", f"tap,id=net1,ifname={wan_tap},script=no,downscript=no",
            "-device", f"virtio-net-pci,netdev=net1,mac={wan_mac}",
        ]
    log.info("Launching %s QEMU: disk=%s tap=%s mac=%s", name, disk_name, tap_name, mac)
    with qemu_log.open("w") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            cwd=workdir,
        )
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name} QEMU exited early with rc={proc.returncode}; see {qemu_log}")
        if serial_sock.exists():
            return proc
        time.sleep(0.5)
    raise RuntimeError(f"{name} QEMU did not create serial socket at {serial_sock}")


def _recv_serial(conn: socket.socket, timeout: float = 2.0) -> str:
    conn.settimeout(timeout)
    chunks: list[bytes] = []
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except TimeoutError:
        pass
    return b"".join(chunks).decode("utf-8", errors="replace")


def _serial_send_wait(conn: socket.socket, command: str, wait: float = 2.0) -> str:
    conn.sendall((command + "\n").encode())
    time.sleep(wait)
    return _recv_serial(conn, timeout=2.0)


def _provision_openwrt_serial(name: str, ip: str, timeout: int = 90) -> None:
    serial_sock = _virt_lab_workdir() / "run" / f"{name}.serial.sock"
    deadline = time.time() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        while True:
            try:
                conn.connect(str(serial_sock))
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if time.time() >= deadline:
                    raise RuntimeError(f"{name} serial socket was not ready at {serial_sock}")
                time.sleep(1)

        log.info("Provisioning %s OpenWrt over serial for %s", name, ip)
        conn.sendall(b"\n")
        booted = False
        while time.time() < deadline:
            conn.sendall(b"\n")
            data = _recv_serial(conn, timeout=2.0)
            if "Please press Enter" in data or "root@OpenWrt" in data or ":/#" in data or "OpenWrt" in data:
                booted = True
                break
            time.sleep(1)
        if not booted:
            raise RuntimeError(f"{name} OpenWrt did not reach serial boot prompt")

        _serial_send_wait(conn, "", wait=2)
        password = shlex.quote(VIRT_LAB_PASSWORD)
        commands = [
            f"printf '%s\\n%s\\n' {password} {password} | passwd root",
            "uci set dropbear.@dropbear[0].PasswordAuth='on'",
            "uci commit dropbear",
            "/etc/init.d/dropbear restart",
            "uci add firewall rule",
            "uci set firewall.@rule[-1].name='Allow-SSH-WAN'",
            "uci set firewall.@rule[-1].src='wan'",
            "uci set firewall.@rule[-1].dest_port='22'",
            "uci set firewall.@rule[-1].proto='tcp'",
            "uci set firewall.@rule[-1].target='ACCEPT'",
            "uci commit firewall",
            "fw4 restart",
            f"uci set network.lan.ipaddr='{ip}'",
            "uci set network.lan.netmask='255.255.255.0'",
            "uci set network.lan.gateway='10.99.99.2'",
            "uci set network.lan.dns='8.8.8.8'",
            "uci commit network",
            "/etc/init.d/network restart",
        ]
        for command in commands:
            _serial_send_wait(conn, command, wait=2)
    time.sleep(8)


def _inner_ssh(host: str, remote_cmd: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    cmd = (
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{host} {shlex.quote(remote_cmd)}"
    )
    return _run(cmd, timeout=timeout, check=False)


def _wait_inner_ssh(host: str, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _inner_ssh(host, "echo OK", timeout=10)
        if r.returncode == 0 and "OK" in r.stdout:
            return True
        time.sleep(3)
    return False


def ensure_suite_checkout(config: WorkerConfig) -> None:
    test_dir = Path(TEST_DIR)
    if test_dir.exists() and (test_dir / ".git").exists():
        log.info("Suite checkout: re-fetching %s at %s", SUITE_REPO_URL, config.suite_ref[:7])
        _run(
            f"cd {TEST_DIR} && git fetch --depth 1 origin {shlex.quote(config.suite_ref)}",
            timeout=120, check=False,
        )
        _run(f"cd {TEST_DIR} && git checkout {shlex.quote(config.suite_ref)}", timeout=60)
    else:
        log.info("Suite checkout: cloning %s at %s", SUITE_REPO_URL, config.suite_ref[:7])
        _run(f"rm -rf {TEST_DIR} && git clone --depth 50 {SUITE_REPO_URL} {TEST_DIR}", timeout=180)
        # Fetch the specific commit directly — it may be on a feature branch
        # not included in the default-branch shallow clone.
        _run(
            f"cd {TEST_DIR} && git fetch --depth 1 origin {shlex.quote(config.suite_ref)}",
            timeout=60, check=False,
        )
        _run(f"cd {TEST_DIR} && git checkout {shlex.quote(config.suite_ref)}", timeout=60)


def write_env_file(config: WorkerConfig) -> None:
    reseller_scenarios = "1" if config.reseller_scenarios else ""
    backend = config.backend
    secondary_host = config.secondary_router_host
    if config.reseller_scenarios and not secondary_host:
        secondary_host = SELLER_OPENWRT_IP
    if config.two_router and not secondary_host:
        secondary_host = SELLER_OPENWRT_IP
    env_content = (
        f"TOLLGATE_LUCI_PASSWORD={VIRT_LAB_PASSWORD}\n"
        f"TOLLGATE_SSH_PASSWORD={VIRT_LAB_PASSWORD}\n"
        f"TOLLGATE_SSH_HOST={OPENWRT_IP}\n"
        f"TOLLGATE_LUCI_URL=http://{OPENWRT_IP}\n"
        f"TOLLGATE_ROUTER_ARCH={CLOUD_ARCH}\n"
        f"TOLLGATE_CLIENT_TYPE=container\n"
        f"TOLLGATE_VIRTUAL_LAB=1\n"
        f"TOLLGATE_LAB_TYPE=gcloud\n"
        f"TOLLGATE_VIRTUAL_GATEWAY={OPENWRT_IP}\n"
        f"TOLLGATE_NDS_PORTAL_PORT=80\n"
        f"TOLLGATE_TEST_MINT_URL={CDK_MINT_URL}\n"
        f"TOLLGATE_CDK_MINT_URL={CDK_MINT_URL}\n"
        f"TOLLGATE_NUTSHELL_V2_MINT_URL={NUTSHELL_V2_MINT_URL}\n"
        f"TOLLGATE_NUTSHELL_V1_MINT_URL={NUTSHELL_V1_MINT_URL}\n"
        f"TOLLGATE_V2_MINT_URL={CDK_MINT_URL}\n"
        f"TOLLGATE_CLIENT_IP={DEBIAN_IP}\n"
        f"TOLLGATE_CLIENT_MAC={DEBIAN_MAC}\n"
        f"TOLLGATE_CONTAINER_HOST={DEBIAN_IP}\n"
        f"TOLLGATE_ROUTER_ID=gcp-cloud\n"
        f"TOLLGATE_ROUTER_MODEL=gcp-n2-standard-2\n"
        f"TOLLGATE_BACKEND={backend}\n"
        f"TOLLGATE_VIEWPORT=desktop\n"
        f"TOLLGATE_DISABLE_ARTIFACT_RERUN=1\n"
        f"TOLLGATE_CASHU_VENV=/opt/cashu-venv\n"
        f"TOLLGATE_RECORD_ALL=1\n"
        f"TOLLGATE_ENABLE_RESELLER_SCENARIOS={reseller_scenarios}\n"
        f"TOLLGATE_SECONDARY_ROUTER_HOST={secondary_host}\n"
        f"TOLLGATE_SECONDARY_ROUTER_PORT={config.secondary_router_port}\n"
        f"TOLLGATE_SECONDARY_ROUTER_PASSWORD={VIRT_LAB_PASSWORD}\n"
        f"TOLLGATE_PORTAL={config.portal}\n"
        f"TOLLGATE_ENABLE_HWSIM={'1' if config.hwsim_enabled else ''}\n"
        f"TOLLGATE_ENABLE_VWIFI={'1' if config.vwifi_enabled else ''}\n"
        f"TOLLGATE_WIFI_PLANE={config.wifi_plane}\n"
        f"GH_TOKEN={os.environ.get('GH_TOKEN', '')}\n"
    )
    Path(TEST_DIR, ".env").write_text(env_content)


def ensure_outer_deps() -> None:
    r = _run(
        "test -d /opt/tollgate-venv && /opt/tollgate-venv/bin/python3 -c "
        "'import pytest, pytest_html, pytest_timeout; print(\"VENV_OK\")' 2>/dev/null",
        timeout=15,
        check=False,
    )
    if "VENV_OK" not in r.stdout:
        log.info("Setting up Python venv on outer VM...")
        _run(
            f"apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv git >/dev/null && "
            f"rm -rf /opt/tollgate-venv && python3 -m venv /opt/tollgate-venv && "
            f"/opt/tollgate-venv/bin/pip install -q -r {TEST_DIR}/requirements.txt",
            timeout=180,
        )

    r = _run("test -x /opt/cashu-venv/bin/cashu && echo CASHU_OK", timeout=10, check=False)
    if "CASHU_OK" not in r.stdout:
        log.info("Setting up cashu CLI venv...")
        r = _run(
            "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv >/dev/null && "
            "rm -rf /opt/cashu-venv && python3 -m venv /opt/cashu-venv && "
            "/opt/cashu-venv/bin/pip install -q --upgrade pip && "
            "/opt/cashu-venv/bin/pip install -q cashu 'marshmallow<4' && "
            "/opt/cashu-venv/bin/python - <<'PY'\n"
            "from pathlib import Path\n"
            "import cashu.core.models\n"
            "models=Path(cashu.core.models.__file__)\n"
            "text=models.read_text()\n"
            "text=text.replace('    active: bool\\n','    active: bool = True\\n')\n"
            "models.write_text(text)\n"
            "PY\n"
            "test -x /opt/cashu-venv/bin/cashu && echo CASHU_OK",
            timeout=240,
            check=False,
        )
        if "CASHU_OK" not in (r.stdout or ""):
            log.warning("Cashu CLI install failed (non-fatal, some tests will skip)")

    ensure_cdk_binary()


def ensure_cdk_binary() -> None:
    binary = f"{CDK_MINT_DIR}/cdk-mintd"
    cli_binary = f"{CDK_MINT_DIR}/cdk-cli"
    r = _run(f"test -x {binary} && echo CDK_BINARY_OK", timeout=10, check=False)
    if "CDK_BINARY_OK" in r.stdout:
        log.info("CDK mintd binary already cached")
    else:
        log.info("Downloading CDK mintd v%s...", CDK_VERSION)
        _run(f"mkdir -p {CDK_MINT_DIR}", timeout=10)
        _run(
            f"wget -q -O {binary} "
            f"https://github.com/cashubtc/cdk/releases/download/v{CDK_VERSION}/cdk-mintd-{CDK_VERSION}-x86_64",
            timeout=120,
        )
        _run(f"chmod +x {binary}", timeout=10)
        r = _run(f"{binary} --version 2>&1 || {binary} --help 2>&1 | head -1", timeout=10, check=False)
        log.info("CDK binary verified: %s", (r.stdout or "").strip()[:80])

    r = _run(f"test -x {cli_binary} && echo CDK_CLI_OK", timeout=10, check=False)
    if "CDK_CLI_OK" in r.stdout:
        log.info("CDK CLI binary already cached")
    else:
        log.info("Downloading CDK CLI v%s...", CDK_VERSION)
        _run(f"mkdir -p {CDK_MINT_DIR}", timeout=10)
        _run(
            f"wget -q -O {cli_binary} "
            f"https://github.com/cashubtc/cdk/releases/download/v{CDK_VERSION}/cdk-cli-{CDK_VERSION}-x86_64",
            timeout=120,
        )
        _run(f"chmod +x {cli_binary}", timeout=10)
        _run(f"ln -sf {cli_binary} /usr/local/bin/cdk-cli 2>/dev/null || true", timeout=10)
        r = _run(f"{cli_binary} --version 2>&1 || {cli_binary} --help 2>&1 | head -1", timeout=10, check=False)
        log.info("CDK CLI binary verified: %s", (r.stdout or "").strip()[:80])


def start_local_mints(config: WorkerConfig) -> dict[str, subprocess.Popen[str]]:
    mints: dict[str, subprocess.Popen[str]] = {}

    # cashu v0.20+ uses "fixed-window-elastic-expiry" strategy which was
    # removed in limits>=5.0. Pin to 3.14.1 to keep Nutshell mints working.
    _run(
        "/opt/cashu-venv/bin/pip install -q 'limits==3.14.1' 2>/dev/null",
        timeout=60,
        check=False,
    )

    # --- CDK V2 Mint (port 8383) ---
    cdk_config = f"""\
[info]
url = "http://{LOCAL_MINT_HOST}:{CDK_MINT_PORT}/"
listen_host = "{LOCAL_MINT_HOST}"
listen_port = {CDK_MINT_PORT}
mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

[database]
engine = "sqlite"

[ln]
ln_backend = "fakewallet"

[fake_wallet]
supported_units = ["sat"]
fee_percent = 0
reserve_fee_min = 0
min_delay_time = 0
max_delay_time = 0
"""
    _run(f"mkdir -p {CDK_MINT_DIR}", timeout=10)
    Path(f"{CDK_MINT_DIR}/config.toml").write_text(cdk_config)

    cdk_log = Path("/tmp/cdk-mintd.log")
    cdk_proc = subprocess.Popen(
        [f"{CDK_MINT_DIR}/cdk-mintd", "-c", f"{CDK_MINT_DIR}/config.toml"],
        cwd=CDK_MINT_DIR,
        stdout=cdk_log.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    mints["cdk-v2"] = cdk_proc
    log.info("Started CDK V2 mint (pid=%d, port=%d)", cdk_proc.pid, CDK_MINT_PORT)

    # --- Nutshell V2 Mint (port 8384) ---
    _run("rm -rf /tmp/nutshell-v2-mint-data && mkdir -p /tmp/nutshell-v2-mint-data", timeout=10)
    ns_v2_log = Path("/tmp/nutshell-v2-mint.log")
    ns_v2_env = {
        **os.environ,
        "CASHU_DIR": "/tmp/nutshell-v2-cashu",
        "MINT_DATABASE": "/tmp/nutshell-v2-mint-data",
        "MINT_BACKEND_BOLT11_SAT": "FakeWallet",
        "MINT_PRIVATE_KEY": "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
        "FAKEWALLET_DELAY_OUTGOING_PAYMENT": "0",
        "FAKEWALLET_DELAY_INCOMING_PAYMENT": "0",
        "MINT_RATE_LIMIT": "False",
    }
    ns_v2_proc = subprocess.Popen(
        ["/opt/cashu-venv/bin/python", "-m", "cashu.mint", "--port", str(NUTSHELL_V2_MINT_PORT), "--host", LOCAL_MINT_HOST],
        stdout=ns_v2_log.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        env=ns_v2_env,
    )
    mints["nutshell-v2"] = ns_v2_proc
    log.info("Started Nutshell V2 mint (pid=%d, port=%d)", ns_v2_proc.pid, NUTSHELL_V2_MINT_PORT)

    # --- Nutshell V1 Mint (port 8385) ---
    _run("rm -rf /tmp/nutshell-v1-mint-data && mkdir -p /tmp/nutshell-v1-mint-data", timeout=10)
    ns_v1_log = Path("/tmp/nutshell-v1-mint.log")
    ns_v1_env = {
        **os.environ,
        "CASHU_DIR": "/tmp/nutshell-v1-cashu",
        "VERSION": "0.19.0",
        "MINT_DATABASE": "/tmp/nutshell-v1-mint-data",
        "MINT_AUTH_DATABASE": "/tmp/nutshell-v1-mint-data",
        "MINT_BACKEND_BOLT11_SAT": "FakeWallet",
        "MINT_PRIVATE_KEY": "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about about",
        "FAKEWALLET_DELAY_OUTGOING_PAYMENT": "0",
        "FAKEWALLET_DELAY_INCOMING_PAYMENT": "0",
        "MINT_RATE_LIMIT": "False",
    }
    ns_v1_proc = subprocess.Popen(
        ["/opt/cashu-venv/bin/python", "-m", "cashu.mint", "--port", str(NUTSHELL_V1_MINT_PORT), "--host", LOCAL_MINT_HOST],
        stdout=ns_v1_log.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        env=ns_v1_env,
    )
    mints["nutshell-v1"] = ns_v1_proc
    log.info("Started Nutshell V1 mint (pid=%d, port=%d)", ns_v1_proc.pid, NUTSHELL_V1_MINT_PORT)

    # /etc/hosts entries for local mint DNS
    _run(
        "grep -q 'testnut.cdk.lan' /etc/hosts || "
        "echo '10.99.99.2 v1.testnut.nutshell.lan v2.testnut.cdk.lan v2.testnut.nutshell.lan "
        "testnut.cdk.lan testnut.nutshell.lan testnut.v1.nutshell.lan v1.testnut.lan' >> /etc/hosts",
        check=False,
    )

    # Health checks with early-exit detection
    mint_procs = {"cdk-v2": cdk_proc, "nutshell-v2": ns_v2_proc, "nutshell-v1": ns_v1_proc}
    for name, url in [("cdk-v2", CDK_MINT_URL), ("nutshell-v2", NUTSHELL_V2_MINT_URL), ("nutshell-v1", NUTSHELL_V1_MINT_URL)]:
        for attempt in range(15):
            try:
                req = urllib.request.Request(f"{url}/v1/keys")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        log.info("Local mint %s healthy at %s", name, url)
                        break
            except Exception:
                pass
            proc = mint_procs[name]
            poll = proc.poll()
            if poll is not None:
                log.error("Local mint %s exited early (rc=%d)", name, poll)
                log_path = {
                    "cdk-v2": "/tmp/cdk-mintd.log",
                    "nutshell-v2": "/tmp/nutshell-v2-mint.log",
                    "nutshell-v1": "/tmp/nutshell-v1-mint.log",
                }[name]
                try:
                    tail = Path(log_path).read_text()[-2000:] if Path(log_path).exists() else "(no log)"
                    log.error("Local mint %s log tail:\n%s", name, tail)
                except Exception:
                    pass
                break
            time.sleep(2)
        else:
            log.warning("Local mint %s not healthy after 30s, continuing anyway", name)

    # Router-side /etc/hosts so the backend can resolve local mint DNS names
    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ControlPath=none root@{OPENWRT_IP} "
        f"'grep -q v1.testnut.lan /etc/hosts || "
        f"echo \"{LOCAL_MINT_HOST} v1.testnut.nutshell.lan v2.testnut.cdk.lan v2.testnut.nutshell.lan "
        f"testnut.cdk.lan testnut.nutshell.lan testnut.v1.nutshell.lan v1.testnut.lan\" >> /etc/hosts; "
        f"killall -HUP dnsmasq 2>/dev/null || true'",
        timeout=15,
        check=False,
    )

    return mints


def start_syslog_capture(results_dir: str) -> subprocess.Popen[str]:
    """Start socat UDP listener to capture syslog from OpenWrt/Debian VMs."""
    if not shutil.which("socat"):
        _run("apt-get install -y -qq socat >/dev/null 2>&1 || true", timeout=30, check=False)
        if not shutil.which("socat"):
            raise FileNotFoundError("socat not available and install failed")
    syslog_dir = Path(results_dir) / "raw" / "syslog"
    syslog_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["socat", "-u", "UDP4-LISTEN:514,bind=0.0.0.0,fork",
         f"OPEN:{syslog_dir}/all.log,append,create"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    log.info("Syslog capture started on UDP 514 → %s", syslog_dir)
    return proc


def configure_openwrt_syslog(openwrt_ip: str) -> None:
    """Configure OpenWrt to forward syslog to host VM via UDP 514."""
    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ControlPath=none root@{openwrt_ip} "
        f"'uci set system.@system[0].log_ip=10.99.99.2 && "
        f"uci set system.@system[0].log_port=514 && "
        f"uci commit system && /etc/init.d/log restart'",
        timeout=15,
        check=False,
    )
    log.info("Configured OpenWrt syslog forwarding on %s → 10.99.99.2:514", openwrt_ip)


def stop_local_mints(mints: dict[str, subprocess.Popen[str]]) -> None:
    for name, proc in mints.items():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
        log.info("Stopped local mint: %s", name)


def wait_for_dpkg_lock(timeout: int = 300) -> None:
    """Wait for unattended-upgrades to release the dpkg lock at boot."""
    for attempt in range(timeout // 5):
        r = _run("fuser /var/lib/dpkg/lock-frontend 2>/dev/null", timeout=5, check=False)
        if r.returncode != 0:
            log.info("dpkg lock is free")
            return
        if attempt == 0:
            log.info("Waiting for unattended-upgrades to release dpkg lock...")
        time.sleep(5)
    log.warning("dpkg lock still held after %ds, proceeding anyway", timeout)


def ensure_github_cli(token: str) -> None:
    os.environ["GH_TOKEN"] = token
    _run("git config --global --add safe.directory '*'", timeout=10, check=False)
    r = _run("command -v gh >/dev/null && gh auth status >/dev/null 2>&1 && echo GH_OK", timeout=15, check=False)
    if "GH_OK" in r.stdout:
        _run("gh auth setup-git", timeout=15)
        _run("git config --global user.email 'test@localhost'", timeout=10, check=False)
        _run("git config --global user.name 'CI'", timeout=10, check=False)
        return
    wait_for_dpkg_lock()
    log.info("Installing GitHub CLI...")
    _run(
        "if ! command -v gh >/dev/null; then "
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wget >/dev/null && "
        "mkdir -p -m 755 /etc/apt/keyrings && "
        "wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg > /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
        "chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
        'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] '
        'https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list && '
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gh >/dev/null; fi",
        timeout=180,
    )
    r = _run("GH_TOKEN=$GH_TOKEN gh auth status >/dev/null 2>&1 && echo GH_OK", timeout=15, check=False)
    if "GH_OK" in r.stdout:
        _run("gh auth setup-git", timeout=15)
        _run("git config --global user.email 'test@localhost'", timeout=10, check=False)
        _run("git config --global user.name 'CI'", timeout=10, check=False)
        return
    last_err = ""
    for attempt in range(1, 4):
        r = _run(
            f"env -u GH_TOKEN -u GITHUB_TOKEN printf '%s\\n' {shlex.quote(token)} | env -u GH_TOKEN -u GITHUB_TOKEN gh auth login --with-token 2>&1",
            timeout=30,
            check=False,
        )
        if r.returncode == 0:
            break
        last_err = (r.stderr or r.stdout or "").strip()[-500:]
        log.warning("gh auth attempt %d failed: %s", attempt, last_err[:200])
        if attempt < 3:
            time.sleep(5 * attempt)
    else:
        raise RuntimeError(f"gh auth failed after 3 attempts: {last_err}")
    r = _run("gh auth status 2>&1 && echo GH_OK", timeout=15, check=False)
    if "GH_OK" not in r.stdout:
        raise RuntimeError("gh auth status check failed on worker VM")
    _run("gh auth setup-git", timeout=15)
    _run("git config --global user.email 'test@localhost'", timeout=10, check=False)
    _run("git config --global user.name 'CI'", timeout=10, check=False)


def reset_openwrt_overlay_only() -> None:
    """Reset OpenWrt disk state; preserve Debian overlay (Playwright cache)."""
    log.info("Resetting OpenWrt overlay only (Debian overlay preserved)")
    _run(
        "killall -9 qemu-system-x86_64 2>/dev/null || true; sleep 1; "
        f"cd {VIRT_LAB_WORKDIR} && "
        "OWRT_BASE=images/openwrt-base.qcow2; "
        "[ -f \"$OWRT_BASE\" ] || OWRT_BASE=../images/openwrt-base.qcow2; "
        "OWRT_BASE=$(readlink -f \"$OWRT_BASE\"); "
        "rm -f overlays/tollgate-poc.qcow2 overlays/tollgate-seller.qcow2 && "
        "qemu-img create -f qcow2 -F qcow2 -b \"$OWRT_BASE\" overlays/tollgate-poc.qcow2 >/dev/null && "
        "qemu-img create -f qcow2 -F qcow2 -b \"$OWRT_BASE\" overlays/tollgate-seller.qcow2 >/dev/null",
        timeout=60,
    )
    r = _run(f"test -f {VIRT_LAB_WORKDIR}/overlays/debian-client.qcow2 && echo DEBIAN_OVERLAY_OK", check=False)
    if "DEBIAN_OVERLAY_OK" in r.stdout:
        log.info("Debian overlay present (cached)")
    else:
        log.warning("Debian overlay missing — creating from base image")
        _run(
            f"cd {VIRT_LAB_WORKDIR} && "
            "DEB_BASE=images/debian-12-nocloud-amd64.qcow2; "
            "[ -f \"$DEB_BASE\" ] || DEB_BASE=../images/debian-12-nocloud-amd64.qcow2; "
            "DEB_BASE=$(readlink -f \"$DEB_BASE\"); "
            "qemu-img create -f qcow2 -F qcow2 -b \"$DEB_BASE\" overlays/debian-client.qcow2 >/dev/null && "
            "qemu-img resize --shrink overlays/debian-client.qcow2 10G >/dev/null 2>&1 || true",
            timeout=60,
        )


def setup_bridge() -> None:
    _run(
        "sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1; "
        "ip link add name tg-poc-br type bridge 2>/dev/null || true; "
        "ip addr add 10.99.99.2/24 dev tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-br up; "
        "ip tuntap add dev tg-poc-tap mode tap user root 2>/dev/null || true; "
        "ip link set tg-poc-tap master tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-tap up; "
        "ip tuntap add dev tg-poc-tap2 mode tap user root 2>/dev/null || true; "
        "ip link set tg-poc-tap2 master tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-tap2 up; "
        "ip tuntap add dev tg-poc-tap3 mode tap user root 2>/dev/null || true; "
        "ip link set tg-poc-tap3 master tg-poc-br 2>/dev/null || true; "
        "ip link set tg-poc-tap3 up; "
        "iptables -t nat -C POSTROUTING -s 10.99.99.0/24 ! -o tg-poc-br -j MASQUERADE 2>/dev/null || "
        "iptables -t nat -A POSTROUTING -s 10.99.99.0/24 ! -o tg-poc-br -j MASQUERADE; "
        f"mkdir -p {VIRT_LAB_WORKDIR}/run; "
        # Two-router upstream bridge
        f"ip link add name {UPSTREAM_BRIDGE} type bridge 2>/dev/null || true; "
        f"ip link set {UPSTREAM_BRIDGE} up; "
        f"ip tuntap add dev {UPSTREAM_TAP_ALPHA} mode tap user root 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_ALPHA} master {UPSTREAM_BRIDGE} 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_ALPHA} up; "
        f"ip tuntap add dev {UPSTREAM_TAP_BETA} mode tap user root 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_BETA} master {UPSTREAM_BRIDGE} 2>/dev/null || true; "
        f"ip link set {UPSTREAM_TAP_BETA} up",
        timeout=20,
    )


_VWIFI_BIN_DIR = Path("/opt/vwifi")


def _ensure_vwifi_binaries() -> Path:
    """Ensure vwifi binaries are available on the host. Returns binary dir."""
    server = _VWIFI_BIN_DIR / "host" / "vwifi-server"
    if server.exists() and os.access(server, os.X_OK):
        log.info("[vwifi] Binaries already present at %s", _VWIFI_BIN_DIR)
        return _VWIFI_BIN_DIR

    log.info("[vwifi] Building vwifi from source...")
    build_script = Path(TEST_DIR) / "scripts" / "build-vwifi.sh"
    if build_script.exists():
        _run(
            f"bash {shlex.quote(str(build_script))} --output-dir {shlex.quote(str(_VWIFI_BIN_DIR))}",
            timeout=300,
        )
    else:
        _run(
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
            "cmake make g++ pkg-config libnl-3-dev libnl-genl-3-dev git >/dev/null 2>&1 || true && "
            "rm -rf /tmp/vwifi-build && "
            "git clone --depth 1 https://github.com/Raizo62/vwifi.git /tmp/vwifi-build && "
            f"mkdir -p {_VWIFI_BIN_DIR}/host {_VWIFI_BIN_DIR}/debian {_VWIFI_BIN_DIR}/openwrt && "
            "cd /tmp/vwifi-build && "
            "mkdir -p build-host && cd build-host && "
            "cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc) && "
            f"cp vwifi-server vwifi-ctrl {_VWIFI_BIN_DIR}/host/ && "
            "cd /tmp/vwifi-build && "
            "rm -rf build-guest && mkdir -p build-guest && cd build-guest && "
            "cmake .. -DCMAKE_BUILD_TYPE=Release && make -j$(nproc) && "
            f"cp vwifi-client vwifi-add-interfaces {_VWIFI_BIN_DIR}/debian/ && "
            f"cp vwifi-client vwifi-add-interfaces {_VWIFI_BIN_DIR}/openwrt/",
            timeout=600,
        )

    if not server.exists():
        raise RuntimeError(f"vwifi-server binary not found at {server}")
    log.info("[vwifi] Binaries ready at %s", _VWIFI_BIN_DIR)
    return _VWIFI_BIN_DIR


def _setup_vwifi_host() -> int | None:
    """Start vwifi-server on the GCP host. Returns server PID or None on failure."""
    log.info("[vwifi] Setting up host-side vwifi-server")

    # Load vhost_vsock kernel module
    _run("modprobe vhost_vsock 2>/dev/null || true", timeout=10)
    _run("chmod a+rw /dev/vhost-vsock 2>/dev/null || true", timeout=5)

    # Ensure binaries exist
    bin_dir = _ensure_vwifi_binaries()
    server_bin = bin_dir / "host" / "vwifi-server"
    if not server_bin.exists():
        log.warning("[vwifi] vwifi-server binary not found, skipping vwifi setup")
        return None

    # Start vwifi-server in background
    server_log = Path("/tmp/vwifi-server.log")
    proc = subprocess.Popen(
        [str(server_bin)],
        stdout=server_log.open("w"),
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    time.sleep(2)

    if proc.poll() is not None:
        log.error("[vwifi] vwifi-server exited early (rc=%d). Log: %s",
                  proc.returncode, server_log.read_text()[-500:] if server_log.exists() else "(no log)")
        return None

    log.info("[vwifi] vwifi-server started (pid=%d)", proc.pid)

    # Verify it's listening (check log for startup message)
    time.sleep(1)
    if server_log.exists():
        log_text = server_log.read_text()[-500:]
        log.info("[vwifi] Server log: %s", log_text[:200])

    return proc.pid


def _setup_vwifi_guests(alpha_ip: str, debian_ip: str, config: WorkerConfig) -> None:
    """Install vwifi-client on OpenWrt and Debian VMs and set up virtual WiFi.

    Called AFTER start_inner_vms() and AFTER _setup_hwsim_wifi().
    Replaces the hwsim-managed interfaces with vwifi-relayed ones.
    """
    bin_dir = _VWIFI_BIN_DIR
    openwrt_client = bin_dir / "openwrt" / "vwifi-client"
    openwrt_add_if = bin_dir / "openwrt" / "vwifi-add-interfaces"
    debian_client = bin_dir / "debian" / "vwifi-client"
    debian_add_if = bin_dir / "debian" / "vwifi-add-interfaces"

    # --- OpenWrt VM (alpha) ---
    log.info("[vwifi] Setting up vwifi-client on OpenWrt alpha (%s)", alpha_ip)

    # Copy binaries to OpenWrt
    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(openwrt_client))} root@{alpha_ip}:/usr/bin/vwifi-client && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(openwrt_add_if))} root@{alpha_ip}:/usr/bin/vwifi-add-interfaces && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{alpha_ip} "
        "'chmod +x /usr/bin/vwifi-client /usr/bin/vwifi-add-interfaces'",
        timeout=30,
    )

    # Remove existing hwsim, load empty hwsim, add vwifi interfaces
    r = _inner_ssh(alpha_ip, """
        # Unload existing hwsim if loaded with radios
        rmmod mac80211_hwsim 2>/dev/null || true
        sleep 1
        # Load empty hwsim (vwifi-add-interfaces will create the PHYs)
        modprobe mac80211_hwsim radios=0 2>&1 || insmod mac80211_hwsim radios=0 2>&1 || true
        sleep 1
        # Add 1 virtual radio via vwifi
        vwifi-add-interfaces 1 2>&1
        sleep 2
        # Start vwifi-client in background (connects to host vwifi-server via vsock)
        vwifi-client &
        sleep 3
        # Check interfaces
        iw dev 2>/dev/null | grep Interface || echo 'NO_INTERFACES'
    """, timeout=30)

    # Find the vwifi-created interface name
    alpha_iface = None
    for line in r.stdout.strip().splitlines():
        if "Interface" in line:
            alpha_iface = line.strip().split()[-1]
            break

    if not alpha_iface:
        log.warning("[vwifi] No vwifi interface found on OpenWrt alpha. iw dev: %s",
                    r.stdout[:300])
        # Non-fatal — tests will skip
        return

    log.info("[vwifi] OpenWrt alpha vwifi interface: %s", alpha_iface)

    # Configure the vwifi interface as AP with SSID TollGate-ALPHA
    _inner_ssh(alpha_ip, f"""
        # Add to br-lan
        brctl addif br-lan {alpha_iface} 2>/dev/null || true
        ip link set {alpha_iface} up 2>/dev/null || true

        # Write a minimal hostapd config for the vwifi interface
        mkdir -p /tmp/vwifi
        cat > /tmp/vwifi/hostapd.conf <<'HOSTAPD'
interface={alpha_iface}
driver=nl80211
ssid=TollGate-ALPHA
hw_mode=g
channel=6
ieee80211n=1
ht_capab=[HT20]
HOSTAPD

        # Start hostapd in background
        hostapd -B /tmp/vwifi/hostapd.conf 2>&1 || true
        sleep 2

        # Update UCI wireless for consistency
        uci set wireless.radio0=wifi-device 2>/dev/null || true
        uci set wireless.radio0.type='mac80211' 2>/dev/null || true
        uci set wireless.radio0.band='2g' 2>/dev/null || true
        uci set wireless.radio0.channel='6' 2>/dev/null || true
        uci set wireless.radio0.htmode='HT20' 2>/dev/null || true
        uci set wireless.radio0.disabled='0' 2>/dev/null || true
        uci set wireless.default_radio0=wifi-iface 2>/dev/null || true
        uci set wireless.default_radio0.device='radio0' 2>/dev/null || true
        uci set wireless.default_radio0.mode='ap' 2>/dev/null || true
        uci set wireless.default_radio0.ssid='TollGate-ALPHA' 2>/dev/null || true
        uci set wireless.default_radio0.network='lan' 2>/dev/null || true
        uci set wireless.default_radio0.encryption='none' 2>/dev/null || true
        uci commit wireless 2>/dev/null || true
    """, timeout=30)

    log.info("[vwifi] OpenWrt alpha AP configured with SSID TollGate-ALPHA on %s", alpha_iface)

    # --- Debian VM ---
    log.info("[vwifi] Setting up vwifi-client on Debian (%s)", debian_ip)

    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(debian_client))} root@{debian_ip}:/usr/local/bin/vwifi-client && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} scp -O "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"{shlex.quote(str(debian_add_if))} root@{debian_ip}:/usr/local/bin/vwifi-add-interfaces && "
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{debian_ip} "
        "'chmod +x /usr/local/bin/vwifi-client /usr/local/bin/vwifi-add-interfaces'",
        timeout=30,
    )

    r = _inner_ssh(debian_ip, """
        modprobe mac80211_hwsim radios=0 2>/dev/null || true
        sleep 1
        vwifi-add-interfaces 1 2>&1
        sleep 2
        vwifi-client &
        sleep 3
        iw dev 2>/dev/null | grep Interface || echo 'NO_INTERFACES'
    """, timeout=30)

    debian_iface = None
    for line in r.stdout.strip().splitlines():
        if "Interface" in line:
            debian_iface = line.strip().split()[-1]
            break

    if debian_iface:
        log.info("[vwifi] Debian vwifi interface: %s", debian_iface)

        # Verify scan can see the AP
        r_scan = _inner_ssh(debian_ip, f"iw {debian_iface} scan 2>&1", timeout=15)
        if "TollGate-ALPHA" in r_scan.stdout:
            log.info("[vwifi] Debian scan sees TollGate-ALPHA — cross-VM WiFi relay working!")
        else:
            log.warning("[vwifi] Debian scan did NOT find TollGate-ALPHA. Output: %s",
                        r_scan.stdout[:300])
    else:
        log.warning("[vwifi] No vwifi interface found on Debian VM. iw dev: %s",
                    r.stdout[:300])

    log.info("[vwifi] Guest setup complete")


def start_inner_vms(config: WorkerConfig) -> None:
    setup_bridge()
    reset_openwrt_overlay_only()

    if config.vwifi_enabled:
        log.info("[vwifi] Loading vhost_vsock module for cross-VM WiFi")
        _run("modprobe vhost_vsock 2>/dev/null || true && chmod a+rw /dev/vhost-vsock 2>/dev/null || true", timeout=10)

    if config.two_router:
        log.info("Starting Beta OpenWrt VM (upstream router)...")
        beta_proc = _launch_qemu(
            name="openwrt-beta",
            memory_mb=512,
            cpus=1,
            disk_name="tollgate-seller.qcow2",
            tap_name="tg-poc-tap3",
            mac=SELLER_OPENWRT_MAC,
            wan_tap=UPSTREAM_TAP_BETA,
            wan_mac=BETA_WAN_MAC,
            vsock_cid=11 if config.vwifi_enabled else None,
        )
        if _wait_inner_ssh(SELLER_OPENWRT_IP, timeout=15):
            log.info("Beta OpenWrt base pre-provisioned, skipping serial")
        else:
            _provision_openwrt_serial("openwrt-beta", SELLER_OPENWRT_IP)
        if beta_proc.poll() is not None:
            raise RuntimeError(f"Beta OpenWrt VM exited during provisioning with rc={beta_proc.returncode}")
        if not _wait_inner_ssh(SELLER_OPENWRT_IP):
            raise RuntimeError("Beta OpenWrt VM did not become reachable")

        _configure_beta_upstream(SELLER_OPENWRT_IP)

        config.secondary_router_host = SELLER_OPENWRT_IP
        log.info("Beta OpenWrt VM SSH OK at %s", SELLER_OPENWRT_IP)

    if config.reseller_scenarios and not config.secondary_router_host:
        log.info("Starting managed seller OpenWrt VM for reseller scenarios...")
        seller_proc = _launch_qemu(
            name="openwrt-seller",
            memory_mb=512,
            cpus=1,
            disk_name="tollgate-seller.qcow2",
            tap_name="tg-poc-tap3",
            mac=SELLER_OPENWRT_MAC,
        )
        if _wait_inner_ssh(SELLER_OPENWRT_IP, timeout=15):
            log.info("Seller OpenWrt base pre-provisioned, skipping serial")
        else:
            _provision_openwrt_serial("openwrt-seller", SELLER_OPENWRT_IP)
        if seller_proc.poll() is not None:
            raise RuntimeError(f"Seller OpenWrt VM exited during provisioning with rc={seller_proc.returncode}")
        if not _wait_inner_ssh(SELLER_OPENWRT_IP):
            raise RuntimeError("Seller OpenWrt VM did not become reachable at managed IP")
        config.secondary_router_host = SELLER_OPENWRT_IP
        log.info("Seller OpenWrt VM SSH OK at %s", SELLER_OPENWRT_IP)

    log.info("Starting Alpha OpenWrt VM...")
    reseller_proc = _launch_qemu(
        name="openwrt",
        memory_mb=512,
        cpus=1,
        disk_name="tollgate-poc.qcow2",
        tap_name="tg-poc-tap",
        mac="52:54:00:12:34:56",
        wan_tap=UPSTREAM_TAP_ALPHA if config.two_router else None,
        wan_mac=ALPHA_WAN_MAC if config.two_router else None,
        vsock_cid=10 if config.vwifi_enabled else None,
    )
    if _wait_inner_ssh(OPENWRT_IP, timeout=15):
        log.info("OpenWrt base pre-provisioned, skipping serial")
    else:
        _provision_openwrt_serial("openwrt", OPENWRT_IP)
    if reseller_proc.poll() is not None:
        raise RuntimeError(f"Alpha OpenWrt VM exited during provisioning with rc={reseller_proc.returncode}")
    if not _wait_inner_ssh(OPENWRT_IP):
        raise RuntimeError("OpenWrt VM did not become reachable")

    if config.two_router:
        _configure_alpha_wan(OPENWRT_IP)

    log.info("Alpha OpenWrt VM SSH OK")

    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{OPENWRT_IP} "
        f"\"grep -q {DEBIAN_MAC} /tmp/dhcp.leases 2>/dev/null || "
        f"echo '0 {DEBIAN_MAC} {DEBIAN_IP} debian-client *' >> /tmp/dhcp.leases\"",
        timeout=15,
    )

    log.info("Starting Debian VM (cached overlay)...")
    debian_proc = _launch_qemu(
        name="debian",
        memory_mb=1536,
        cpus=2,
        disk_name="debian-client.qcow2",
        tap_name="tg-poc-tap2",
        mac=DEBIAN_MAC,
        vsock_cid=20 if config.vwifi_enabled else None,
    )
    time.sleep(25)
    if debian_proc.poll() is not None:
        raise RuntimeError(f"Debian VM exited before SSH with rc={debian_proc.returncode}")
    if not _wait_inner_ssh(DEBIAN_IP):
        raise RuntimeError("Debian VM did not become reachable")
    log.info("Debian VM SSH OK")


def _configure_beta_upstream(beta_ip: str) -> None:
    log.info("Configuring Beta as upstream DHCP server + NAT gateway")
    _inner_ssh(beta_ip, """
        uci set network.upstream=interface
        uci set network.upstream.proto='static'
        uci set network.upstream.device='eth1'
        uci set network.upstream.ipaddr='10.99.98.1'
        uci set network.upstream.netmask='255.255.255.0'
        uci commit network

        uci set dhcp.upstream=dhcp
        uci set dhcp.upstream.interface='upstream'
        uci set dhcp.upstream.start='10'
        uci set dhcp.upstream.limit='50'
        uci set dhcp.upstream.leasetime='2m'
        uci commit dhcp

        # Assign upstream to lan zone so DHCP/DNS traffic is accepted
        uci add_list firewall.@zone[0].network='upstream'
        uci commit firewall

        /etc/init.d/network restart
        /etc/init.d/firewall restart
        /etc/init.d/dnsmasq restart

        # NAT masquerading for Alpha's internet access through Beta
        nft add table ip tollgate-nat 2>/dev/null || true
        nft add chain ip tollgate-nat postrouting "{ type nat hook postrouting priority srcnat ; policy accept ; }" 2>/dev/null || true
        nft add rule ip tollgate-nat postrouting ip saddr 10.99.98.0/24 oifname "br-lan" masquerade 2>/dev/null || true
        nft add rule ip filter forward iifname "eth1" accept 2>/dev/null || true
        nft add rule ip filter forward oifname "eth1" ct state established,related accept 2>/dev/null || true
    """, timeout=45)
    time.sleep(8)
    r = _inner_ssh(beta_ip, "pgrep -f dnsmasq >/dev/null && echo DHCP_OK", timeout=10)
    if "DHCP_OK" not in r.stdout:
        log.warning("Beta DHCP server may not be running")
    else:
        log.info("Beta DHCP server confirmed running")


def _configure_alpha_wan(alpha_ip: str) -> None:
    log.info("Configuring Alpha eth1 as WAN (DHCP from Beta)")
    _inner_ssh(alpha_ip, """
        uci set network.wan=interface
        uci set network.wan.proto='dhcp'
        uci set network.wan.device='eth1'
        uci commit network
        /etc/init.d/network restart
    """, timeout=30)
    # Force DHCP renewal — the initial boot attempt may have timed out
    _inner_ssh(alpha_ip, "ifdown wan 2>/dev/null; sleep 2; ifup wan", timeout=15)
    time.sleep(12)
    r = _inner_ssh(alpha_ip, "ip addr show eth1 2>/dev/null | grep 'inet '", timeout=10)
    if "10.99.98" in r.stdout:
        log.info("Alpha WAN got DHCP lease from Beta")
    else:
        log.warning("Alpha may not have received DHCP lease: %s", r.stdout.strip()[-200:])


def _setup_hwsim_wifi(alpha_ip: str, *, vwifi_mode: bool = False) -> None:
    """Provision virtual WiFi interfaces on the OpenWrt VM via mac80211_hwsim.

    When *vwifi_mode* is True, loads hwsim with ``radios=0`` (empty) and skips
    manual interface creation — ``_setup_vwifi_guests()`` handles interface
    creation via vwifi-add-interfaces.  Still configures UCI wireless for
    SSID/metadata consistency.

    When *vwifi_mode* is False (default), creates AP interfaces manually
    (bypasses netifd's mac80211.sh which fails with hwsim due to
    HOSTAPD_START_FAILED).  The manual path:
      modprobe → iw phy … interface add … type __ap → brctl addif → ip link up

    Idempotent — safe to call multiple times.  Non-fatal: logs a warning
    and returns on any failure so the cloud lab continues without WiFi.
    """
    log.info("[hwsim] Setting up virtual WiFi on %s (vwifi=%s)", alpha_ip, vwifi_mode)

    # --- 1. Load mac80211_hwsim module (idempotent) ---
    r = _inner_ssh(alpha_ip, "lsmod | grep mac80211_hwsim", timeout=10)
    if r.returncode == 0 and "mac80211_hwsim" in r.stdout:
        log.info("[hwsim] Module already loaded, skipping modprobe")
    else:
        radios = "0" if vwifi_mode else "2"
        r = _inner_ssh(alpha_ip, f"modprobe mac80211_hwsim radios={radios} 2>&1", timeout=15)
        if r.returncode != 0:
            log.warning("[hwsim] modprobe mac80211_hwsim failed (rc=%d): %s — skipping WiFi setup",
                        r.returncode, (r.stderr or r.stdout or "").strip()[:300])
            return
        r = _inner_ssh(alpha_ip, "lsmod | grep mac80211_hwsim", timeout=10)
        if r.returncode != 0:
            log.warning("[hwsim] Module not in lsmod after modprobe — skipping WiFi setup")
            return
        log.info("[hwsim] Loaded mac80211_hwsim radios=%s", radios)

    # In vwifi mode, skip manual interface creation — vwifi-add-interfaces handles it
    if vwifi_mode:
        log.info("[hwsim] vwifi mode: skipping manual interface creation")
        # Still configure UCI wireless for consistency (iwinfo reads SSID from UCI)
        r = _inner_ssh(alpha_ip, "uci get wireless.radio0.type 2>/dev/null", timeout=10)
        if r.returncode != 0 or "mac80211" not in r.stdout:
            _inner_ssh(alpha_ip, """
                while uci -q delete wireless.@wifi-device[0]; do true; done
                while uci -q delete wireless.@wifi-iface[0]; do true; done

                uci set wireless.radio0=wifi-device
                uci set wireless.radio0.type='mac80211'
                uci set wireless.radio0.band='2g'
                uci set wireless.radio0.channel='6'
                uci set wireless.radio0.htmode='HT20'
                uci set wireless.radio0.disabled='0'

                uci set wireless.default_radio0=wifi-iface
                uci set wireless.default_radio0.device='radio0'
                uci set wireless.default_radio0.mode='ap'
                uci set wireless.default_radio0.ssid='TollGate-ALPHA'
                uci set wireless.default_radio0.network='lan'
                uci set wireless.default_radio0.encryption='none'

                uci commit wireless 2>/dev/null
            """, timeout=30)
            log.info("[hwsim] UCI wireless configured (vwifi mode)")
        log.info("[hwsim] Setup complete (vwifi mode, interface creation deferred to vwifi)")
        return

    # --- 2. Remove stale tmp interfaces left by netifd ---
    _inner_ssh(alpha_ip, "iw dev tmp.radio0 del 2>/dev/null; iw dev tmp.radio1 del 2>/dev/null", timeout=5)

    # --- 3. Create AP interfaces manually (bypasses broken netifd wifi reload) ---
    r = _inner_ssh(alpha_ip, "iw dev 2>/dev/null | grep -c 'phy0-ap0'", timeout=10)
    if r.returncode == 0 and "1" in r.stdout.strip():
        log.info("[hwsim] phy0-ap0 already exists, skipping manual creation")
    else:
        log.info("[hwsim] Creating AP interfaces manually")
        r = _inner_ssh(alpha_ip, """
            iw phy phy0 interface add phy0-ap0 type __ap 2>&1 && \
            iw phy phy1 interface add phy1-ap0 type __ap 2>&1
        """, timeout=15)
        if r.returncode != 0:
            log.warning("[hwsim] Manual interface creation failed: %s", (r.stdout or r.stderr or "").strip()[:300])
            return

    # --- 4. Add to br-lan and bring up ---
    _inner_ssh(alpha_ip, """
        brctl addif br-lan phy0-ap0 2>/dev/null
        brctl addif br-lan phy1-ap0 2>/dev/null
        ip link set phy0-ap0 up 2>/dev/null
        ip link set phy1-ap0 up 2>/dev/null
    """, timeout=10)

    # --- 5. Configure UCI wireless for consistency (iwinfo reads SSID from UCI) ---
    r = _inner_ssh(alpha_ip, "uci get wireless.radio0.type 2>/dev/null", timeout=10)
    if r.returncode != 0 or "mac80211" not in r.stdout:
        _inner_ssh(alpha_ip, """
            while uci -q delete wireless.@wifi-device[0]; do true; done
            while uci -q delete wireless.@wifi-iface[0]; do true; done

            PHY0_PATH=$(readlink -f /sys/class/ieee80211/phy0/device 2>/dev/null)
            PHY1_PATH=$(readlink -f /sys/class/ieee80211/phy1/device 2>/dev/null)

            uci set wireless.radio0=wifi-device
            uci set wireless.radio0.type='mac80211'
            uci set wireless.radio0.path="${PHY0_PATH#*/sys/devices/}"
            uci set wireless.radio0.band='2g'
            uci set wireless.radio0.channel='6'
            uci set wireless.radio0.htmode='HT20'
            uci set wireless.radio0.disabled='0'

            uci set wireless.default_radio0=wifi-iface
            uci set wireless.default_radio0.device='radio0'
            uci set wireless.default_radio0.mode='ap'
            uci set wireless.default_radio0.ssid='TollGate-ALPHA'
            uci set wireless.default_radio0.network='lan'
            uci set wireless.default_radio0.encryption='none'

            uci set wireless.radio1=wifi-device
            uci set wireless.radio1.type='mac80211'
            uci set wireless.radio1.path="${PHY1_PATH#*/sys/devices/}"
            uci set wireless.radio1.band='5g'
            uci set wireless.radio1.channel='36'
            uci set wireless.radio1.htmode='VHT80'
            uci set wireless.radio1.disabled='0'

            uci set wireless.default_radio1=wifi-iface
            uci set wireless.default_radio1.device='radio1'
            uci set wireless.default_radio1.mode='ap'
            uci set wireless.default_radio1.ssid='TollGate-ALPHA'
            uci set wireless.default_radio1.network='lan'
            uci set wireless.default_radio1.encryption='none'

            uci commit wireless 2>/dev/null
        """, timeout=30)
        log.info("[hwsim] UCI wireless configured")

    # --- 6. Verify interfaces ---
    r = _inner_ssh(alpha_ip, "iw dev 2>/dev/null | grep Interface", timeout=10)
    interfaces = [line.strip() for line in r.stdout.strip().splitlines() if "Interface" in line]
    verified = any("ap0" in iface for iface in interfaces)

    if verified:
        log.info("[hwsim] AP interfaces verified: %s", ", ".join(interfaces))
    else:
        log.warning("[hwsim] No AP interfaces detected — WiFi tests may skip. iw dev: %s",
                    r.stdout.strip()[:300])

    log.info("[hwsim] Setup complete (verified=%s)", verified)


def ensure_debian_client_deps() -> bool:
    r = _inner_ssh(DEBIAN_IP, 'python3 -c "import playwright; print(\\"PLAYWRIGHT_OK\\")" 2>/dev/null')
    if "PLAYWRIGHT_OK" in r.stdout:
        log.info("Debian Playwright cache hit")
        return True
    log.info("Installing Debian Playwright deps (one-time)...")
    install = (
        "apt-get -o Acquire::ForceIPv4=true update -qq && "
        "DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::ForceIPv4=true install -y -qq --no-install-recommends "
        "python3-pip libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 libdbus-1-3 "
        "libdrm2 libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 "
        "libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 xvfb fonts-liberation fonts-freefont-ttf >/dev/null && "
        "python3 -m pip install -q --break-system-packages playwright && "
        "python3 -m playwright install chromium >/dev/null && "
        'python3 -c "import playwright; print(\\"PLAYWRIGHT_OK\\")"'
    )
    r = _inner_ssh(DEBIAN_IP, install, timeout=600)
    return "PLAYWRIGHT_OK" in r.stdout


def deploy_tollgate(config: WorkerConfig) -> None:
    repo_arg = repr(config.artifact_repo)
    branch_arg = repr(config.sut_branch)
    run_id_arg = repr(config.artifact_run_id)
    backend_arg = repr(config.backend)
    py = f"""
import logging
import os
import sys

from lib.backend import BackendConfig
from lib.deploy import deploy_branch
from lib.router import Router

os.environ["TOLLGATE_DISABLE_ARTIFACT_RERUN"] = "1"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

backend = BackendConfig({backend_arg})
hosts = [{OPENWRT_IP!r}]
secondary = {config.secondary_router_host!r}
if secondary:
    hosts.append(secondary)

ok = True
for host in hosts:
    router = Router(host=host, phone_ip="", phone_mac="", domain="", backend=backend)
    result = deploy_branch(
        router,
        {branch_arg},
        arch={CLOUD_ARCH!r},
        force=True,
        reboot=False,
        repo={repo_arg},
        backend=backend,
        run_id={run_id_arg},
    )
    print(
        f"host={{host}} version={{result['installed_version']}} "
        f"health={{result['health_code']}} success={{result['success']}}"
    )
    ok = ok and bool(result["success"])

sys.exit(0 if ok else 1)
"""
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"python3 -c {shlex.quote(py)}",
        timeout=300,
    )
    log.info("Deploy complete for %d host(s)", 1 + bool(config.secondary_router_host))


def deploy_portal_overlay(config: WorkerConfig) -> None:
    """Download and install an alternative portal .ipk on the OpenWrt VM."""
    from lib.portal import PortalConfig

    portal = PortalConfig(config.portal)
    if not portal.needs_separate_deploy:
        return

    repo_arg = repr(portal.repo)
    workflow_arg = repr(portal.workflow)
    portal_type_arg = repr(portal.type)
    py = f"""
import logging
import os
import sys

from lib.portal import PortalConfig
from lib.deploy import deploy_portal
from lib.router import Router
from lib.backend import BackendConfig

os.environ["TOLLGATE_DISABLE_ARTIFACT_RERUN"] = "1"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")

backend = BackendConfig({repr(config.backend)})
router = Router(host={OPENWRT_IP!r}, phone_ip='', phone_mac='', domain='', backend=backend)
portal = PortalConfig({portal_type_arg})

result = deploy_portal(router, portal, arch={CLOUD_ARCH!r})
print(f"portal={{portal.type}} success={{result.get('success')}} skipped={{result.get('skipped', False)}}")
sys.exit(0 if result.get('success') or result.get('skipped') else 1)
"""
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"python3 -c {shlex.quote(py)}",
        timeout=300,
    )
    log.info("Portal overlay deploy complete: %s", config.portal)


def wait_for_backend() -> None:
    for attempt in range(30):
        r = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://{OPENWRT_IP}:2121/ || true", timeout=10, check=False)
        code = r.stdout.strip()
        if "200" in code:
            log.info("TollGate backend healthy (attempt %d, http=%s)", attempt + 1, code)
            return
        if attempt % 5 == 0:
            log.info("Waiting for backend... attempt %d, http=%s", attempt + 1, code)
        time.sleep(2)
    raise RuntimeError("TollGate backend did not become healthy after 60s")


def preflight_check(config: WorkerConfig, mint_url: str, results_dir: str) -> dict[str, Any]:
    """Run pre-flight checks before starting the test suite.

    Verifies that every component can actually do its job.  Writes a
    ``preflight.json`` into *results_dir* so the report can show which
    checks passed/failed.

    Returns a dict with ``ok: bool`` and per-check status.
    """
    checks: dict[str, Any] = {}

    # 1. SSH to OpenWrt
    r = _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{OPENWRT_IP} 'echo OPENWRT_OK'",
        timeout=15, check=False,
    )
    checks["ssh_openwrt"] = "OPENWRT_OK" in r.stdout

    # 2. SSH to Debian
    r = _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{DEBIAN_IP} 'echo DEBIAN_OK'",
        timeout=15, check=False,
    )
    checks["ssh_debian"] = "DEBIAN_OK" in r.stdout

    # 3. Backend responds
    r = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://{OPENWRT_IP}:2121/", timeout=10, check=False)
    checks["backend_http"] = "200" in r.stdout

    # 4. Mint health (HTTP /v1/keys)
    try:
        req = urllib.request.Request(f"{mint_url}/v1/keys")
        with urllib.request.urlopen(req, timeout=5) as resp:
            checks["mint_keys"] = resp.status == 200
    except Exception as exc:
        checks["mint_keys"] = False
        checks["mint_keys_error"] = str(exc)

    # 5. Full mint cycle — mint 4 sats and verify token format
    mint_ok = False
    try:
        r = _run(
            f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && "
            f"set -a && source .env && set +a && "
            f"python3 -c \""
            f"from lib.cashu import create_minter; "
            f"m = create_minter(mint_url='{mint_url}', venv_path='/opt/cashu-venv'); "
            f"m.ensure_mint_available(); "
            f"m.warmup(timeout=30); "
            f"token = m.mint(4, timeout=60); "
            f"assert token.startswith(('cashuA', 'cashuB')), f'bad token: {{token[:20]}}'; "
            f"print(f'MINT_CYCLE_OK token_len={{len(token)}}')\"",
            timeout=120, check=False,
        )
        mint_ok = "MINT_CYCLE_OK" in r.stdout
        if not mint_ok:
            checks["mint_cycle_error"] = r.stdout[-300:] + " | " + r.stderr[-300:]
    except Exception as exc:
        checks["mint_cycle_error"] = str(exc)
    checks["mint_cycle"] = mint_ok

    # 6. Backend can reach the mint (router-side check)
    r = _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
        f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-o ConnectTimeout=5 root@{OPENWRT_IP} "
        f"'curl -s -o /dev/null -w \"%{{http_code}}\" {mint_url}/v1/keys 2>/dev/null || echo 000'",
        timeout=15, check=False,
    )
    checks["router_to_mint"] = "200" in r.stdout

    mint_logs: dict[str, str] = {}
    for name, path in [
        ("cdk-v2", "/tmp/cdk-mintd.log"),
        ("nutshell-v2", "/tmp/nutshell-v2-mint.log"),
        ("nutshell-v1", "/tmp/nutshell-v1-mint.log"),
    ]:
        try:
            p = Path(path)
            if p.exists():
                mint_logs[name] = p.read_text()[-2000:]
        except Exception:
            pass
    checks["_mint_logs"] = mint_logs

    critical_keys = ("ssh_openwrt", "ssh_debian", "backend_http", "mint_keys", "mint_cycle", "router_to_mint")
    ok = all(checks.get(k) for k in critical_keys)
    checks["ok"] = ok

    Path(results_dir).mkdir(parents=True, exist_ok=True)
    serializable = {k: v for k, v in checks.items() if k != "_mint_logs"}
    (Path(results_dir) / "preflight.json").write_text(json.dumps(serializable, indent=2))

    if ok:
        log.info("[preflight] All checks passed")
    else:
        failed = [k for k in critical_keys if not checks.get(k)]
        log.error("[preflight] FAILED checks: %s", ", ".join(failed))
        if not checks.get("mint_cycle"):
            log.error("[preflight] Mint cycle error: %s", checks.get("mint_cycle_error", "(none)"))
        if not checks.get("router_to_mint"):
            log.error("[preflight] Router cannot reach mint at %s", mint_url)

    return checks


def _configure_mint(mint_url: str) -> None:
    """Configure the backend to use a specific mint URL and wait for health."""
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"python3 -c \""
        f"from lib.router import Router; "
        f"from lib.backend import BackendConfig; "
        f"import os; "
        f"r = Router(host=os.environ['TOLLGATE_SSH_HOST'], phone_ip='', phone_mac='', domain='', backend=BackendConfig(os.environ.get('TOLLGATE_BACKEND','go'))); "
        f"r.ssh('cp /tmp/config.json.bak /etc/tollgate/config.json 2>/dev/null || true'); "
        f"r.replace_mints(['{mint_url}']); "
        f"\" 2>&1",
        timeout=120,
        check=False,
    )
    wait_for_backend()


def select_test_mint(forced_mint: str = "auto") -> str:
    """Probe the backend with CDK V2 keysets. Return the mint URL to use.

    If forced_mint is not 'auto', skip probing and use the specified mint:
      - 'cdk-v2'       → CDK V2 mint (01-prefix keysets)
      - 'nutshell-v2'  → Nutshell V2 mint (01-prefix keysets)
      - 'nutshell-v1'  → Nutshell V1 mint (00-prefix keysets, for Go/gonuts)

    Strategy: start with CDK (V2). If the backend starts as a full merchant
    (kind 10021 with price_per_step tags) after being configured with CDK V2,
    V2 is supported. If not (crash or degraded mode), fall back to Nutshell V1
    (V1 keysets). If Nutshell V1 isn't running either, fall back to public testnuts.
    """
    MINT_ALIASES = {
        "cdk-v2": (CDK_MINT_URL, V2_TESTNUT_CDK_LAN),
        "nutshell-v2": (NUTSHELL_V2_MINT_URL, V2_TESTNUT_NUTSHELL_LAN),
        "nutshell-v1": (NUTSHELL_V1_MINT_URL, V1_TESTNUT_NUTSHELL_LAN),
    }

    if forced_mint != "auto":
        urls = MINT_ALIASES.get(forced_mint)
        if not urls:
            raise ValueError(f"Unknown mint '{forced_mint}'. Choose from: {', '.join(MINT_ALIASES)}")
        host_url, lan_url = urls
        log.info("Forced mint=%s → %s (LAN: %s)", forced_mint, host_url, lan_url)
        _configure_mint(lan_url)
        return lan_url

    PUBLIC_TESTNUTS = "https://testnut.cashu.exchange"
    cdk_ok = False
    try:
        r = _run(
            f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
            f"python3 -c \""
            f"from lib.router import Router; "
            f"from lib.backend import BackendConfig; "
            f"import os, json, time; "
            f"r = Router(host=os.environ['TOLLGATE_SSH_HOST'], phone_ip='', phone_mac='', domain='', backend=BackendConfig(os.environ.get('TOLLGATE_BACKEND','go'))); "
            f"r.ssh('cat /etc/tollgate/config.json > /tmp/config.json.bak 2>/dev/null || true'); "
            f"r.replace_mints(['{CDK_MINT_URL}']); "
            f"time.sleep(8); "
            f"code = r.api_status('/'); "
            f"body = r.api_body('/') or ''; "
            f"try: data = json.loads(body); "
            f"except: data = {{}}; "
            f"has_pps = any(isinstance(t, list) and len(t) > 0 and t[0] == 'price_per_step' for t in data.get('tags', [])); "
            f"print(f'v2_probe={{code}} full_merchant={{has_pps}}'); "
            f"\" 2>&1",
            timeout=120,
            check=False,
        )
        if "v2_probe=200" in r.stdout and "full_merchant=True" in r.stdout:
            cdk_ok = True
            log.info("Backend supports V2 keysets — using CDK mint (full merchant confirmed)")
        elif "v2_probe=200" in r.stdout:
            log.info("Backend returned 200 with CDK V2 but not full merchant — V2 likely unsupported")
    except Exception as exc:
        log.warning("V2 probe failed: %s", exc)

    if cdk_ok:
        return V2_TESTNUT_CDK_LAN

    nutshell_v1_ok = False
    for _attempt in range(10):
        try:
            req = urllib.request.Request(f"{NUTSHELL_V1_MINT_URL}/v1/keys")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read())
                    keysets = data.get("keysets", [])
                    if keysets:
                        kid = keysets[0].get("id", "")
                        if kid.startswith("00") and len(kid) == 16:
                            nutshell_v1_ok = True
                            log.info("Nutshell V1 mint has V1 keysets (kid=%s)", kid)
                            break
                        log.info("Nutshell V1 mint has V2 keysets (kid=%s), unusable for Go backend", kid[:16])
                        break
        except Exception:
            pass
        time.sleep(3)

    fallback_url = NUTSHELL_V1_MINT_LAN if nutshell_v1_ok else PUBLIC_TESTNUTS
    log.info("Backend does not support V2 keysets — falling back to %s", fallback_url)

    _configure_mint(fallback_url)
    return fallback_url


def run_tests(config: WorkerConfig, results_dir: str) -> int:
    expected_pr = f"--expected-pr={config.sut_pr} " if config.sut_pr else ""
    backend = config.backend
    run_scenarios = config.reseller_scenarios
    quick = config.quick
    smoke = config.smoke
    virtual_wifi_cmd = ""
    if config.wifi_plane == "hwsim-netns":
        virtual_wifi_cmd = (
            f"virtual_wifi_exit=0; "
            f"python3 -m pytest tests/api/test_virtual_wifi_hwsim_netns.py "
            f"-v --tb=short --timeout=180 --backend={backend} "
            f"{expected_pr}--client=container --results {results_dir} "
            f"--junitxml={results_dir}/raw/virtual-wifi/junit.xml "
            f"--html={results_dir}/raw/virtual-wifi/report.html --self-contained-html "
            f">{results_dir}/raw/virtual-wifi/output.log 2>&1; virtual_wifi_exit=$?; "
        )

    if quick:
        log.info("QUICK MODE: running visual happy path only")
        test_cmd = (
            f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
            f"mkdir -p {results_dir}/raw/visual {results_dir}/report && "
            f"python3 -m pytest tests/api/test_visual_happy_path.py::test_visual_happy_path "
            f"-v --tb=short --timeout=300 --backend={backend} "
            f"{expected_pr}--client=container --results {results_dir} "
            f"--junitxml={results_dir}/raw/visual/junit.xml "
            f"--html={results_dir}/raw/visual/report.html --self-contained-html "
            f">{results_dir}/raw/visual/output.log 2>&1; exit $?"
        )
        r = _run(test_cmd, timeout=600, check=False)
        log.info("Quick test stdout (%d bytes): %s", len(r.stdout), _redact(r.stdout[-2000:]))
        return r.returncode

    if smoke:
        log.info("SMOKE MODE: visual + smoke API + hwsim (if enabled)")
        hwsim_cmd = ""
        if config.hwsim_enabled:
            hwsim_cmd = (
                f"hwsim_exit=0; "
                f"python3 -m pytest tests/api/test_mac80211_hwsim.py "
                f"-v --tb=short --timeout=120 --backend={backend} "
                f"{expected_pr}--client=container --results {results_dir} "
                f"--junitxml={results_dir}/raw/hwsim/junit.xml "
                f"--html={results_dir}/raw/hwsim/report.html --self-contained-html "
                f">{results_dir}/raw/hwsim/output.log 2>&1; hwsim_exit=$?; "
            )
        test_cmd = (
            f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
            f"mkdir -p {results_dir}/raw/visual {results_dir}/raw/smoke-api {results_dir}/raw/hwsim {results_dir}/raw/virtual-wifi {results_dir}/report && "
            "visual_exit=0; smoke_exit=0; "
            f"python3 -m pytest tests/api/test_visual_happy_path.py -v --tb=short --timeout=300 --backend={backend} "
            f"{expected_pr}--client=container --results {results_dir} "
            f"--junitxml={results_dir}/raw/visual/junit.xml "
            f"--html={results_dir}/raw/visual/report.html --self-contained-html "
            f">{results_dir}/raw/visual/output.log 2>&1; visual_exit=$?; "
            f"python3 -m pytest tests/api/ -v --tb=short --timeout=120 -m smoke --backend={backend} "
            f"{expected_pr}--client=container --results {results_dir} "
            f"--ignore=tests/api/test_visual_happy_path.py "
            f"--junitxml={results_dir}/raw/smoke-api/junit.xml "
            f"--html={results_dir}/raw/smoke-api/report.html --self-contained-html "
            f">{results_dir}/raw/smoke-api/output.log 2>&1; smoke_exit=$?; "
            f"{hwsim_cmd}"
            f"{virtual_wifi_cmd}"
            "worst_exit=0; "
            "for e in \"$visual_exit\" \"$smoke_exit\" \"${hwsim_exit:-0}\" \"${virtual_wifi_exit:-0}\"; do "
            "  if [ \"$e\" -ne 0 ] && [ \"$e\" -gt \"$worst_exit\" ]; then worst_exit=$e; fi; done; "
            "exit \"$worst_exit\""
        )
        r = _run(test_cmd, timeout=1200, check=False)
        log.info("Smoke test stdout (%d bytes): %s", len(r.stdout), _redact(r.stdout[-2000:]))
        return r.returncode
    # Virtual-lab scenario tests (captive portal browser, mint health, hwsim)
    # Runs alongside the main API suite. Excludes reseller/two-router which
    # have their own dedicated runners.
    vl_scenario_cmd = (
        f"vl_scenario_exit=0; "
        f"python3 -m pytest "
        f"tests/scenarios/test_captive_portal_browser.py "
        f"tests/scenarios/test_mint_health.py "
        f"tests/scenarios/test_boot_hygiene.py "
        f"tests/scenarios/test_upstream_wifi.py "
        f"-v --tb=short --timeout=600 --backend={backend} "
        f"{expected_pr}--client=container --results {results_dir} "
        f"--junitxml={results_dir}/raw/vl-scenarios/junit.xml "
        f"--html={results_dir}/raw/vl-scenarios/report.html --self-contained-html "
        f">{results_dir}/raw/vl-scenarios/output.log 2>&1; vl_scenario_exit=$?; "
    )
    scenario_cmd = ""
    if run_scenarios:
        scenario_cmd = (
            f"scenario_exit=0; "
            f"python3 -m pytest tests/scenarios/test_reseller_mode.py -v --tb=short --backend={backend} "
            f"{expected_pr}--client=container --results {results_dir} "
            f"--junitxml={results_dir}/raw/scenarios/junit.xml "
            f"--html={results_dir}/raw/scenarios/report.html --self-contained-html "
            f">{results_dir}/raw/scenarios/output.log 2>&1; scenario_exit=$?; "
        )
    two_router_cmd = ""
    if config.two_router:
        two_router_cmd = (
            f"two_router_exit=0; "
            f"python3 -m pytest tests/scenarios/test_two_router_cloud.py -v --tb=short --timeout=300 --backend={backend} "
            f"{expected_pr}--client=container --results {results_dir} "
            f"--junitxml={results_dir}/raw/two-router/junit.xml "
            f"--html={results_dir}/raw/two-router/report.html --self-contained-html "
            f">{results_dir}/raw/two-router/output.log 2>&1; two_router_exit=$?; "
        )
    test_cmd = (
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"mkdir -p {results_dir}/raw/api {results_dir}/raw/visual {results_dir}/raw/scenarios "
        f"{results_dir}/raw/vl-scenarios {results_dir}/raw/two-router {results_dir}/raw/virtual-wifi {results_dir}/report && "
        "visual_exit=0; api_exit=0; "
        f"python3 -m pytest tests/api/test_visual_happy_path.py -v --tb=short --timeout=300 --backend={backend} "
        f"{expected_pr}--client=container --results {results_dir} "
        f"--junitxml={results_dir}/raw/visual/junit.xml "
        f"--html={results_dir}/raw/visual/report.html --self-contained-html "
        f">{results_dir}/raw/visual/output.log 2>&1; visual_exit=$?; "
        f"python3 -m pytest tests/api/ -v --tb=short --timeout=300 --backend={backend} "
        f"{expected_pr}--client=container --results {results_dir} "
        f"--ignore=tests/api/test_visual_happy_path.py "
        f"--junitxml={results_dir}/raw/api/junit.xml "
        f"--html={results_dir}/raw/api/report.html --self-contained-html "
        f">{results_dir}/raw/api/output.log 2>&1; api_exit=$?; "
        f"{vl_scenario_cmd}"
        f"{scenario_cmd}"
        f"{two_router_cmd}"
        f"{virtual_wifi_cmd}"
        "worst_exit=0; "
        "for e in \"$visual_exit\" \"$api_exit\" \"${vl_scenario_exit:-0}\" \"${scenario_exit:-0}\" \"${two_router_exit:-0}\" \"${virtual_wifi_exit:-0}\"; do "
        "  if [ \"$e\" -ne 0 ] && [ \"$e\" -gt \"$worst_exit\" ]; then worst_exit=$e; fi; done; "
        "exit \"$worst_exit\""
    )
    r = _run(test_cmd, timeout=3000, check=False)
    log.info("Test stdout (%d bytes): %s", len(r.stdout), _redact(r.stdout[-2000:]))
    return r.returncode


def collect_and_render(config: WorkerConfig, results_dir: str, started_at: str, finished_at: str) -> None:
    commit_arg = f"--sut-commit {config.sut_commit} " if config.sut_commit else ""
    pr_arg = f"--sut-pr {config.sut_pr} " if config.sut_pr else ""
    scope = "quick" if config.quick else ("smoke" if config.smoke else "full")
    pytest_runners = "--pytest visual=raw/visual/junit.xml "
    if config.smoke:
        pytest_runners += "--pytest smoke-api=raw/smoke-api/junit.xml "
        if config.hwsim_enabled:
            pytest_runners += "--pytest hwsim=raw/hwsim/junit.xml "
        if config.wifi_plane == "hwsim-netns":
            pytest_runners += "--pytest virtual-wifi=raw/virtual-wifi/junit.xml "
    elif not config.quick:
        pytest_runners += "--pytest api=raw/api/junit.xml "
        if config.reseller_scenarios:
            pytest_runners += "--pytest scenarios=raw/scenarios/junit.xml "
        if config.two_router:
            pytest_runners += "--pytest two-router=raw/two-router/junit.xml "
        pytest_runners += "--pytest vl-scenarios=raw/vl-scenarios/junit.xml "
        if config.wifi_plane == "hwsim-netns":
            pytest_runners += "--pytest virtual-wifi=raw/virtual-wifi/junit.xml "
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"python3 scripts/collect-results.py --run-dir {results_dir} "
        f"{pytest_runners}"
        f"--run-id {config.run_id} "
        f"--sut-repo {config.artifact_repo} --sut-branch {shlex.quote(config.sut_branch)} "
        f"{commit_arg}{pr_arg}--sut-backend {config.backend} "
        f"--suite-commit {config.suite_ref} --client-type container "
        f"--portal {config.portal} "
        f"--router-id gcp-cloud --router-model gcp-n2-standard-2 --router-arch {CLOUD_ARCH} "
        f"--viewport desktop --test-plan cloud-api --query-router {OPENWRT_IP} --virtual-lab "
        f"--lab-type gcloud --tier api --scope {scope} --profile gcloud-api "
        f"--started-at {started_at} --finished-at {finished_at} --allow-failures",
        timeout=60,
    )
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && "
        f"python3 scripts/render-report.py --run-dir {results_dir}",
        timeout=60,
    )


def _create_minimal_run_json(
    config: WorkerConfig, results_dir: str, started_at: str, finished_at: str, test_exit: int
) -> None:
    results_path = Path(results_dir)
    run_json = results_path / "run.json"
    if run_json.exists():
        return

    passed = failed = skipped = error = 0
    for junit in results_path.rglob("junit.xml"):
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(junit)
            root = tree.getroot()
            passed += int(root.get("tests", 0)) - int(root.get("failures", 0)) - int(root.get("errors", 0)) - int(root.get("skipped", 0))
            failed += int(root.get("failures", 0))
            skipped += int(root.get("skipped", 0))
            error += int(root.get("errors", 0))
        except Exception:
            pass

    commit_short = (config.sut_commit or "")[:7] or "unknown"
    run_data = {
        "schema_version": 1,
        "run_id": config.run_id,
        "status": "failed" if test_exit != 0 else "passed",
        "started_at": started_at,
        "finished_at": finished_at,
        "counts": {"passed": passed, "failed": failed, "skipped": skipped, "error": error},
        "sut": {
            "repo": config.artifact_repo,
            "branch": config.sut_branch,
            "commit": config.sut_commit or "unknown",
            "commit_short": commit_short,
            "pr": config.sut_pr,
            "backend": config.backend,
            "portal": config.portal,
        },
        "lab": {"router_id": "gcp-cloud", "client_type": "container", "lab_type": "gcloud"},
        "note": "minimal run.json created after collect_and_render failure",
    }
    results_path.mkdir(parents=True, exist_ok=True)
    run_json.write_text(json.dumps(run_data, indent=2))
    log.info("Created minimal run.json: %d passed, %d failed, %d skipped", passed, failed, skipped)

    summary_path = results_path / "summary.json"
    if not summary_path.exists():
        summary_data = {"tests": [], "counts": run_data["counts"]}
        summary_path.write_text(json.dumps(summary_data, indent=2))

    report_dir = results_path / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    index_html = report_dir / "index.html"
    if not index_html.exists():
        index_html.write_text(
            f"<html><head><title>Run {config.run_id}</title></head>"
            f"<body><h1>Run {config.run_id}</h1>"
            f"<p>passed={passed} failed={failed} skipped={skipped}</p>"
            f"<p>collect_and_render failed — minimal report</p></body></html>"
        )


def publish_results(config: WorkerConfig, results_dir: str) -> str:
    run_json = Path(results_dir) / "run.json"
    if not run_json.exists():
        log.error("Cannot publish: run.json not found in %s", results_dir)
        return "https://tests.tollgate.me/"

    commit_short = config.sut_commit[:7]
    data = json.loads(run_json.read_text())
    nested = data.get("sut") or {}
    commit_short = nested.get("commit_short") or commit_short
    expected_url = f"https://tests.tollgate.me/reports/{commit_short}/{config.run_id}/report/index.html"
    log.info("Publishing from results_dir=%s → expected_url=%s", results_dir, expected_url)

    try:
        _run(
            f"git config --global user.email 'tollgate-ci@users.noreply.github.com' && "
            f"git config --global user.name 'TollGate CI' && "
            f"git config --global --add safe.directory '*' && "
            f"gh auth setup-git 2>/dev/null || true && "
            f"cd {TEST_DIR} && TOLLGATE_GH_PAGES_CNAME=tests.tollgate.me "
            f"./scripts/publish-report.sh {shlex.quote(results_dir)}",
            timeout=600,
        )
    except RuntimeError as exc:
        log.error("publish-report.sh failed: %s", _redact(str(exc))[:500])
        raise
    except Exception as exc:
        log.error("publish-report.sh unexpected error: %s", _redact(str(exc))[:500])
        raise

    return expected_url


def post_pr_comment(config: WorkerConfig, report_url: str, counts: dict[str, Any]) -> None:
    if not config.sut_pr:
        return
    body = (
        f"## Cloud lab results\n\n"
        f"**Run:** `{config.run_id}`\n\n"
        f"| Passed | Failed | Skipped |\n"
        f"|--------|--------|--------|\n"
        f"| {counts.get('passed', '?')} | {counts.get('failed', '?')} | {counts.get('skipped', '?')} |\n\n"
        f"[View full report]({report_url})\n"
    )
    repo = config.artifact_repo.split("/")[0] + "/tollgate-module-basic-go"
    if "tollgate-rs" in config.artifact_repo or config.backend == "rust":
        repo = config.artifact_repo
    _run(
        f"gh pr comment {shlex.quote(config.sut_pr)} --repo {shlex.quote(repo)} "
        f"--body {shlex.quote(body)}",
        timeout=30,
        check=False,
    )


def stop_inner_vms() -> None:
    _run("killall -9 qemu-system-x86_64 2>/dev/null || true", timeout=15, check=False)


def _start_vm_log_streaming(
    config: WorkerConfig, results_dir: str
) -> list[tuple[threading.Thread, subprocess.Popen[str], Any]]:
    streams: list[tuple[threading.Thread, subprocess.Popen[str], Any]] = []

    streamed_dir = os.path.join(results_dir, "raw", "streamed")
    if os.path.isdir(results_dir):
        os.makedirs(streamed_dir, exist_ok=True)

    def _stream_reader(prefix: str, proc: subprocess.Popen[str], fh: Any) -> None:
        assert proc.stdout is not None
        try:
            for raw_line in proc.stdout:
                redacted = _redact(raw_line.rstrip("\n"))
                log.info("[%s] %s", prefix, redacted)
                if fh is not None:
                    fh.write(redacted + "\n")
                    fh.flush()
        except Exception:
            pass
        finally:
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass

    targets = [
        ("openwrt", OPENWRT_IP, f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
         f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
         f"-o ConnectTimeout=5 root@{OPENWRT_IP} 'logread -f'"),
        ("debian", DEBIAN_IP, f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
         f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
         f"-o ConnectTimeout=5 root@{DEBIAN_IP} "
         "'journalctl -f -u container-test.service 2>/dev/null || tail -f /var/log/syslog 2>/dev/null || echo NO_LOGS'"),
    ]
    if config.two_router or config.reseller_scenarios:
        targets.append(
            ("seller", SELLER_OPENWRT_IP, f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
             f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
             f"-o ConnectTimeout=5 root@{SELLER_OPENWRT_IP} 'logread -f'"),
        )

    for prefix, _ip, ssh_cmd in targets:
        fh: Any = None
        if os.path.isdir(results_dir):
            log_path = os.path.join(streamed_dir, f"{prefix}.log")
            try:
                fh = open(log_path, buffering=1, encoding="utf-8")  # noqa: SIM115
            except OSError:
                fh = None
        proc = subprocess.Popen(
            ["bash", "-c", ssh_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        t = threading.Thread(target=_stream_reader, args=(prefix, proc, fh), daemon=True)
        t.start()
        streams.append((t, proc, fh))

    return streams


def _stop_vm_log_streaming(streams: list[tuple[threading.Thread, subprocess.Popen[str], Any]]) -> None:
    for t, proc, fh in streams:
        try:
            proc.kill()
        except OSError:
            pass
    for t, proc, fh in streams:
        t.join(timeout=5)
    for t, proc, fh in streams:
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass


def delete_self(config: WorkerConfig) -> None:
    _run(
        f"gcloud compute instances delete {shlex.quote(config.vm_name)} "
        f"--project={shlex.quote(config.project)} --zone={shlex.quote(config.zone)} "
        "--delete-disks=all --quiet",
        timeout=120,
        check=False,
    )


def run_worker(config: WorkerConfig) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    results_dir = f"{RESULTS_ROOT}/{config.run_id}"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    test_exit = 1
    wall_t0 = time.monotonic()
    MAX_WALL_SECONDS = 3600
    vm_streams: list[tuple[threading.Thread, subprocess.Popen[str], Any]] = []
    local_mints: dict[str, subprocess.Popen[str]] = {}
    syslog_proc: subprocess.Popen[str] | None = None

    try:
        try:
            log.info("=== Pipeline start ===")

            os.environ["GH_TOKEN"] = config.gh_token
            log.info("[1/10] Suite checkout (ref=%s)", config.suite_ref[:7])
            ensure_suite_checkout(config)

            log.info("[2/10] Outer deps (venv + cashu)")
            ensure_outer_deps()

            log.info("[3/10] GitHub CLI auth (token=***%s)", config.gh_token[-4:] if len(config.gh_token) > 8 else "***")
            ensure_github_cli(config.gh_token)

            if config.vwifi_enabled:
                log.info("[3.5/10] Starting vwifi-server on host for cross-VM WiFi relay")
                try:
                    _setup_vwifi_host()
                except Exception as vwifi_exc:
                    log.warning("[vwifi] Host setup failed (non-fatal, WiFi tests may skip): %s", vwifi_exc)
                    config.vwifi_enabled = False
            else:
                log.info("[vwifi] Skipped (not enabled — use --vwifi to opt in)")

            log.info("[4/10] Inner VMs (OpenWrt + Debian)")
            start_inner_vms(config)

            log.info("[4.5/10] Setup hwsim virtual WiFi on Alpha (enabled=%s vwifi=%s)", config.hwsim_enabled, config.vwifi_enabled)
            if config.hwsim_enabled or config.vwifi_enabled:
                try:
                    _setup_hwsim_wifi(OPENWRT_IP, vwifi_mode=config.vwifi_enabled)
                except Exception as hwsim_exc:
                    log.warning("[hwsim] Setup failed (non-fatal, WiFi tests may skip): %s", hwsim_exc)
            else:
                log.info("[hwsim] Skipped (not enabled — use --hwsim or --vwifi to opt in)")

            if config.vwifi_enabled:
                log.info("[4.55/10] Setting up vwifi guests for cross-VM WiFi relay")
                try:
                    _setup_vwifi_guests(OPENWRT_IP, DEBIAN_IP, config)
                except Exception as vwifi_exc:
                    log.warning("[vwifi] Guest setup failed (non-fatal, WiFi tests may skip): %s", vwifi_exc)

            log.info("[4.6/10] Start syslog capture + configure OpenWrt forwarding")
            try:
                syslog_proc = start_syslog_capture(results_dir)
            except Exception as exc:
                log.warning("Syslog capture start failed (non-fatal): %s", exc)
            try:
                configure_openwrt_syslog(OPENWRT_IP)
                if config.secondary_router_host:
                    configure_openwrt_syslog(config.secondary_router_host)
            except Exception as exc:
                log.warning("OpenWrt syslog config failed (non-fatal): %s", exc)

            log.info("[5/10] Start local mints (CDK + Nutshell)")
            local_mints = start_local_mints(config)

            log.info("[6/10] Write .env + Debian client deps")
            write_env_file(config)
            ensure_debian_client_deps()

            log.info("[7/10] Deploy TollGate (branch=%s, artifact_run=%s)", config.sut_branch, config.artifact_run_id)
            deploy_tollgate(config)

            log.info("[8/10] Wait for backend health")
            wait_for_backend()

            if config.portal != "builtin":
                log.info("[8.1/10] Deploy portal overlay (%s)", config.portal)
                try:
                    deploy_portal_overlay(config)
                except Exception as portal_exc:
                    log.error("Portal overlay failed (non-fatal, tests may skip): %s", _redact(str(portal_exc))[:500])

            log.info("[8.5/11] Select test mint (forced=%s)", config.mint)
            chosen_mint = select_test_mint(forced_mint=config.mint)
            env_path = Path(f"{TEST_DIR}/.env")
            if env_path.exists():
                env_text = env_path.read_text()
                env_text = env_text.replace(f"TOLLGATE_TEST_MINT_URL={CDK_MINT_URL}", f"TOLLGATE_TEST_MINT_URL={chosen_mint}")
                env_path.write_text(env_text)
                log.info("Updated .env TOLLGATE_TEST_MINT_URL=%s", chosen_mint)

            log.info("[9/11] Pre-flight checks")
            preflight = preflight_check(config, chosen_mint, results_dir)
            if not preflight.get("ok"):
                log.error("Pre-flight checks FAILED — aborting test run to save time")
                raise RuntimeError(f"Pre-flight checks failed: {[k for k, v in preflight.items() if v is False]}")

            log.info("[10/11] Run tests (results_dir=%s)", results_dir)
            vm_streams = _start_vm_log_streaming(config, results_dir)
            try:
                test_exit = run_tests(config, results_dir)
            finally:
                _stop_vm_log_streaming(vm_streams)
            log.info("Tests finished with exit=%d (%.1fs elapsed)", test_exit, time.monotonic() - wall_t0)

        except Exception as exc:
            elapsed = time.monotonic() - wall_t0
            import traceback
            log.error("Pipeline failed at step: %s (%.1fs elapsed)\n%s", _redact(str(exc))[:200], elapsed, traceback.format_exc())
            if elapsed >= MAX_WALL_SECONDS:
                log.warning("1h max lifetime exceeded — will force-delete after collect/publish")
            test_exit = 1

        # ── Collect, render, publish (always attempted) ──────────────
        finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        _run(
            f"mkdir -p {results_dir}/raw && "
            f"cp {WORKER_LOG} {results_dir}/raw/worker.log 2>/dev/null || true",
            timeout=10, check=False,
        )

        for name, path in [
            ("cdk-v2", "/tmp/cdk-mintd.log"),
            ("nutshell-v2", "/tmp/nutshell-v2-mint.log"),
            ("nutshell-v1", "/tmp/nutshell-v1-mint.log"),
        ]:
            _run(f"cp {path} {results_dir}/raw/{name}.log 2>/dev/null || true", timeout=5, check=False)

        try:
            log.info("[11/11] Collect + render results")
            collect_and_render(config, results_dir, started_at, finished_at)
        except Exception as collect_exc:
            log.error("collect_and_render failed (non-fatal): %s", _redact(str(collect_exc))[:500])

        run_json = Path(results_dir) / "run.json"
        if not run_json.exists():
            _create_minimal_run_json(config, results_dir, started_at, finished_at, test_exit)

        counts: dict[str, Any] = {}
        if run_json.exists():
            counts = json.loads(run_json.read_text()).get("counts", {})

        report_url = ""
        total_run = sum(counts.get(k, 0) for k in ("passed", "failed", "skipped", "error"))
        if config.publish and run_json.exists():
            try:
                log.info("Publishing results to gh-pages (total_tests=%d)...", total_run)
                report_url = publish_results(config, results_dir)
                log.info("Published: %s", report_url)
                post_pr_comment(config, report_url, counts)
            except Exception as pub_exc:
                log.error("Publish failed (non-fatal): %s", _redact(str(pub_exc))[:500])

        log.info(
            "=== Pipeline complete: passed=%s failed=%s skipped=%s exit=%d (%.1fs) ===",
            counts.get("passed", "?"),
            counts.get("failed", "?"),
            counts.get("skipped", "?"),
            test_exit,
            time.monotonic() - wall_t0,
        )
        return test_exit
    finally:
        elapsed = time.monotonic() - wall_t0
        force_delete = elapsed >= MAX_WALL_SECONDS
        if force_delete:
            log.warning("1h max lifetime reached (%.1fs) — forcing VM deletion regardless of self_delete setting", elapsed)
        if syslog_proc and syslog_proc.poll() is None:
            syslog_proc.kill()
        stop_local_mints(local_mints)
        stop_inner_vms()
        if force_delete:
            log.info("Force-deleting VM (1h lifetime exceeded)")
            delete_self(config)
        elif config.keep_vm_on_failure:
            log.warning("Keeping VM alive for debugging (keep_vm_on_failure=true). "
                        "Kill switch will shut it down at 1h if still running.")
        else:
            log.info("Self-deleting VM %s", config.vm_name)
            delete_self(config)


def main() -> int:
    if "--from-metadata" not in sys.argv:
        print("Usage: python -m lib.cloud_lab.worker --from-metadata", file=sys.stderr)
        return 2
    config = load_config_from_metadata()
    return run_worker(config)


if __name__ == "__main__":
    raise SystemExit(main())
