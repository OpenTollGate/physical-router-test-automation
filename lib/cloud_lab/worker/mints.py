"""Cloud lab worker — local Cashu mints."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
import urllib.request
from pathlib import Path

from lib.cloud_lab.constants import (
    CDK_MINT_DIR,
    CDK_MINT_PORT,
    CDK_MINT_URL,
    CDK_VERSION,
    LOCAL_MINT_HOST,
    NUTSHELL_V1_MINT_LAN,
    NUTSHELL_V1_MINT_PORT,
    NUTSHELL_V1_MINT_URL,
    NUTSHELL_V2_MINT_PORT,
    NUTSHELL_V2_MINT_URL,
    OPENWRT_IP,
    TEST_DIR,
    V1_TESTNUT_NUTSHELL_LAN,
    V2_TESTNUT_CDK_LAN,
    V2_TESTNUT_NUTSHELL_LAN,
    VIRT_LAB_PASSWORD,
)
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.deploy import wait_for_backend
from lib.cloud_lab.worker.shell import _run, log

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
