"""DVM event publishing for the cloud lab pipeline.

Publishes NIP-90 DVM lifecycle events alongside the existing kind 30078
summaries. Events use kind 5900 (CI/CD job request, squatting with
origami74/dvm-cicd-runner) and the standard 6900/7000 result/feedback kinds.

All functions are non-fatal — failures are logged but don't crash the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import _redact, log
from lib.constants import BLOSSOM_SERVERS, NOSTR_RELAYS

KIND_JOB_REQUEST = 5900
KIND_JOB_RESULT = 6900
KIND_JOB_FEEDBACK = 7000

_job_request_event_id: str | None = None
_job_request_event_json: str | None = None


def _get_nsec_file() -> str | None:
    nsec_file = os.environ.get("NSEC_FILE", "")
    if not nsec_file or not Path(nsec_file).exists():
        for candidate in [os.path.expanduser("~/nsec"), "/root/nsec"]:
            if Path(candidate).exists():
                nsec_file = candidate
                break
    if not nsec_file or not Path(nsec_file).exists():
        nsec_hex = os.environ.get("BOT_NSEC_HEX", "")
        if nsec_hex:
            nsec_path = os.path.expanduser("~/nsec")
            Path(nsec_path).write_text(nsec_hex)
            os.environ["NSEC_FILE"] = nsec_path
            return nsec_path
        return None
    return nsec_file


def _relay_args(relays: list[str]) -> str:
    return " ".join(shlex.quote(r) for r in relays)


def _nak_available() -> bool:
    import shutil
    return shutil.which("nak") is not None


def _run_nak(cmd: str, nsec_file: str, timeout: int = 15) -> subprocess.CompletedProcess[str] | None:
    env = os.environ.copy()
    with open(nsec_file) as f:
        env["NOSTR_SECRET_KEY"] = f.read().strip()
    try:
        return subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except Exception as exc:
        log.warning("nak command failed: %s", _redact(str(exc))[:200])
        return None


def publish_job_request(config: WorkerConfig) -> str:
    """Publish kind 5900 job request event.

    Stores the event ID for later linking in feedback/result events.
    Returns the event ID (empty string on failure).
    """
    global _job_request_event_id

    nsec_file = _get_nsec_file()
    if not nsec_file or not _nak_available():
        log.info("DVM job request skipped (no nsec or nak)")
        return ""

    relays = NOSTR_RELAYS
    content = json.dumps({
        "run_id": config.run_id,
        "branch": config.sut_branch,
        "backend": config.backend,
        "scope": "cloud-api",
        "portal": config.portal,
    })

    cmd = (
        f"nak event -k {KIND_JOB_REQUEST} "
        f"-c {shlex.quote(content)} "
        f"-t {shlex.quote('t=ci/cd')} "
        f"-t {shlex.quote('t=tollgate')} "
        f"-t {shlex.quote(f'param=branch;{config.sut_branch}')} "
        f"-t {shlex.quote(f'param=backend;{config.backend}')} "
        f"-t {shlex.quote('param=scope;cloud-api')} "
        f"{_relay_args(relays)}"
    )

    r = _run_nak(cmd, nsec_file)
    if r and r.returncode == 0 and r.stdout:
        for line in reversed(r.stdout.strip().split("\n")):
            line = line.strip()
            if line.startswith("{"):
                event = json.loads(line)
                event_id: str = event.get("id", "")
                _job_request_event_id = event_id
                global _job_request_event_json
                _job_request_event_json = json.dumps(event)
                log.info("DVM job request published (kind=%d, id=%s)", KIND_JOB_REQUEST, event_id[:16])
                return event_id

    return ""


def publish_feedback(status: str, extra_info: str = "") -> None:
    """Publish kind 7000 job feedback event.

    Args:
        status: One of 'processing', 'success', 'error', 'partial'.
        extra_info: Optional human-readable info.
    """
    if not _job_request_event_id or not _nak_available():
        return

    nsec_file = _get_nsec_file()
    if not nsec_file:
        return

    relays = NOSTR_RELAYS
    content = extra_info or ""

    tags = [
        f"-t {shlex.quote(f'status={status}')}",
        f"-t {shlex.quote(f'e={_job_request_event_id}')}",
    ]
    if extra_info:
        tags.append(f"-t {shlex.quote(f'info={extra_info[:200]}')}")

    cmd = (
        f"nak event -k {KIND_JOB_FEEDBACK} "
        f"-c {shlex.quote(content)} "
        f"{' '.join(tags)} "
        f"{_relay_args(relays)}"
    )

    r = _run_nak(cmd, nsec_file)
    if r and r.returncode == 0:
        log.info("DVM feedback published: %s", status)
    else:
        log.warning("DVM feedback failed")


def publish_job_result(config: WorkerConfig, counts: dict[str, Any], result_urls: list[str] | None = None) -> None:
    """Publish kind 6900 job result event.

    Args:
        config: Worker config (run_id, branch, etc.)
        counts: Test result counts (passed, failed, skipped)
        result_urls: List of Blossom URLs for result artifacts.
    """
    if not _job_request_event_id or not _nak_available():
        return

    nsec_file = _get_nsec_file()
    if not nsec_file:
        return

    relays = NOSTR_RELAYS
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)

    content = json.dumps({
        "run_id": config.run_id,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "branch": config.sut_branch,
        "backend": config.backend,
        "commit": config.sut_commit or "",
        "pr": config.sut_pr or "",
        "portal": config.portal,
        "files": result_urls or [],
    })

    tags = [
        f"-t {shlex.quote(f'e={_job_request_event_id}')}",
        f"-t {shlex.quote(f'param=branch;{config.sut_branch}')}",
        f"-t {shlex.quote(f'param=passed;{passed}')}",
        f"-t {shlex.quote(f'param=failed;{failed}')}",
    ]
    if _job_request_event_json:
        tags.append(f"-t {shlex.quote(f'request={_job_request_event_json}')}")
        try:
            req_event = json.loads(_job_request_event_json)
            customer_pubkey = req_event.get("pubkey", "")
            if customer_pubkey:
                tags.append(f"-t {shlex.quote(f'p={customer_pubkey}')}")
        except (json.JSONDecodeError, KeyError):
            pass
    for url in (result_urls or [])[:10]:
        tags.append(f"-t {shlex.quote(f'file={url}')}")

    cmd = (
        f"nak event -k {KIND_JOB_RESULT} "
        f"-c {shlex.quote(content)} "
        f"{' '.join(tags)} "
        f"{_relay_args(relays)}"
    )

    r = _run_nak(cmd, nsec_file)
    if r and r.returncode == 0:
        log.info("DVM job result published (kind=%d, %d passed, %d failed)", KIND_JOB_RESULT, passed, failed)
    else:
        log.warning("DVM job result failed")
