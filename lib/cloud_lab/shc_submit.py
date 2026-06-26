"""SHC (Sovereign Hybrid Compute) cloud lab submit.

Orders a VM, bootstraps all dependencies, and runs the worker pipeline.
The worker pipeline is the same as GCP — only the VM creation and bootstrap
differ.

Flow:
    1. Wait for CI artifact (build-package.yml)
    2. Order SHC Dev VPS Standard (2C/8GB)
    3. Wait for provisioning + IP assignment
    4. Inject SSH key
    5. Wait for SSH daemon to accept connections
    6. Upload bootstrap script
    7. Launch bootstrap in background (nohup)
    8. Optionally monitor (--wait): poll log, cancel VM when done

Usage:
    from lib.cloud_lab.shc_submit import submit_run_shc
    info = submit_run_shc(target, publish=True, quick=True)
"""

from __future__ import annotations

import base64
import logging
import os
import shlex
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from lib.cloud_lab.resolve import RunTarget
from lib.deploy import ensure_artifact

log = logging.getLogger(__name__)

SHC_TOOLKIT_PATH = os.environ.get("SHC_TOOLKIT_PATH", str(Path(__file__).resolve().parents[3] / "shc-toolkit"))
if SHC_TOOLKIT_PATH not in sys.path:
    sys.path.insert(0, SHC_TOOLKIT_PATH)

SUITE_REPO = "OpenTollGate/physical-router-test-automation"
SUITE_REPO_URL = f"https://github.com/{SUITE_REPO}.git"
TEST_DIR = "/opt/tollgate-test"
VIRT_LAB_PASSWORD = "tollgate"

# Bootstrap step names for progress display
_STEPS = [
    "Installing system packages",
    "Installing Rust toolchain",
    "Installing nak CLI",
    "Writing nsec",
    "Cloning test suite",
    "Applying suite overlay",
    "Creating Python venv",
    "Creating cashu venv",
    "Downloading CDK mints",
    "Downloading QEMU images",
    "Installing BlossomFS",
    "Installing vwifi",
    "Installing gh CLI",
    "Configuring network bridge",
    "Running worker pipeline",
]


def _suite_ref() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=repo_root, timeout=10,
    )
    return r.stdout.strip() if r.returncode == 0 else "main"


def _gh_token() -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(key, "")
        if val:
            return val
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else ""


def _nsec_hex() -> str:
    for path in [os.environ.get("NSEC_FILE", ""), Path.home() / ".config/prta/nsec"]:
        if path and Path(path).exists():
            return Path(path).read_text().strip()
    return ""


def _working_tree_overlay_b64() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    changes = subprocess.run(
        ["git", "diff", "--name-only"], capture_output=True, text=True, cwd=repo_root,
    ).stdout.strip().split("\n")
    changes = [c for c in changes if c and not c.startswith(".omo/") and not c.startswith(".playwright-mcp/")]
    if not changes:
        return ""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in changes:
            full = repo_root / f
            if full.exists() and full.is_file():
                tar.add(str(full), arcname=f)
    return base64.b64encode(buf.getvalue()).decode()


def _wait_for_ssh(ssh_base: list[str], ssh_target: str, timeout: int = 300) -> None:
    """Retry SSH until the VM accepts connections.

    Key propagation can take 2-5 minutes after apply_ssh_key_live returns.
    """
    print("Waiting for SSH daemon...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            [*ssh_base, ssh_target, "echo OK"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and "OK" in r.stdout:
            print(" ready!")
            return
        print(".", end="", flush=True)
        time.sleep(5)
    print(" TIMEOUT")
    raise TimeoutError(f"SSH not reachable on {ssh_target} after {timeout}s")


def _build_bootstrap_script(
    *,
    bootstrap_env: str,
    overlay_b64: str,
    test_dir: str,
    suite_repo_url: str,
) -> str:
    """Build the bash bootstrap script that runs inside the VM.

    Each step has explicit error checking. A completion marker (/tmp/tollgate-done)
    is created at the end so the host can reliably detect completion.
    """
    overlay_step = (
        f"base64 -d /tmp/overlay.b64 | sudo tar xzf - -C {test_dir}\n"
        f'echo "[6] Applied suite overlay"'
        if overlay_b64
        else 'echo "[6] No overlay to apply"'
    )

    # NOTE: $VAR refs bash variables (runtime), {var} refs Python f-string (build time)
    return f"""#!/bin/bash
set -eo pipefail
exec >> /var/log/tollgate-run.log 2>&1
echo "=== TollGate SHC worker started $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

export {bootstrap_env}
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/root/.cargo/bin"

step() {{
    local n=$1 msg=$2
    echo "[$n/$N_STEPS] $msg"
}}

fail() {{
    local n=$1 msg=$2
    echo "[$n/$N_STEPS] FAILED: $msg"
    echo "BOOTSTRAP_FAILED at step $n" >> /tmp/tollgate-status
    exit 1
}}

N_STEPS=15
echo "BOOTSTRAP_START" >> /tmp/tollgate-status

step 1 "Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq || fail 1 "apt-get update"
sudo apt-get install -y -qq qemu-system-x86 qemu-utils sshpass git curl wget \
  python3-venv python3-pip net-tools iproute2 socat nftables \
  build-essential libssl-dev pkg-config fuse3 libfuse3-dev \
  cmake g++ libnl-3-dev libnl-genl-3-dev jq || fail 1 "apt-get install"
echo "[1/$N_STEPS] done"

step 2 "Installing Rust..."
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sudo sh -s -- -y || fail 2 "rustup"
source /root/.cargo/env
echo "[2/$N_STEPS] done"

step 3 "Installing nak..."
sudo wget -q -O /usr/local/bin/nak https://github.com/rust-nostr/nostr/releases/download/v0.39.0/nak-linux-x86_64 || fail 3 "nak download"
sudo chmod +x /usr/local/bin/nak
echo "[3/$N_STEPS] done"

step 4 "Writing nsec..."
echo -n "$BOT_NSEC_HEX" | sudo tee /root/nsec > /dev/null
sudo chmod 600 /root/nsec
echo "[4/$N_STEPS] done"

step 5 "Cloning test suite..."
sudo rm -rf {test_dir}
sudo git clone --depth 50 {suite_repo_url} {test_dir} || fail 5 "git clone"
cd {test_dir}
sudo git fetch --depth 1 origin "$TOLLGATE_SUITE_REF" 2>/dev/null || true
sudo git checkout "$TOLLGATE_SUITE_REF" || fail 5 "git checkout"
echo "[5/$N_STEPS] done"

step 6 "Applying overlay..."
cat > /tmp/overlay.b64 <<'OVERLAY_EOF'
{overlay_b64}
OVERLAY_EOF
{overlay_step}
echo "[6/$N_STEPS] done"

step 7 "Creating Python venv..."
sudo python3 -m venv /opt/tollgate-venv || fail 7 "venv create"
sudo /opt/tollgate-venv/bin/pip install -q -r {test_dir}/requirements.txt || fail 7 "pip install"
echo "[7/$N_STEPS] done"

step 8 "Creating cashu venv..."
sudo python3 -m venv /opt/cashu-venv || fail 8 "cashu venv"
sudo /opt/cashu-venv/bin/pip install -q --upgrade pip
sudo /opt/cashu-venv/bin/pip install -q cashu 'marshmallow<4' || fail 8 "cashu install"
MODELS=$(/opt/cashu-venv/bin/python3 -c 'import cashu.core.models; print(cashu.core.models.__file__)')
sudo sed -i 's/    active: bool$/    active: bool = True/' "$MODELS"
echo "[8/$N_STEPS] done"

step 9 "Downloading CDK mints..."
CDK_VER=0.16.0
sudo mkdir -p /opt/cdk-mintd
sudo wget -q -O /opt/cdk-mintd/cdk-mintd "https://github.com/cashubtc/cdk/releases/download/v${{CDK_VER}}/cdk-mintd-${{CDK_VER}}-x86_64" || fail 9 "cdk-mintd download"
sudo chmod +x /opt/cdk-mintd/cdk-mintd
sudo wget -q -O /opt/cdk-mintd/cdk-cli "https://github.com/cashubtc/cdk/releases/download/v${{CDK_VER}}/cdk-cli-${{CDK_VER}}-x86_64" || fail 9 "cdk-cli download"
sudo chmod +x /opt/cdk-mintd/cdk-cli
sudo ln -sf /opt/cdk-mintd/cdk-cli /usr/local/bin/cdk-cli
echo "[9/$N_STEPS] done"

step 10 "Downloading QEMU images..."
WORKDIR=/root/tollgate-virtual-lab
sudo mkdir -p "$WORKDIR/images" "$WORKDIR/run" "$WORKDIR/overlays"
cd "$WORKDIR/images"
OPENWRT_VERSION=24.10.1
sudo wget -q "https://downloads.openwrt.org/releases/${{OPENWRT_VERSION}}/targets/x86/64/openwrt-${{OPENWRT_VERSION}}-x86-64-generic-ext4-combined.img.gz" || fail 10 "openwrt download"
sudo gunzip -kf "openwrt-${{OPENWRT_VERSION}}-x86-64-generic-ext4-combined.img.gz"
sudo qemu-img convert -f raw -O qcow2 "openwrt-${{OPENWRT_VERSION}}-x86-64-generic-ext4-combined.img" openwrt-base.qcow2
sudo qemu-img resize openwrt-base.qcow2 2G
sudo wget -q "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-nocloud-amd64.qcow2" || fail 10 "debian download"
echo "[10/$N_STEPS] done"

step 11 "Installing BlossomFS (from cache)..."
fetch_cached() {{
  local key=$1 dest=$2
  local url
  url=$(nak req -k 1063 -l 1 -t "filename=$key" wss://relay.cashu.email 2>/dev/null | python3 -c "import sys,json;[print(next(t[1] for t in json.loads(l)['tags'] if t[0]=='url')) for l in [sys.stdin.readline().strip()] if l]" 2>/dev/null)
  if [ -n "$url" ]; then sudo curl -sfL -o "$dest" "$url" && sudo chmod +x "$dest" && return 0; fi
  return 1
}}
sudo mkdir -p /opt/blossomfs/target/release
if fetch_cached blossomfs-8784100 /opt/blossomfs/target/release/blossomfs; then
  echo "  BlossomFS from cache"
else
  echo "  Compiling BlossomFS from source..."
  sudo git clone --depth 1 https://github.com/Amperstrand/blossomfs /opt/blossomfs || fail 11 "blossomfs clone"
  cd /opt/blossomfs && sudo /root/.cargo/bin/cargo build --release || fail 11 "blossomfs build"
fi
echo "[11/$N_STEPS] done"

step 12 "Installing vwifi (from cache)..."
sudo mkdir -p /opt/vwifi/bin/host /opt/vwifi/bin/debian /opt/vwifi/bin/openwrt
if fetch_cached vwifi-host-server-072cdb8 /opt/vwifi/bin/host/vwifi-server \
   && fetch_cached vwifi-host-ctrl-072cdb8 /opt/vwifi/bin/host/vwifi-ctrl \
   && fetch_cached vwifi-guest-client-072cdb8 /opt/vwifi/bin/debian/vwifi-client; then
  sudo cp /opt/vwifi/bin/debian/vwifi-client /opt/vwifi/bin/openwrt/vwifi-client
  echo "  vwifi from cache"
else
  echo "  Compiling vwifi from source..."
  sudo git clone --depth 1 https://github.com/Raizo62/vwifi.git /tmp/vwifi-build || fail 12 "vwifi clone"
  cd /tmp/vwifi-build && mkdir -p build-host && cd build-host
  sudo cmake .. -DCMAKE_BUILD_TYPE=Release && sudo make -j$(nproc) || fail 12 "vwifi build host"
  sudo cp vwifi-server vwifi-ctrl /opt/vwifi/bin/host/
  cd /tmp/vwifi-build && mkdir -p build-guest && cd build-guest
  sudo cmake .. -DCMAKE_BUILD_TYPE=Release && sudo make -j$(nproc) || fail 12 "vwifi build guest"
  sudo cp vwifi-client vwifi-add-interfaces /opt/vwifi/bin/debian/
  sudo cp vwifi-client vwifi-add-interfaces /opt/vwifi/bin/openwrt/
fi
echo "[12/$N_STEPS] done"

step 13 "Installing gh CLI..."
if ! command -v gh >/dev/null 2>&1; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list
  sudo apt-get update -qq && sudo apt-get install -y -qq gh || fail 13 "gh install"
fi
echo "[13/$N_STEPS] done"

step 14 "Configuring network bridge..."
sudo sysctl -w net.ipv4.ip_forward=1 > /dev/null
sudo ip link add name tg-poc-br type bridge 2>/dev/null || true
sudo ip addr add 10.99.99.2/24 dev tg-poc-br 2>/dev/null || true
sudo ip link set tg-poc-br up
sudo ip tuntap add dev tg-poc-tap mode tap user root 2>/dev/null || true
sudo ip link set tg-poc-tap master tg-poc-br 2>/dev/null || true
sudo ip link set tg-poc-tap up
sudo iptables -t nat -A POSTROUTING -s 10.99.99.0/24 ! -o tg-poc-br -j MASQUERADE 2>/dev/null || true
echo "[14/$N_STEPS] done"

step 15 "Running worker pipeline..."
cd {test_dir}
echo "PIPELINE_START" >> /tmp/tollgate-status
sudo /opt/tollgate-venv/bin/python3 -m lib.cloud_lab.worker --from-env || fail 15 "worker pipeline"
echo "PIPELINE_DONE" >> /tmp/tollgate-status
echo "[15/$N_STEPS] done"

echo "=== Pipeline complete ==="
echo "BOOTSTRAP_DONE" >> /tmp/tollgate-status
touch /tmp/tollgate-done
"""


def submit_run_shc(
    target: RunTarget,
    *,
    publish: bool = False,
    artifact_timeout_s: int = 1800,
    quick: bool = False,
    smoke: bool = False,
    complete: bool = False,
    mint: str = "auto",
    portal: str = "builtin",
    keep_vm_on_failure: bool = False,
    lease_minutes: int = 90,
) -> dict[str, str]:
    """Order an SHC VM, bootstrap it, and run the test worker pipeline.

    Returns dict with run_id, vm_name, service_id, ip, and monitoring info.
    The caller is responsible for cancelling the VM when done (via
    wait_for_shc_run or manual cleanup).
    """

    from shc_toolkit.client import SHCClient

    client = SHCClient()

    # 1. Wait for CI artifact
    print(f"Waiting for CI artifact ({target.repo}@{target.branch})...")
    artifact_run_id = ensure_artifact(
        branch=target.branch, arch="x86_64", repo=target.repo,
        workflow="build-package.yml", commit=target.sut_commit or None,
        timeout_s=artifact_timeout_s,
    )
    print(f"Artifact ready: run {artifact_run_id}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = (target.sut_commit or target.branch)[:7].replace("/", "-")
    run_id = f"{timestamp}-{short}"
    hostname = f"tollgate-{short[:12]}"

    suite_ref = _suite_ref()
    token = _gh_token()
    nsec = _nsec_hex()
    overlay_b64 = _working_tree_overlay_b64()
    if overlay_b64:
        print(f"Including local overlay ({len(overlay_b64)} bytes b64)")

    # 2. Order Standard VM (2C/8GB is enough)
    print(f"Ordering SHC VM '{hostname}' (Standard 2C/8GB)...")
    result = client.submit_order(hostname=hostname, package_id=81, pricing_id=245)
    sids = result.get("service_ids", [])
    if not sids:
        raise RuntimeError(f"SHC order failed: {result}")
    service_id = int(sids[0])
    print(f"  Ordered service #{service_id}")

    # 3. Wait for provisioning + IP
    print("Waiting for provisioning...")
    deadline = time.time() + 600
    vm_ip = ""
    while time.time() < deadline:
        vm = client.get_vm(service_id)
        state = vm.get("provisioning_state", "unknown")
        ips = vm.get("ips", [])
        vm_ip = ips[0]["ip"] if ips else ""
        print(f"  state={state} ip={vm_ip or 'pending'}")
        if state == "ready" and vm_ip:
            break
        if state in ("failed", "error", "cancelled"):
            raise RuntimeError(f"SHC provisioning failed: {state}")
        time.sleep(10)
    else:
        raise TimeoutError(f"SHC VM {service_id} not ready after 600s")

    print(f"VM ready: {hostname} @ {vm_ip}")

    # 4. Inject SSH key
    ssh_key_path = os.environ.get("SHC_SSH_KEY", str(Path.home() / ".ssh/id_rsa.pub"))
    if Path(ssh_key_path).exists():
        print("Injecting SSH key...")
        pubkey = Path(ssh_key_path).read_text().strip()
        client.apply_ssh_key_live(service_id, pubkey)

    ssh_user = os.environ.get("SHC_SSH_USER", "debian")
    ssh_target = f"{ssh_user}@{vm_ip}"
    ssh_base = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]

    # 5. Wait for SSH daemon (key propagation can take 2-5 min)
    try:
        _wait_for_ssh(ssh_base, ssh_target, timeout=300)
    except TimeoutError:
        print(" key auth not ready, trying password fallback...")
        creds = client.get_vm_credentials(service_id)
        vm_password = creds.get("password", "")
        if not vm_password:
            raise
        sshpass_check = subprocess.run(
            ["sshpass", "-p", vm_password, *ssh_base, ssh_target, "echo OK"],
            capture_output=True, text=True, timeout=15,
        )
        if sshpass_check.returncode != 0:
            raise TimeoutError(f"Neither key nor password auth worked on {ssh_target}")
        print("  Password auth works — injecting SSH key manually...")
        pubkey_content = Path(ssh_key_path).read_text().strip()
        subprocess.run(
            ["sshpass", "-p", vm_password, *ssh_base, ssh_target,
             f"mkdir -p ~/.ssh && echo '{pubkey_content}' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"],
            capture_output=True, text=True, timeout=15,
        )
        time.sleep(2)
        _wait_for_ssh(ssh_base, ssh_target, timeout=30)

    # Clear any previous status
    subprocess.run(
        [*ssh_base, ssh_target, "sudo rm -f /tmp/tollgate-status /tmp/tollgate-done /var/log/tollgate-run.log"],
        capture_output=True, text=True, timeout=15,
    )

    # 6. Build bootstrap script
    bootstrap_env = " ".join([
        f"TOLLGATE_RUN_ID={shlex.quote(run_id)}",
        f"TOLLGATE_SUT_BRANCH={shlex.quote(target.branch)}",
        f"TOLLGATE_SUT_COMMIT={shlex.quote(target.sut_commit or '')}",
        f"TOLLGATE_SUT_PR={shlex.quote(target.pr or '')}",
        f"TOLLGATE_ARTIFACT_RUN_ID={shlex.quote(artifact_run_id)}",
        f"TOLLGATE_ARTIFACT_REPO={shlex.quote(target.repo)}",
        f"TOLLGATE_PR_REPO={shlex.quote(target.pr_repo or target.repo)}",
        f"TOLLGATE_SUITE_REF={shlex.quote(suite_ref)}",
        f"TOLLGATE_BACKEND={shlex.quote(target.backend)}",
        f"TOLLGATE_PUBLISH={'true' if publish else 'false'}",
        f"TOLLGATE_QUICK={'true' if quick else 'false'}",
        f"TOLLGATE_SMOKE={'true' if smoke else 'false'}",
        f"TOLLGATE_COMPLETE={'true' if complete else 'false'}",
        f"TOLLGATE_MINT={shlex.quote(mint)}",
        f"TOLLGATE_PORTAL={shlex.quote(portal)}",
        f"TOLLGATE_KEEP_VM_ON_FAILURE={'true' if keep_vm_on_failure else 'false'}",
        f"TOLLGATE_GCP_PROJECT=tollgate-test-lab",
        f"TOLLGATE_GCP_ZONE=shc",
        f"TOLLGATE_VM_NAME={hostname}",
        f"GH_TOKEN={shlex.quote(token)}",
        f"BOT_NSEC_HEX={shlex.quote(nsec)}",
        f"VIRT_LAB_PASSWORD={VIRT_LAB_PASSWORD}",
        f"NSEC_FILE=/root/nsec",
        f"HOME=/root",
    ])

    bootstrap_script = _build_bootstrap_script(
        bootstrap_env=bootstrap_env,
        overlay_b64=overlay_b64,
        test_dir=TEST_DIR,
        suite_repo_url=SUITE_REPO_URL,
    )

    # 7. Upload script
    script_path = "/tmp/tollgate-shc-worker.sh"
    print("Uploading bootstrap script...")
    proc = subprocess.run(
        [*ssh_base, ssh_target, f"cat > {script_path} && chmod +x {script_path}"],
        input=bootstrap_script, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to write bootstrap script: {proc.stderr}")

    # 8. Launch in background
    print("Launching worker pipeline...")
    subprocess.run(
        [*ssh_base, ssh_target, f"nohup sudo bash {script_path} > /dev/null 2>&1 & echo LAUNCHED"],
        capture_output=True, text=True, timeout=30,
    )

    log_hint = f"ssh {ssh_target} 'tail -f /var/log/tollgate-run.log'"

    return {
        "run_id": run_id,
        "vm_name": hostname,
        "service_id": str(service_id),
        "ip": vm_ip,
        "ssh_target": ssh_target,
        "artifact_run_id": artifact_run_id,
        "suite_ref": suite_ref,
        "log_hint": log_hint,
    }


def wait_for_shc_run(
    client,
    service_id: int,
    ssh_target: str,
    ssh_base: list[str],
    *,
    timeout_s: int = 5400,
    keep_vm_on_failure: bool = False,
) -> int:
    """Monitor an SHC cloud lab run until completion.

    Polls the VM every 15s:
    - Checks /tmp/tollgate-done for completion
    - Checks /tmp/tollgate-status for failure markers
    - Tails the log for progress

    Cancels the VM when done (unless keep_vm_on_failure and failed).
    Returns 0 on success, 1 on failure.
    """
    print(f"\nMonitoring SHC run (service #{service_id})...")
    print(f"  Log: ssh {ssh_target} 'tail -f /var/log/tollgate-run.log'")
    print()

    deadline = time.time() + timeout_s
    last_status_line = ""

    while time.time() < deadline:
        # Check VM still exists
        try:
            vm = client.get_vm(service_id)
            state = vm.get("provisioning_state", "unknown")
        except Exception as e:
            print(f"  VM API error: {e}")
            state = "unknown"

        if state in ("cancelled", "deleted", "suspended", "terminated"):
            print(f"\nVM state={state} — run complete (or VM was cancelled externally)")
            return 0

        # Check bootstrap status via SSH
        r = subprocess.run(
            [*ssh_base, ssh_target,
             "cat /tmp/tollgate-status 2>/dev/null | tail -1; "
             "test -f /tmp/tollgate-done && echo MARKER_DONE"],
            capture_output=True, text=True, timeout=15,
        )
        output = r.stdout.strip()

        if "MARKER_DONE" in output:
            print("\nPipeline complete!")
            if not keep_vm_on_failure:
                print(f"Cancelling VM #{service_id}...")
                client.cancel_vm(service_id, immediate=True)
                print("VM cancelled.")
            return 0

        if "BOOTSTRAP_FAILED" in output:
            print(f"\nBootstrap FAILED: {output}")
            # Fetch last 30 lines of log for diagnostics
            r = subprocess.run(
                [*ssh_base, ssh_target, "tail -30 /var/log/tollgate-run.log 2>/dev/null"],
                capture_output=True, text=True, timeout=15,
            )
            print(r.stdout)
            if not keep_vm_on_failure:
                print(f"Cancelling VM #{service_id}...")
                client.cancel_vm(service_id, immediate=True)
            return 1

        # Show progress from log
        r = subprocess.run(
            [*ssh_base, ssh_target, "tail -1 /var/log/tollgate-run.log 2>/dev/null"],
            capture_output=True, text=True, timeout=15,
        )
        line = r.stdout.strip()
        if line and line != last_status_line:
            elapsed = int(time.time() - (deadline - timeout_s))
            print(f"  [{elapsed}s] {line}")
            last_status_line = line

        time.sleep(15)

    print(f"\nTimeout after {timeout_s}s")
    return 1
