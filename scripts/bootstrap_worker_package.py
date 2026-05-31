#!/usr/bin/env python3
"""Bootstrap lib/cloud_lab/worker/ package from worker.py monolith."""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC_PATH = REPO / "lib" / "cloud_lab" / "worker.py"
PKG = REPO / "lib" / "cloud_lab" / "worker"
SOURCE = SRC_PATH.read_text()
tree = ast.parse(SOURCE)
lines = SOURCE.splitlines(keepends=True)


def extract(name: str) -> str:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return "".join(lines[node.lineno - 1 : node.end_lineno])
    raise KeyError(name)


def extract_assign(name: str) -> str:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return "".join(lines[node.lineno - 1 : node.end_lineno])
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                return "".join(lines[node.lineno - 1 : node.end_lineno])
    return ""


def write_module(filename: str, doc: str, imports: str, body: str) -> None:
    content = f'"""{doc}"""\n\nfrom __future__ import annotations\n\n{imports}\n\n{body}'
    (PKG / filename).write_text(content)
    print(f"  {filename}")


def main() -> None:
    PKG.mkdir(exist_ok=True)

    write_module(
        "shell.py",
        "Cloud lab worker — shell utilities.",
        "import logging\nimport subprocess\nimport time",
        extract_assign("log")
        + extract_assign("_REDACT_PATTERNS")
        + extract("_redact")
        + extract("_run"),
    )

    write_module(
        "config.py",
        "Cloud lab worker — GCP metadata config.",
        """import logging
import urllib.request
from dataclasses import dataclass

from lib.cloud_lab.worker.shell import log""",
        extract_assign("METADATA_URL")
        + extract("WorkerConfig").replace("class WorkerConfig", "@dataclass\nclass WorkerConfig")
        + extract("_metadata_get")
        + extract("_metadata_get_optional")
        + extract("load_config_from_metadata"),
    )

    network_body = (
        extract("setup_bridge")
        + extract("_configure_beta_lan").replace("def _configure_beta_lan", "def configure_beta_lan")
        + extract("_configure_beta_upstream").replace("def _configure_beta_upstream", "def configure_beta_upstream")
        + extract("_configure_alpha_wan").replace("def _configure_alpha_wan", "def configure_alpha_wan")
        + extract("_configure_two_router_payment").replace(
            "def _configure_two_router_payment", "def configure_two_router_payment"
        )
    ).replace("_inner_ssh", "inner_ssh")
    write_module(
        "network.py",
        "Cloud lab worker — network bridges and two-router topology.",
        """import json
import logging
import shlex
import time

from lib.cloud_lab.constants import (
    BETA_BRIDGE,
    BETA_LAN_HOST_IP,
    BETA_LAN_IP,
    BETA_LAN_SUBNET,
    BETA_TAP,
    BETA_WAN_IP,
    LOCAL_MINT_HOST,
    MGMT_BETA_IP,
    MGMT_BRIDGE,
    MGMT_HOST_IP,
    MGMT_SUBNET,
    MGMT_TAP_ALPHA,
    MGMT_TAP_BETA,
    MGMT_TAP_DEBIAN,
    OPENWRT_IP,
    UPSTREAM_BRIDGE,
    UPSTREAM_TAP_ALPHA,
    UPSTREAM_TAP_BETA,
    VIRT_LAB_PASSWORD,
    VIRT_LAB_WORKDIR,
)
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.inner_ssh import inner_ssh
from lib.cloud_lab.worker.shell import _run, log""",
        network_body,
    )

    vms_body = (
        extract("_virt_lab_workdir")
        + extract("_launch_qemu")
        + extract("_configure_mgmt_nic").replace("def _configure_mgmt_nic", "def configure_mgmt_nic")
        + extract("_recv_serial")
        + extract("_serial_send_wait")
        + extract("_provision_openwrt_serial")
        + extract("reset_openwrt_overlay_only")
        + extract("start_inner_vms").replace("_wait_inner_ssh", "wait_inner_ssh")
        .replace("_configure_mgmt_nic", "configure_mgmt_nic")
        .replace("_configure_beta_lan", "configure_beta_lan")
        .replace("_configure_beta_upstream", "configure_beta_upstream")
        .replace("_configure_alpha_wan", "configure_alpha_wan")
        + extract("stop_inner_vms")
        + extract("delete_self")
    )
    write_module(
        "vms.py",
        "Cloud lab worker — inner QEMU VMs.",
        """import logging
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path

from lib.cloud_lab.constants import (
    ALPHA_WAN_MAC,
    BETA_BRIDGE,
    BETA_LAN_HOST_IP,
    BETA_LAN_IP,
    BETA_TAP,
    BETA_WAN_MAC,
    DEBIAN_IP,
    DEBIAN_MAC,
    MGMT_ALPHA_IP,
    MGMT_ALPHA_MAC,
    MGMT_BETA_IP,
    MGMT_BETA_MAC,
    MGMT_DEBIAN_IP,
    MGMT_DEBIAN_MAC,
    MGMT_TAP_ALPHA,
    MGMT_TAP_BETA,
    MGMT_TAP_DEBIAN,
    OPENWRT_IP,
    SELLER_OPENWRT_IP,
    SELLER_OPENWRT_MAC,
    UPSTREAM_BRIDGE,
    UPSTREAM_TAP_ALPHA,
    UPSTREAM_TAP_BETA,
    VIRT_LAB_PASSWORD,
    VIRT_LAB_WORKDIR,
)
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.inner_ssh import inner_ssh, wait_inner_ssh
from lib.cloud_lab.worker.network import (
    configure_alpha_wan,
    configure_beta_lan,
    configure_beta_upstream,
    setup_bridge,
)
from lib.cloud_lab.worker.shell import _run, log""",
        vms_body,
    )

    wifi_body = (
        extract_assign("_VWIFI_BIN_DIR")
        + extract("_ensure_vwifi_binaries")
        + extract("_setup_vwifi_host").replace("def _setup_vwifi_host", "def setup_vwifi_host")
        + extract("_setup_vwifi_guests").replace("def _setup_vwifi_guests", "def setup_vwifi_guests").replace(
            "_inner_ssh", "inner_ssh"
        )
        + extract("_setup_hwsim_wifi").replace("def _setup_hwsim_wifi", "def setup_hwsim_wifi").replace(
            "_inner_ssh", "inner_ssh"
        )
    )
    write_module(
        "wifi.py",
        "Cloud lab worker — hwsim and vwifi setup.",
        """import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

from lib.cloud_lab.constants import TEST_DIR, VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.inner_ssh import inner_ssh
from lib.cloud_lab.worker.shell import _run, log""",
        wifi_body,
    )

    mints_body = (
        extract("ensure_cdk_binary")
        + extract("start_local_mints")
        + extract("stop_local_mints")
        + extract("_configure_mint")
        + extract("select_test_mint")
    )
    write_module(
        "mints.py",
        "Cloud lab worker — local Cashu mints.",
        """import json
import logging
import os
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
from lib.cloud_lab.worker.shell import _run, log""",
        mints_body,
    )

    provision_body = extract("ensure_suite_checkout") + extract("write_env_file") + extract("ensure_outer_deps")
    provision_body += extract("wait_for_dpkg_lock") + extract("ensure_github_cli")
    provision_body += extract("ensure_debian_client_deps").replace("_inner_ssh", "inner_ssh")
    write_module(
        "provision.py",
        "Cloud lab worker — suite checkout and outer deps.",
        """import logging
import os
import shlex
import time
from pathlib import Path

from lib.cloud_lab.constants import (
    CDK_MINT_URL,
    CLOUD_ARCH,
    DEBIAN_IP,
    DEBIAN_MAC,
    MGMT_BETA_IP,
    NUTSHELL_V1_MINT_URL,
    NUTSHELL_V2_MINT_URL,
    OPENWRT_IP,
    SELLER_OPENWRT_IP,
    SUITE_REPO_URL,
    TEST_DIR,
    VIRT_LAB_PASSWORD,
)
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.inner_ssh import inner_ssh
from lib.cloud_lab.worker.mints import ensure_cdk_binary
from lib.cloud_lab.worker.shell import _run, log""",
        provision_body,
    )

    write_module(
        "deploy.py",
        "Cloud lab worker — TollGate deploy.",
        """import logging
import shlex
import time

from lib.cloud_lab.constants import CLOUD_ARCH, OPENWRT_IP, TEST_DIR
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import _run, log""",
        extract("deploy_tollgate") + extract("deploy_portal_overlay") + extract("wait_for_backend"),
    )

    write_module(
        "preflight.py",
        "Cloud lab worker — pre-flight checks.",
        """import json
import logging
import shlex
import urllib.request
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import DEBIAN_IP, OPENWRT_IP, TEST_DIR, VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import _run, log""",
        extract("preflight_check"),
    )

    logstream_body = (
        extract("start_syslog_capture")
        + extract("configure_openwrt_syslog")
        + extract("_start_vm_log_streaming").replace("def _start_vm_log_streaming", "def start_vm_log_streaming")
        + extract("_stop_vm_log_streaming").replace("def _stop_vm_log_streaming", "def stop_vm_log_streaming")
    )
    write_module(
        "logstream.py",
        "Cloud lab worker — syslog and VM log streaming.",
        """import logging
import os
import shlex
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import DEBIAN_IP, OPENWRT_IP, SELLER_OPENWRT_IP, VIRT_LAB_PASSWORD
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import _redact, _run, log""",
        logstream_body,
    )

    collect_body = extract("collect_and_render")
    old_runners = (
        '    pytest_runners = "--pytest visual=raw/visual/junit.xml "\n'
        "    if config.smoke:\n"
        '        pytest_runners += "--pytest smoke-api=raw/smoke-api/junit.xml "\n'
        "        if config.hwsim_enabled:\n"
        '            pytest_runners += "--pytest hwsim=raw/hwsim/junit.xml "\n'
        '        if config.wifi_plane == "hwsim-netns":\n'
        '            pytest_runners += "--pytest virtual-wifi=raw/virtual-wifi/junit.xml "\n'
        "    elif not config.quick:\n"
        '        pytest_runners += "--pytest api=raw/api/junit.xml "\n'
        "        if config.reseller_scenarios:\n"
        '            pytest_runners += "--pytest scenarios=raw/scenarios/junit.xml "\n'
        "        if config.two_router:\n"
        '            pytest_runners += "--pytest two-router=raw/two-router/junit.xml "\n'
        '        pytest_runners += "--pytest vl-scenarios=raw/vl-scenarios/junit.xml "\n'
        '        if config.wifi_plane == "hwsim-netns":\n'
        '            pytest_runners += "--pytest virtual-wifi=raw/virtual-wifi/junit.xml "\n'
    )
    collect_body = collect_body.replace(old_runners, "    pytest_runners = pytest_collect_args(config)\n")
    collect_body = collect_body.replace(
        'scope = "quick" if config.quick else ("smoke" if config.smoke else "full")',
        "scope = runner_scope(config)",
    )

    report_body = (
        collect_body
        + extract("_create_minimal_run_json").replace("def _create_minimal_run_json", "def create_minimal_run_json")
        + extract("publish_results")
        + extract("post_pr_comment").replace(
            '    repo = config.artifact_repo.split("/")[0] + "/tollgate-module-basic-go"\n'
            '    if "tollgate-rs" in config.artifact_repo or config.backend == "rust":\n'
            "        repo = config.artifact_repo",
            "    repo = config.artifact_repo",
        )
    )
    write_module(
        "report.py",
        "Cloud lab worker — collect, render, publish.",
        """import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import CLOUD_ARCH, OPENWRT_IP, TEST_DIR
from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.runner import pytest_collect_args, runner_scope
from lib.cloud_lab.worker.shell import _redact, _run, log""",
        report_body,
    )

    pipeline_body = (
        extract_assign("_pipeline_t0")
        + extract_assign("_pipeline_steps")
        + extract("_step_start")
        + extract("_step_end")
        + extract("_log_pipeline_summary")
        + extract("_save_pipeline_timing")
        + extract("_finish_pending_step")
        + extract("run_worker")
        .replace("1h max lifetime exceeded", "2h max lifetime exceeded")
        .replace("_setup_vwifi_host()", "setup_vwifi_host()")
        .replace("_setup_vwifi_guests(", "setup_vwifi_guests(")
        .replace("_setup_hwsim_wifi(", "setup_hwsim_wifi(")
        .replace("_start_vm_log_streaming(", "start_vm_log_streaming(")
        .replace("_stop_vm_log_streaming(", "stop_vm_log_streaming(")
        .replace("_configure_two_router_payment(", "configure_two_router_payment(")
        .replace("_create_minimal_run_json(", "create_minimal_run_json(")
    )
    write_module(
        "pipeline.py",
        "Cloud lab worker — orchestration pipeline.",
        """import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from lib.cloud_lab.constants import (
    CDK_MINT_URL,
    DEBIAN_IP,
    OPENWRT_IP,
    RESULTS_ROOT,
    TEST_DIR,
    WORKER_LOG,
)
from lib.cloud_lab.worker.config import WorkerConfig, load_config_from_metadata
from lib.cloud_lab.worker.deploy import deploy_portal_overlay, deploy_tollgate, wait_for_backend
from lib.cloud_lab.worker.logstream import (
    configure_openwrt_syslog,
    start_syslog_capture,
    start_vm_log_streaming,
    stop_vm_log_streaming,
)
from lib.cloud_lab.worker.mints import select_test_mint, start_local_mints, stop_local_mints
from lib.cloud_lab.worker.network import configure_two_router_payment
from lib.cloud_lab.worker.preflight import preflight_check
from lib.cloud_lab.worker.provision import (
    ensure_debian_client_deps,
    ensure_github_cli,
    ensure_outer_deps,
    ensure_suite_checkout,
    write_env_file,
)
from lib.cloud_lab.worker.report import (
    collect_and_render,
    create_minimal_run_json,
    post_pr_comment,
    publish_results,
)
from lib.cloud_lab.worker.runner import run_tests
from lib.cloud_lab.worker.shell import _redact, _run, log
from lib.cloud_lab.worker.vms import delete_self, start_inner_vms, stop_inner_vms
from lib.cloud_lab.worker.wifi import setup_hwsim_wifi, setup_vwifi_guests, setup_vwifi_host

MAX_WALL_SECONDS = 7200""",
        pipeline_body,
    )

    # __init__.py and __main__.py
    (PKG / "__init__.py").write_text(
        '"""Autonomous cloud lab worker package."""\n\n'
        "from lib.cloud_lab.worker.config import WorkerConfig, load_config_from_metadata\n"
        "from lib.cloud_lab.worker.pipeline import run_worker\n"
        "from lib.cloud_lab.worker.runner import build_runners, run_tests\n\n"
        "__all__ = [\n"
        '    "WorkerConfig",\n'
        '    "load_config_from_metadata",\n'
        '    "run_worker",\n'
        '    "build_runners",\n'
        '    "run_tests",\n'
        "]\n"
    )
    (PKG / "__main__.py").write_text(
        '"""Entry point: python -m lib.cloud_lab.worker --from-metadata"""\n\n'
        "from __future__ import annotations\n\n"
        "import sys\n\n"
        "from lib.cloud_lab.worker.config import load_config_from_metadata\n"
        "from lib.cloud_lab.worker.pipeline import run_worker\n\n\n"
        "def main() -> int:\n"
        '    if "--from-metadata" not in sys.argv:\n'
        '        print("Usage: python -m lib.cloud_lab.worker --from-metadata", file=sys.stderr)\n'
        "        return 2\n"
        "    config = load_config_from_metadata()\n"
        "    return run_worker(config)\n\n\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )

    # Remove legacy split artifacts
    for legacy in ("_runner_legacy.py",):
        p = PKG / legacy
        if p.exists():
            p.unlink()

    print("Package bootstrap complete")


if __name__ == "__main__":
    main()
