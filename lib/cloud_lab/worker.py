"""Autonomous cloud lab worker — runs on the GCP outer VM."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from lib.cloud_lab.constants import (
    CLOUD_ARCH,
    DEBIAN_IP,
    DEBIAN_MAC,
    OPENWRT_IP,
    RESULTS_ROOT,
    SUITE_REPO_URL,
    TEST_DIR,
    VIRT_LAB_PASSWORD,
    VIRT_LAB_WORKDIR,
)

log = logging.getLogger("tollgate.cloud_worker")

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
    publish: bool
    project: str
    zone: str
    vm_name: str
    gh_token: str


def _metadata_get(key: str) -> str:
    req = urllib.request.Request(
        f"{METADATA_URL}/{key}",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode().strip()


def load_config_from_metadata() -> WorkerConfig:
    return WorkerConfig(
        run_id=_metadata_get("tollgate-run-id"),
        sut_branch=_metadata_get("tollgate-sut-branch"),
        sut_commit=_metadata_get("tollgate-sut-commit"),
        sut_pr=_metadata_get("tollgate-pr"),
        artifact_run_id=_metadata_get("tollgate-artifact-run-id"),
        artifact_repo=_metadata_get("tollgate-artifact-repo"),
        suite_ref=_metadata_get("tollgate-suite-ref"),
        backend=_metadata_get("tollgate-backend"),
        publish=_metadata_get("tollgate-publish").lower() in ("true", "1", "yes"),
        project=_metadata_get("tollgate-project"),
        zone=_metadata_get("tollgate-zone"),
        vm_name=_metadata_get("tollgate-vm-name"),
        gh_token=_metadata_get("tollgate-gh-token"),
    )


def _run(cmd: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    log.debug("run: %s", cmd[:200])
    r = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[-800:]
        raise RuntimeError(f"Command failed ({r.returncode}): {cmd[:120]}\n{err}")
    return r


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
        _run(f"cd {TEST_DIR} && git fetch --depth 1 origin && git checkout {shlex.quote(config.suite_ref)}", timeout=120)
    else:
        parent = test_dir.parent
        _run(f"rm -rf {TEST_DIR} && git clone --depth 50 {SUITE_REPO_URL} {TEST_DIR}", timeout=180)
        _run(f"cd {TEST_DIR} && git checkout {shlex.quote(config.suite_ref)}", timeout=60)


def write_env_file(backend: str) -> None:
    env_content = (
        f"TOLLGATE_LUCI_PASSWORD={VIRT_LAB_PASSWORD}\n"
        f"TOLLGATE_SSH_PASSWORD={VIRT_LAB_PASSWORD}\n"
        f"TOLLGATE_SSH_HOST={OPENWRT_IP}\n"
        f"TOLLGATE_LUCI_URL=http://{OPENWRT_IP}\n"
        f"TOLLGATE_ROUTER_ARCH={CLOUD_ARCH}\n"
        f"TOLLGATE_CLIENT_TYPE=container\n"
        f"TOLLGATE_VIRTUAL_LAB=1\n"
        f"TOLLGATE_VIRTUAL_GATEWAY={OPENWRT_IP}\n"
        f"TOLLGATE_CLIENT_IP={DEBIAN_IP}\n"
        f"TOLLGATE_CLIENT_MAC={DEBIAN_MAC}\n"
        f"TOLLGATE_CONTAINER_HOST={DEBIAN_IP}\n"
        f"TOLLGATE_ROUTER_ID=gcp-cloud\n"
        f"TOLLGATE_ROUTER_MODEL=gcp-n2-standard-2\n"
        f"TOLLGATE_BACKEND={backend}\n"
        f"TOLLGATE_VIEWPORT=desktop\n"
        f"TOLLGATE_DISABLE_ARTIFACT_RERUN=1\n"
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

    r = _run("test -x /tmp/cashu-venv/bin/cashu && echo CASHU_OK", timeout=10, check=False)
    if "CASHU_OK" not in r.stdout:
        log.info("Setting up cashu CLI venv...")
        _run(
            "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv >/dev/null && "
            "rm -rf /tmp/cashu-venv && python3 -m venv /tmp/cashu-venv && "
            "/tmp/cashu-venv/bin/pip install -q --upgrade pip && "
            "/tmp/cashu-venv/bin/pip install -q cashu 'marshmallow<4' && "
            "/tmp/cashu-venv/bin/python - <<'PY'\n"
            "from pathlib import Path\n"
            "import cashu.core.models\n"
            "models=Path(cashu.core.models.__file__)\n"
            "text=models.read_text()\n"
            "text=text.replace('    active: bool\\n','    active: bool = True\\n')\n"
            "models.write_text(text)\n"
            "PY\n"
            "test -x /tmp/cashu-venv/bin/cashu && echo CASHU_OK",
            timeout=240,
        )


def ensure_github_cli(token: str) -> None:
    os.environ["GH_TOKEN"] = token
    r = _run("command -v gh >/dev/null && gh auth status >/dev/null 2>&1 && echo GH_OK", timeout=15, check=False)
    if "GH_OK" in r.stdout:
        return
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
    _run(f"printf '%s\\n' {shlex.quote(token)} | gh auth login --with-token >/dev/null 2>&1", timeout=30)
    r = _run("gh auth status >/dev/null 2>&1 && echo GH_OK", timeout=15, check=False)
    if "GH_OK" not in r.stdout:
        raise RuntimeError("gh auth failed on worker VM")


def reset_openwrt_overlay_only() -> None:
    """Reset OpenWrt disk state; preserve Debian overlay (Playwright cache)."""
    log.info("Resetting OpenWrt overlay only (Debian overlay preserved)")
    _run(
        "killall -9 qemu-system-x86_64 2>/dev/null || true; sleep 1; "
        f"cd {VIRT_LAB_WORKDIR} && "
        "OWRT_BASE=images/openwrt-base.qcow2; "
        "[ -f \"$OWRT_BASE\" ] || OWRT_BASE=../images/openwrt-base.qcow2; "
        "rm -f overlays/tollgate-poc.qcow2 && "
        "qemu-img create -f qcow2 -F qcow2 -b \"$OWRT_BASE\" overlays/tollgate-poc.qcow2 >/dev/null",
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
        "iptables -t nat -C POSTROUTING -s 10.99.99.0/24 ! -o tg-poc-br -j MASQUERADE 2>/dev/null || "
        "iptables -t nat -A POSTROUTING -s 10.99.99.0/24 ! -o tg-poc-br -j MASQUERADE; "
        f"mkdir -p {VIRT_LAB_WORKDIR}/run",
        timeout=20,
    )


def start_inner_vms() -> None:
    setup_bridge()
    reset_openwrt_overlay_only()

    log.info("Starting OpenWrt VM...")
    _run(
        f"cd {VIRT_LAB_WORKDIR} && "
        "setsid -f qemu-system-x86_64 -enable-kvm -m 256 -smp 1 -nographic "
        "-drive file=overlays/tollgate-poc.qcow2,format=qcow2,if=virtio "
        "-netdev tap,id=net0,ifname=tg-poc-tap,script=no,downscript=no "
        "-device virtio-net-pci,netdev=net0,mac=52:54:00:12:34:56 "
        "-pidfile run/openwrt.pid </dev/null >/tmp/openwrt.log 2>&1",
        timeout=20,
    )
    if not _wait_inner_ssh(OPENWRT_IP):
        raise RuntimeError("OpenWrt VM did not become reachable")
    log.info("OpenWrt VM SSH OK")

    _run(
        f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} ssh "
        f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@{OPENWRT_IP} "
        f"\"grep -q {DEBIAN_MAC} /tmp/dhcp.leases 2>/dev/null || "
        f"echo '0 {DEBIAN_MAC} {DEBIAN_IP} debian-client *' >> /tmp/dhcp.leases\"",
        timeout=15,
    )

    log.info("Starting Debian VM (cached overlay)...")
    _run(
        f"cd {VIRT_LAB_WORKDIR} && "
        "setsid -f qemu-system-x86_64 -enable-kvm -m 1024 -smp 2 -nographic "
        "-drive file=overlays/debian-client.qcow2,format=qcow2,if=virtio "
        "-netdev tap,id=net0,ifname=tg-poc-tap2,script=no,downscript=no "
        f"-device virtio-net-pci,netdev=net0,mac={DEBIAN_MAC} "
        "-pidfile run/debian.pid </dev/null >/tmp/debian.log 2>&1",
        timeout=20,
    )
    time.sleep(25)
    if not _wait_inner_ssh(DEBIAN_IP):
        raise RuntimeError("Debian VM did not become reachable")
    log.info("Debian VM SSH OK")


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
    repo_arg = shlex.quote(config.artifact_repo)
    branch_arg = shlex.quote(config.sut_branch)
    run_id_arg = shlex.quote(config.artifact_run_id)
    backend_arg = shlex.quote(config.backend)
    py = (
        "import sys,logging,os;"
        "os.environ['TOLLGATE_DISABLE_ARTIFACT_RERUN']='1';"
        "logging.basicConfig(level=logging.INFO,format='%(asctime)s [%(name)s] %(message)s',datefmt='%H:%M:%S');"
        "from lib.router import Router;"
        "from lib.deploy import deploy_branch;"
        "from lib.backend import BackendConfig;"
        f"b=BackendConfig({backend_arg});"
        f"r=Router(host='{OPENWRT_IP}',phone_ip='',phone_mac='',domain='',backend=b);"
        f"result=deploy_branch(r,{branch_arg},arch='{CLOUD_ARCH}',force=True,reboot=False,"
        f"repo={repo_arg},backend=b,run_id={run_id_arg});"
        "print(f\"version={result['installed_version']} health={result['health_code']} success={result['success']}\");"
        "sys.exit(0 if result['success'] else 1)"
    )
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"python3 -c {shlex.quote(py)}",
        timeout=300,
    )


def wait_for_backend() -> None:
    for _ in range(30):
        r = _run(f"curl -s -o /dev/null -w '%{{http_code}}' http://{OPENWRT_IP}:2121/ || true", timeout=10, check=False)
        if "200" in r.stdout:
            log.info("TollGate backend healthy")
            return
        time.sleep(2)
    raise RuntimeError("TollGate backend did not become healthy")


def run_tests(config: WorkerConfig, results_dir: str) -> int:
    expected_pr = f"--expected-pr={config.sut_pr} " if config.sut_pr else ""
    backend = config.backend
    test_cmd = (
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"mkdir -p {results_dir}/raw/api {results_dir}/raw/visual {results_dir}/report && "
        "visual_exit=0; api_exit=0; "
        f"python3 -m pytest tests/api/test_visual_happy_path.py -v --tb=short --backend={backend} "
        f"{expected_pr}--client=container --results {results_dir} "
        f"--junitxml={results_dir}/raw/visual/junit.xml "
        f"--html={results_dir}/raw/visual/report.html --self-contained-html "
        f"2>&1 | tee {results_dir}/raw/visual/output.log || visual_exit=${{PIPESTATUS[0]}}; "
        f"python3 -m pytest tests/api/ -v --tb=short --backend={backend} "
        f"{expected_pr}--client=container --results {results_dir} "
        f"--ignore=tests/api/test_visual_happy_path.py "
        f"--junitxml={results_dir}/raw/api/junit.xml "
        f"--html={results_dir}/raw/api/report.html --self-contained-html "
        f"2>&1 | tee {results_dir}/raw/api/output.log || api_exit=${{PIPESTATUS[0]}}; "
        f"if [ \"$visual_exit\" -ne 0 ]; then exit \"$visual_exit\"; fi; "
        "exit \"$api_exit\""
    )
    r = _run(test_cmd, timeout=1200, check=False)
    return r.returncode


def collect_and_render(config: WorkerConfig, results_dir: str, started_at: str, finished_at: str) -> None:
    commit_arg = f"--sut-commit {config.sut_commit} " if config.sut_commit else ""
    pr_arg = f"--sut-pr {config.sut_pr} " if config.sut_pr else ""
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && set -a && source .env && set +a && "
        f"python3 scripts/collect-results.py --run-dir {results_dir} "
        f"--pytest visual=raw/visual/junit.xml --pytest api=raw/api/junit.xml "
        f"--run-id {config.run_id} "
        f"--sut-repo {config.artifact_repo} --sut-branch {shlex.quote(config.sut_branch)} "
        f"{commit_arg}{pr_arg}--sut-backend {config.backend} "
        f"--suite-commit {config.suite_ref} --client-type container "
        f"--router-id gcp-cloud --router-model gcp-n2-standard-2 --router-arch {CLOUD_ARCH} "
        f"--viewport desktop --test-plan cloud-api --query-router {OPENWRT_IP} --virtual-lab "
        f"--started-at {started_at} --finished-at {finished_at} --allow-failures",
        timeout=60,
    )
    _run(
        f"cd {TEST_DIR} && source /opt/tollgate-venv/bin/activate && "
        f"python3 scripts/render-report.py --run-dir {results_dir}",
        timeout=60,
    )


def publish_results(config: WorkerConfig, results_dir: str) -> str:
    _run(
        f"cd {TEST_DIR} && TOLLGATE_GH_PAGES_CNAME=tests.tollgate.me "
        f"./scripts/publish-report.sh {shlex.quote(results_dir)}",
        timeout=300,
    )
    run_json = Path(results_dir) / "run.json"
    if run_json.exists():
        data = json.loads(run_json.read_text())
        commit_short = (data.get("sut") or {}).get("commit_short") or config.sut_commit[:7]
        return f"https://tests.tollgate.me/reports/{commit_short}/{config.run_id}/report/index.html"
    return "https://tests.tollgate.me/"


def post_pr_comment(config: WorkerConfig, report_url: str, counts: dict) -> None:
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
        f"gh pr comment {shlex.quote(config.sut_pr)} --repo {shlex.quote(config.artifact_repo)} "
        f"--body {shlex.quote(body)}",
        timeout=30,
        check=False,
    )


def stop_inner_vms() -> None:
    _run("killall -9 qemu-system-x86_64 2>/dev/null || true", timeout=15, check=False)


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

    try:
        os.environ["GH_TOKEN"] = config.gh_token
        ensure_suite_checkout(config)
        write_env_file(config.backend)
        ensure_outer_deps()
        ensure_github_cli(config.gh_token)
        start_inner_vms()
        ensure_debian_client_deps()
        deploy_tollgate(config)
        wait_for_backend()
        test_exit = run_tests(config, results_dir)
        finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        collect_and_render(config, results_dir, started_at, finished_at)

        counts: dict = {}
        run_json = Path(results_dir) / "run.json"
        if run_json.exists():
            counts = json.loads(run_json.read_text()).get("counts", {})

        report_url = ""
        if config.publish and run_json.exists():
            report_url = publish_results(config, results_dir)
            log.info("Published: %s", report_url)
            post_pr_comment(config, report_url, counts)

        log.info(
            "Run complete: passed=%s failed=%s skipped=%s exit=%s",
            counts.get("passed", "?"),
            counts.get("failed", "?"),
            counts.get("skipped", "?"),
            test_exit,
        )
        return test_exit
    finally:
        stop_inner_vms()
        delete_self(config)


def main() -> int:
    if "--from-metadata" not in sys.argv:
        print("Usage: python -m lib.cloud_lab.worker --from-metadata", file=sys.stderr)
        return 2
    config = load_config_from_metadata()
    return run_worker(config)


if __name__ == "__main__":
    raise SystemExit(main())
