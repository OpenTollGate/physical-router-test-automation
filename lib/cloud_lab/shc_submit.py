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

SHC_PACKAGE_ID_STANDARD = 81
SHC_PRICING_ID_STANDARD = 245

# Starter tier: 1C/4GB/8GB at $0.24/day (48% cheaper than Standard $0.46/day)
SHC_PACKAGE_ID_STARTER = 80
SHC_PRICING_ID_STARTER = 241

SHC_TIER_PACKAGE_PRICING = {
    "starter": (SHC_PACKAGE_ID_STARTER, SHC_PRICING_ID_STARTER),
    "standard": (SHC_PACKAGE_ID_STANDARD, SHC_PRICING_ID_STANDARD),
}

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
    repo_root = Path(__file__).resolve().parents[2]
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
    for path in [os.environ.get("NSEC_FILE", ""), str(Path.home() / ".config/prta/nsec")]:
        if path and Path(path).exists():
            return Path(path).read_text().strip()
    return ""


def _working_tree_overlay_b64() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(
        ["git", "diff", "--name-only"], capture_output=True, text=True, cwd=repo_root,
    ).stdout.strip().split("\n")
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"], capture_output=True, text=True, cwd=repo_root,
    ).stdout.strip().split("\n")
    changes = [c for c in tracked + untracked if c and not c.startswith(".omo/") and not c.startswith(".playwright-mcp/")]
    if not changes:
        return ""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in changes:
            full = repo_root / f
            if full.exists() and full.is_file():
                tar.add(str(full), arcname=f)
    return base64.b64encode(buf.getvalue()).decode()


def _wait_for_ssh(ssh_base: list[str], ssh_target: str, timeout: int = 300, sshpass_password: str = "") -> None:
    """Retry SSH until the VM accepts connections.

    Key propagation can take 2-5 minutes after apply_ssh_key_live returns.
    On 1C VMs (Starter tier), cloud-init takes 20+ min — password auth is faster.
    """
    prefix = ["sshpass", "-p", sshpass_password] if sshpass_password else []
    print("Waiting for SSH daemon...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                [*prefix, *ssh_base, ssh_target, "echo OK"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and "OK" in r.stdout:
                print(" ready!")
                return
        except subprocess.TimeoutExpired:
            pass
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
    lease_minutes: int = 90,
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
    STEP_TIMES[$n]=$(date +%s)
    echo "[$n/$N_STEPS] $msg ($(date -u +%H:%M:%S))..."
}}

step_done() {{
    local n=$1
    local elapsed=$(( $(date +%s) - ${{STEP_TIMES[$n]:-0}} ))
    echo "[$n/$N_STEPS] done (${{elapsed}}s)"
    echo "[$n/$N_STEPS] done (${{elapsed}}s)" >> /tmp/tollgate-status
}}

declare -A STEP_TIMES

fail() {{
    local n=$1 msg=$2
    echo "[$n/$N_STEPS] FAILED: $msg"
    echo "BOOTSTRAP_FAILED at step $n" >> /tmp/tollgate-status
    exit 1
}}

N_STEPS=15
echo "BOOTSTRAP_START" >> /tmp/tollgate-status

LEASE_MINUTES={lease_minutes}

self_cancel() {{
    local sid="${{TOLLGATE_SERVICE_ID}}"
    local key="${{SHC_API_KEY}}"
    [ -z "$sid" ] || [ -z "$key" ] && return 1
    local api="https://blesta.sovereignhybridcompute.com/user-api/v2"
    local resp code body cid
    resp=$(curl -s -X POST "$api/vm/$sid/cancel" \
        -H "Authorization: Bearer $key" \
        -H "Content-Type: application/json" \
        -d '{{"immediate": true}}' -w '\n%{{http_code}}' 2>/dev/null)
    code=$(echo "$resp" | tail -1)
    body=$(echo "$resp" | grep -o '{{.*}}' | head -1)
    if [ "$code" = "409" ]; then
        cid=$(echo "$body" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('confirmation',{{}}).get('confirmation_id',''))" 2>/dev/null)
        if [ -n "$cid" ]; then
            curl -s -X POST "$api/vm/$sid/cancel" \
                -H "Authorization: Bearer $key" \
                -H "Content-Type: application/json" \
                -H "X-User-Api-Confirm: $cid" \
                -d '{{"immediate": true}}' >/dev/null 2>&1
            echo "VM cancelled via SHC API (service #$sid)"
        fi
    elif [ "$code" = "200" ] || [ "$code" = "201" ]; then
        echo "VM cancelled via SHC API (service #$sid)"
    fi
}}

echo "Scheduling self-cancel in ${{LEASE_MINUTES}} minutes via at..."
echo "self_cancel; shutdown -h now 'TollGate lease expired'" | at "now + ${{LEASE_MINUTES}} minutes" 2>/dev/null || \
  ( echo "$(( $(date +%s) + LEASE_MINUTES * 60 ))" > /tmp/tollgate-lease-expires && \
    ( while true; do sleep 60; [ "$(date +%s)" -ge "$(cat /tmp/tollgate-lease-expires)" ] && self_cancel && shutdown -h now; done & ) )
echo "Lease kill switch armed (cancels SHC service + shuts down)"

TOTAL_RAM=$(free -m | awk '/^Mem:/{{print $2}}')
if [ "$TOTAL_RAM" -le 4096 ] && [ "$(swapon --show 2>/dev/null | wc -l)" -eq 0 ]; then
  sudo fallocate -l 512M /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile && echo "Swap enabled (512M)"
fi

step 1 "Installing system packages..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq || fail 1 "apt-get update"
sudo apt-get install -y -qq --no-install-recommends qemu-system-x86 qemu-utils \
  sshpass git curl wget python3-venv python3-pip python3-setuptools python3-wheel python3-dev \
  net-tools iproute2 socat nftables build-essential libssl-dev pkg-config \
  fuse3 libfuse3-dev ca-certificates cmake g++ libnl-3-dev libnl-genl-3-dev \
  libsecp256k1-dev jq genisoimage ffmpeg seabios ipxe-qemu \
  libsecp256k1-dev autoconf automake libtool || fail 1 "apt-get install"
sudo apt-get clean && sudo rm -rf /var/lib/apt/lists/*
echo "[1/$N_STEPS] done"

step 2 "Installing Rust..."
if [ -z "$SKIP_BLOSSOMFS" ]; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sudo sh -s -- -y || fail 2 "rustup"
  source /root/.cargo/env
else
  echo "  SKIP_BLOSSOMFS set — Rust not needed"
fi
echo "[2/$N_STEPS] done"

step 3 "Installing nak..."
NAK_VER=0.20.0
sudo wget -q -O /usr/local/bin/nak "https://github.com/fiatjaf/nak/releases/download/v${{NAK_VER}}/nak-v${{NAK_VER}}-linux-amd64" || fail 3 "nak download"
sudo chmod +x /usr/local/bin/nak
echo "[3/$N_STEPS] done"

step 4 "Writing nsec..."
echo -n "$BOT_NSEC_HEX" | sudo tee /root/nsec > /dev/null
sudo chmod 600 /root/nsec
VM_NPUB="$(nak key public "$(cat /root/nsec)" 2>/dev/null || echo DERIVE_FAILED)"
echo "  Publisher npub: $VM_NPUB"
if [ -n "$EXPECTED_NPUB" ] && [ "$VM_NPUB" != "$EXPECTED_NPUB" ]; then
  echo "  WARNING: npub mismatch — got $VM_NPUB, expected $EXPECTED_NPUB"
  if [ "${{STRICT_NPUB_CHECK:-0}}" = "1" ]; then
    fail 4 "npub mismatch (STRICT_NPUB_CHECK=1): got $VM_NPUB, expected $EXPECTED_NPUB"
  fi
fi
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
sudo /opt/tollgate-venv/bin/python3 -c "import nostr_publish" 2>/dev/null || sudo /opt/tollgate-venv/bin/pip install -q nostr-publish || echo "WARN: nostr-publish install failed"
echo "[7/$N_STEPS] done"

step 8 "Creating cashu venv..."
sudo python3 -m venv /opt/cashu-venv || fail 8 "cashu venv"
sudo /opt/cashu-venv/bin/pip install -q --upgrade pip
echo 'scikit-build-core<0.10' > /tmp/pip-constraint.txt && \
  PIP_CONSTRAINT=/tmp/pip-constraint.txt sudo -E /opt/cashu-venv/bin/pip install -q cashu 'marshmallow<4' || fail 8 "cashu install"
MODELS=$(/opt/cashu-venv/bin/python3 -c 'import cashu.core.models; print(cashu.core.models.__file__)')
sudo sed -i 's/    active: bool$/    active: bool = True/' "$MODELS"
echo "[8/$N_STEPS] done"

step 9 "Downloading CDK mints..."
CDK_VER=0.16.0
sudo mkdir -p /opt/cdk-mintd
sudo pkill -f cdk-mintd 2>/dev/null || true
sudo rm -f /opt/cdk-mintd/cdk-mintd /opt/cdk-mintd/cdk-cli
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
OPENWRT_VERSION=${{OPENWRT_VERSION:-24.10.1}}
OWRT_IMG="openwrt-${{OPENWRT_VERSION}}-x86-64-generic-ext4-combined.img.gz"
sudo curl -sfL "https://blossom.psbt.me/924e4b83a34d600914841d53df51bba930d4a56070032a30cba5bca87273c213" -o "$OWRT_IMG" || \
  sudo wget -q "https://downloads.openwrt.org/releases/${{OPENWRT_VERSION}}/targets/x86/64/$OWRT_IMG" || fail 10 "openwrt download"
sudo gunzip -kf "openwrt-${{OPENWRT_VERSION}}-x86-64-generic-ext4-combined.img.gz" || true
sudo rm -f openwrt-base.qcow2
sudo qemu-img convert -f raw -O qcow2 "openwrt-${{OPENWRT_VERSION}}-x86-64-generic-ext4-combined.img" openwrt-base.qcow2 || fail 10 "qemu-img convert"
sudo qemu-img resize openwrt-base.qcow2 2G || fail 10 "qemu-img resize"
sudo wget -q "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2" || fail 10 "debian download"
sudo mv debian-12-genericcloud-amd64.qcow2 debian-12-base.qcow2 2>/dev/null || true
sudo mkdir -p /tmp/ci-seed
cat > /tmp/ci-seed/user-data << 'CIEOF'
#cloud-config
ssh_pwauth: true
runcmd:
  - echo 'root:tollgate' | chpasswd
  - sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
  - sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
  - grep -q PermitRootLogin /etc/ssh/sshd_config || echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config
  - systemctl restart ssh
CIEOF
printf 'instance-id: debian-client-001\nlocal-hostname: debian-client\n' > /tmp/ci-seed/meta-data
genisoimage -quiet -output "$WORKDIR/images/debian-seed.iso" -volid cidata -joliet -rock /tmp/ci-seed/user-data /tmp/ci-seed/meta-data || fail 10 "genisoimage"
echo "[10/$N_STEPS] done"

step 11 "Installing BlossomFS (from cache)..."
if [ -n "$SKIP_BLOSSOMFS" ]; then
  echo "  SKIP_BLOSSOMFS set — skipping (smoke/quick mode)"
else
  fetch_cached() {{
    local key=$1 dest=$2
    # Try hardcoded Blossom URL first (fastest, most reliable)
    case "$key" in
      blossomfs-*) sudo curl -sfL -o "$dest" "https://blossom.psbt.me/22c53f31f1b428d907f2276a44c9cfde8bb1a6f9ac831422ffead8f53fccabf4" && sudo chmod +x "$dest" && return 0 ;;
    esac
    # Fall back
    local url
    url=$(nak req -k 1063 -l 1 -t "filename=$key" wss://relay.cashu.email < /dev/null 2>/dev/null | python3 -c "import sys,json;[print(next(t[1] for t in json.loads(l)['tags'] if t[0]=='url')) for l in [sys.stdin.readline().strip()] if l]" 2>/dev/null)
    if [ -n "$url" ]; then sudo curl -sfL -o "$dest" "$url" && sudo chmod +x "$dest" && return 0; fi
    return 1
  }}
  sudo mkdir -p /opt/blossomfs/target/release
  if fetch_cached blossomfs-8784100 /opt/blossomfs/target/release/blossomfs; then
    echo "  BlossomFS from cache"
  else
    echo "  Compiling BlossomFS from source..."
    sudo rm -rf /opt/blossomfs
    sudo git clone --depth 1 https://github.com/Amperstrand/blossomfs /opt/blossomfs || fail 11 "blossomfs clone"
    cd /opt/blossomfs && sudo /root/.cargo/bin/cargo build --release || fail 11 "blossomfs build"
    echo "  Uploading BlossomFS binary to Blossom for caching..."
    nak blossom upload --server blossom.psbt.me --sec "$(cat /root/nsec)" /opt/blossomfs/target/release/blossomfs 2>/dev/null && echo "  BlossomFS cached" || echo "  Upload failed (non-fatal)"
  fi
fi
echo "[11/$N_STEPS] done"

step 12 "Installing vwifi (from cache)..."
if [ -n "$SKIP_VWIFI" ]; then
  echo "  SKIP_VWIFI set — skipping (smoke/quick mode)"
else
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
fi
echo "[12/$N_STEPS] done"

step 13 "Installing gh CLI..."
if ! command -v gh >/dev/null 2>&1; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list
  sudo apt-get update -qq && sudo apt-get install -y -qq gh || fail 13 "gh install"
fi
echo "[13/$N_STEPS] done"

step 14 "Loading kernel modules + verifying KVM..."
sudo modprobe bridge 2>/dev/null || true
sudo modprobe tun 2>/dev/null || true
sudo modprobe kvm 2>/dev/null || true
sudo modprobe kvm_intel 2>/dev/null || true
sudo modprobe vhost_net 2>/dev/null || true
sudo sysctl -w net.ipv4.ip_forward=1 > /dev/null
echo "  Kernel modules: $(lsmod | grep -E '^(bridge|tun|kvm|vhost_net)' | awk '{{print $1}}' | tr '\\n' ' ')"
if [ -e /dev/kvm ]; then
  echo "  /dev/kvm: present"
else
  echo "  WARNING: /dev/kvm not found — QEMU will use slow TCG emulation"
fi
echo "[14/$N_STEPS] done"

echo "=== Pre-flight: Blossom + Nostr verification ==="
echo "preflight-$(date +%s)" > /tmp/blossom-preflight.txt
if /usr/local/bin/nak blossom upload --server "$BLOSSOM_SERVER" --sec "$(cat /root/nsec)" /tmp/blossom-preflight.txt < /dev/null 2>&1; then
    echo "  Blossom upload: OK ($BLOSSOM_SERVER)"
    rm -f /tmp/blossom-preflight.txt
else
    echo "  WARNING: Blossom pre-flight FAILED — results may not publish"
fi
echo "  BLOSSOM_SERVER: $BLOSSOM_SERVER"
echo "  NOSTR_RELAYS: $NOSTR_RELAYS"
echo "=== Pre-flight complete ==="

step 15 "Running worker pipeline..."
cd {test_dir}
echo "PIPELINE_START" >> /tmp/tollgate-status
unset BOT_NSEC_HEX
echo "  Worker env: TOLLGATE_RUN_ID=$TOLLGATE_RUN_ID SUT_BRANCH=$TOLLGATE_SUT_BRANCH BACKEND=$TOLLGATE_BACKEND PUBLISH=$TOLLGATE_PUBLISH"
/opt/tollgate-venv/bin/python3 -m lib.cloud_lab.worker --from-env
WORKER_EXIT=$?
if [ $WORKER_EXIT -gt 5 ]; then
    fail 15 "worker pipeline crashed (exit=$WORKER_EXIT)"
fi
echo "PIPELINE_DONE (exit=$WORKER_EXIT)" >> /tmp/tollgate-status
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
    two_router: bool = False,
    provider=None,
    tier: str = "standard",
) -> dict[str, str]:
    """Order an SHC VM, bootstrap it, and run the test worker pipeline.

    Returns dict with run_id, vm_name, service_id, ip, and monitoring info.
    The caller is responsible for cancelling the VM when done (via
    wait_for_shc_run or manual cleanup).
    """

    from shc_toolkit.client import SHCClient

    if tier not in SHC_TIER_PACKAGE_PRICING:
        raise ValueError(f"Unknown tier '{tier}'. Use: {list(SHC_TIER_PACKAGE_PRICING)}")
    package_id, pricing_id = SHC_TIER_PACKAGE_PRICING[tier]
    tier_label = tier.capitalize()
    tier_min_balance = 0.25 if tier == "starter" else 0.50

    client = SHCClient()

    balance = client.get_account_balance()
    credit = float(balance.get("credit", [{}])[0].get("amount", 0))
    if credit < tier_min_balance:
        raise RuntimeError(
            f"Insufficient SHC balance: ${credit:.2f}. "
            f"Need at least ${tier_min_balance:.2f} for a Dev VPS {tier_label}. "
            f"Add credit at https://blesta.sovereignhybridcompute.com"
        )
    print(f"SHC balance: ${credit:.2f} (tier: {tier_label})")

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
    hostname = f"tollgate-{short[:8]}-{timestamp[-6:]}"

    suite_ref = _suite_ref()
    token = _gh_token()
    nsec = _nsec_hex()
    overlay_b64 = _working_tree_overlay_b64()
    if overlay_b64:
        print(f"Including local overlay ({len(overlay_b64)} bytes b64)")

    ssh_key_path = os.environ.get("SHC_SSH_KEY", str(Path.home() / ".ssh/id_rsa.pub"))
    pubkey = Path(ssh_key_path).read_text().strip() if Path(ssh_key_path).exists() else ""

    if provider is not None:
        print(f"Creating SHC VM '{hostname}' via Pulumi ({tier_label})...")
        machine_type = "1C/4GB" if tier == "starter" else "2C/8GB"
        vm_info = provider.create_vm(hostname, machine_type=machine_type)
        service_id = int(vm_info.service_id)
        vm_ip = vm_info.ip
        print(f"  VM ready: service #{service_id} @ {vm_ip}")
        if pubkey:
            provider.apply_ssh_key(vm_info, pubkey)
    else:
        tier_specs = {"starter": "1C/4GB/8GB", "standard": "2C/8GB/16GB"}
        print(f"Ordering SHC VM '{hostname}' ({tier_label} {tier_specs[tier]})...")
        result = client.submit_order(
            hostname=hostname,
            package_id=package_id,
            pricing_id=pricing_id,
            idempotency_key=f"tollgate-{run_id}",
        )
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
        if pubkey:
            print("Injecting SSH key...")
            client.apply_ssh_key_live(service_id, pubkey)

    ssh_user = os.environ.get("SHC_SSH_USER", "debian")
    ssh_target = f"{ssh_user}@{vm_ip}"
    ssh_base = [
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
    ]
    # Derive private key path from the public key path for explicit -i flag.
    # This avoids the 5-min timeout when ssh-agent is empty and default keys
    # are passphrase-protected.
    priv_key = ssh_key_path.replace(".pub", "") if ssh_key_path.endswith(".pub") else ssh_key_path
    if priv_key != ssh_key_path and Path(priv_key).exists():
        ssh_base.extend(["-i", priv_key])

    # 5. Wait for SSH daemon
    # cloud-init key propagation can take 5-10 min on any tier. Try to get
    # password credentials as a fallback for all tiers, not just starter.
    use_sshpass = False
    vm_password = ""
    try:
        creds = client.get_vm_credentials(service_id)
        vm_password = creds.get("password", "") or creds.get("root_password", "")
    except Exception:
        pass
    if vm_password:
        use_sshpass = True
        ssh_wait_pw = vm_password
        ssh_wait_timeout = 900
    else:
        ssh_wait_pw = ""
        ssh_wait_timeout = 600

    try:
        _wait_for_ssh(ssh_base, ssh_target, timeout=ssh_wait_timeout, sshpass_password=ssh_wait_pw)
    except TimeoutError:
        if not ssh_wait_pw:
            print(" key auth not ready, retrying key injection...")
            try:
                if pubkey:
                    client.apply_ssh_key_live(service_id, pubkey)
            except Exception:
                pass
            try:
                creds = client.get_vm_credentials(service_id)
                vm_password = creds.get("password", "") or creds.get("root_password", "")
                print(f"  Credentials returned: {list(creds.keys())}")
            except Exception as cred_err:
                print(f"  get_vm_credentials failed: {cred_err}")
                vm_password = ""
            if not vm_password:
                raise TimeoutError(
                    f"SSH key auth failed and no password available for {ssh_target}. "
                    f"Manual intervention required."
                )
            sshpass_check = subprocess.run(
                ["sshpass", "-p", vm_password, *ssh_base, ssh_target, "echo OK"],
                capture_output=True, text=True, timeout=15,
            )
            if sshpass_check.returncode != 0:
                raise TimeoutError(f"Neither key nor password auth worked on {ssh_target}")
            print("  Password auth works — using sshpass for bootstrap")
            use_sshpass = True

    def ssh_cmd(cmd: str) -> list[str]:
        if use_sshpass:
            return ["sshpass", "-p", vm_password, *ssh_base, ssh_target, cmd]
        return [*ssh_base, ssh_target, cmd]

    subprocess.run(
        ssh_cmd("sudo rm -f /tmp/tollgate-status /tmp/tollgate-done /var/log/tollgate-run.log"),
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
        f"OPENWRT_VERSION={shlex.quote(os.environ.get('OPENWRT_VERSION', '24.10.1'))}",
        f"TOLLGATE_DEPLOY_MODE={shlex.quote(os.environ.get('TOLLGATE_DEPLOY_MODE', 'framework'))}",
        f"BLOSSOM_SERVER={shlex.quote(os.environ.get('BLOSSOM_SERVER', 'https://blossom.psbt.me'))}",
        f"NOSTR_RELAYS={shlex.quote(os.environ.get('NOSTR_RELAYS', 'wss://relay1.orangesync.tech,wss://relay.damus.io,wss://nos.lol'))}",
        f"TOLLGATE_BACKEND={shlex.quote(target.backend)}",
        f"TOLLGATE_PUBLISH={'true' if publish else 'false'}",
        f"TOLLGATE_QUICK={'true' if quick else 'false'}",
        f"TOLLGATE_SMOKE={'true' if smoke else 'false'}",
        "SKIP_BLOSSOMFS=1",
        "SKIP_VWIFI=1",
        f"TOLLGATE_COMPLETE={'true' if complete else 'false'}",
        f"TOLLGATE_MINT={shlex.quote(mint)}",
        f"TOLLGATE_PORTAL={shlex.quote(portal)}",
        f"TOLLGATE_KEEP_VM_ON_FAILURE={'true' if keep_vm_on_failure else 'false'}",
        f"TOLLGATE_TWO_ROUTER={'true' if two_router else 'false'}",
        "TOLLGATE_GCP_PROJECT=tollgate-test-lab",
        "TOLLGATE_GCP_ZONE=shc",
        f"TOLLGATE_VM_NAME={hostname}",
        "TOLLGATE_CLOUD=shc",
        f"TOLLGATE_SERVICE_ID={service_id}",
        f"SHC_API_KEY={shlex.quote(os.environ.get('SHC_API_KEY', ''))}",
        f"GH_TOKEN={shlex.quote(token)}",
        f"BOT_NSEC_HEX={shlex.quote(nsec)}",
        f"EXPECTED_NPUB={shlex.quote(os.environ.get('EXPECTED_NPUB', ''))}",
        f"STRICT_NPUB_CHECK={shlex.quote(os.environ.get('STRICT_NPUB_CHECK', '0'))}",
        f"VIRT_LAB_PASSWORD={VIRT_LAB_PASSWORD}",
        "NSEC_FILE=/root/nsec",
        "HOME=/root",
    ])

    bootstrap_script = _build_bootstrap_script(
        bootstrap_env=bootstrap_env,
        overlay_b64=overlay_b64,
        test_dir=TEST_DIR,
        suite_repo_url=SUITE_REPO_URL,
        lease_minutes=lease_minutes,
    )

    # 7. Upload script
    script_path = "/tmp/tollgate-shc-worker.sh"
    print("Uploading bootstrap script...")
    proc = subprocess.run(
        ssh_cmd(f"cat > {script_path} && chmod +x {script_path}"),
        input=bootstrap_script, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to write bootstrap script: {proc.stderr}")

    print("Launching worker pipeline...")
    subprocess.run(
        ssh_cmd(f"nohup sudo bash {script_path} > /dev/null 2>&1 & echo LAUNCHED"),
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
    use_sshpass: bool = False,
    vm_password: str = "",
) -> int:
    print(f"\nMonitoring SHC run (service #{service_id})...")
    print(f"  Log: ssh {ssh_target} 'tail -f /var/log/tollgate-run.log'")
    print()

    deadline = time.time() + timeout_s
    start = time.time()
    last_status_line = ""

    def build_ssh_cmd(remote_cmd: str) -> list[str]:
        prefix = ["sshpass", "-p", vm_password] if use_sshpass else []
        return [*prefix, *ssh_base, ssh_target, remote_cmd]

    while time.time() < deadline:
        try:
            vm = client.get_vm(service_id)
            state = vm.get("provisioning_state", "unknown")
        except Exception as e:
            print(f"  VM API error: {e}")
            state = "unknown"

        if state in ("cancelled", "deleted", "suspended", "terminated"):
            print(f"\nVM state={state} — run complete (or VM was cancelled externally)")
            return 0

        if use_sshpass:
            try:
                creds = client.get_vm_credentials(service_id)
                vm_password = creds.get("password", vm_password)
            except Exception:
                pass

        try:
            r = subprocess.run(
                build_ssh_cmd(
                    "cat /tmp/tollgate-status 2>/dev/null | tail -1; "
                    "test -f /tmp/tollgate-done && echo MARKER_DONE"
                ),
                capture_output=True, text=True, timeout=15,
            )
            output = r.stdout.strip()
        except subprocess.TimeoutExpired:
            output = ""

        if "MARKER_DONE" in output:
            print("\nPipeline complete!")
            if not keep_vm_on_failure:
                print(f"Cancelling VM #{service_id}...")
                client.cancel_vm(service_id, immediate=True)
                print("VM cancelled.")
            return 0

        if "BOOTSTRAP_FAILED" in output:
            print(f"\nBootstrap FAILED: {output}")
            try:
                r = subprocess.run(
                    build_ssh_cmd("tail -30 /var/log/tollgate-run.log 2>/dev/null"),
                    capture_output=True, text=True, timeout=15,
                )
                print(r.stdout)
            except Exception:
                pass
            if not keep_vm_on_failure:
                print(f"Cancelling VM #{service_id}...")
                client.cancel_vm(service_id, immediate=True)
            return 1

        try:
            r = subprocess.run(
                build_ssh_cmd("tail -5 /var/log/tollgate-run.log 2>/dev/null"),
                capture_output=True, text=True, timeout=15,
            )
            line = r.stdout.strip()
        except subprocess.TimeoutExpired:
            line = ""

        if line and line != last_status_line:
            elapsed = int(time.time() - start)
            new_lines = [ln for ln in line.splitlines() if ln.strip()]
            for nl in new_lines[-3:]:
                print(f"  [{elapsed}s] {nl}")
            last_status_line = line

        time.sleep(15)

    print(f"\nTimeout after {timeout_s}s")
    return 1
