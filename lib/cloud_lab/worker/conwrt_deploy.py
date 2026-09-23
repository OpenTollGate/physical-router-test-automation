"""Cloud lab worker — conwrt-based deploy.

Uses conwrt's flow renderer to generate the exact same configuration
commands that the conwrt wizard would produce for a physical router,
then executes them against the QEMU OpenWrt VM.

This tests conwrt's deployment path end-to-end: the flow registry, the
use_case system, the target profile derivation, and the rendered shell
commands — not just the resulting router state.
"""

from __future__ import annotations

import shlex

from lib.cloud_lab.constants import OPENWRT_IP, VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import _run, log

CONWRT_DIR = "/opt/conwrt"
CONWRT_REPO = "https://github.com/amperstrand/conwrt.git"
SKIP_KINDS = frozenset({"flash", "wifi_sta", "set_lan_ip"})

_SSH_PREFIX = (
    f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} "
    f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
    f"-o ControlPath=none root@{OPENWRT_IP}"
)


def ensure_conwrt() -> None:
    r = _run(f"test -d {CONWRT_DIR}/scripts/flows && echo EXISTS || echo MISSING", timeout=5, check=False)
    if "EXISTS" in r.stdout:
        # Keep an existing clone current — a reused worker VM must never test
        # a stale conwrt silently.
        _run(
            f"git -C {CONWRT_DIR} fetch -q origin master "
            f"&& git -C {CONWRT_DIR} reset -q --hard origin/master",
            timeout=60, check=False,
        )
        return
    log.info("Cloning conwrt for flow-based deploy...")
    _run(f"git clone --depth 1 {CONWRT_REPO} {CONWRT_DIR}", timeout=60)


def _execute_host_commands(lines: list[str], timeout: int = 180) -> None:
    """Run rendered host-side commands as one script piped to bash via stdin.

    Piping (with set -e) instead of joining lines with ' && ' keeps multi-line
    and heredoc commands valid — the same bug class conwrt hit when it
    &&-joined rendered scripts into ash syntax errors — while preserving the
    fail-fast behavior of the old chain."""
    script = "\n".join(ln for ln in lines if ln.strip())
    if not script:
        return
    log.info("conwrt: executing %d host commands", len(script.splitlines()))
    _run(f"printf %s {shlex.quote('set -e\n' + script + '\n')} | bash", timeout=timeout)


def deploy_via_conwrt(config: WorkerConfig) -> None:
    ensure_conwrt()

    portal = config.portal or "net4sats"

    py = f"""
import json, subprocess, sys
sys.path.insert(0, "{CONWRT_DIR}/scripts")
from flows import get
from flows.render import _step_parts
from profile.target import derive_target_profile
from profile.ops import render_shell

flow = get({portal!r})
if not flow:
    sys.exit(f"flow {{portal!r}} not found")

model = json.load(open("{CONWRT_DIR}/models/virtual-x86-64.json"))

ver_r = subprocess.run(
    ["sshpass", "-p", "{VIRT_LAB_PASSWORD}",
     "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
     "-o", "ControlPath=none", "root@{OPENWRT_IP}",
     ". /etc/openwrt_release && echo $DISTRIB_RELEASE"],
    capture_output=True, text=True, timeout=15,
)
version = ver_r.stdout.strip()
major = version.split(".")[0] if version and version[0].isdigit() else "24"
print(f"conwrt: OpenWRT {{version}} major={{major}}", file=sys.stderr)

target = derive_target_profile(model, version=major)
print(f"conwrt: target arch={{target['arch']}} pkg={{target['pkg_manager']}} ver={{target['version']}}", file=sys.stderr)

params = {{"upstream_ssid": "dummy", "upstream_key": "dummy", "upstream_band": "5ghz"}}
host_cmds, router_ops = [], []
for step in flow.steps:
    if step.kind in {SKIP_KINDS}:
        continue
    h, r = _step_parts(step, target, params)
    host_cmds.extend(h)
    router_ops.extend(r)

import base64
print("HOST_B64:" + base64.b64encode("\\n".join(host_cmds).encode()).decode())
if router_ops:
    shell = render_shell(router_ops)
    print("ROUTER_B64:" + base64.b64encode(shell.encode()).decode())
"""

    result = _run(f"python3 -c {shlex.quote(py)}", timeout=60)

    import base64 as _b64
    host_script = ""
    router_script = ""
    for line in result.stdout.split("\n"):
        if line.startswith("HOST_B64:"):
            host_script = _b64.b64decode(line[9:]).decode()
        elif line.startswith("ROUTER_B64:"):
            router_script = _b64.b64decode(line[10:]).decode()

    if host_script:
        adapted = host_script.replace("root@$IP", f"root@{OPENWRT_IP}")
        adapted_lines = []
        for hl in adapted.split("\n"):
            if hl.strip().startswith("scp"):
                hl = f"sshpass -p {shlex.quote(VIRT_LAB_PASSWORD)} {hl}"
            adapted_lines.append(hl)
        _execute_host_commands(adapted_lines)

    if router_script:
        log.info("conwrt: applying router ops via SSH")
        _run(
            f"printf %s {shlex.quote(router_script)} | {_SSH_PREFIX} sh",
            timeout=300,
        )

    log.info("conwrt deploy complete (portal=%s, OpenWRT=%s)", portal, result.stderr.strip().split("\n")[-1] if result.stderr else "?")
