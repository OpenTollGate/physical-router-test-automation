"""DVM event publishing for the cloud lab pipeline.

Publishes NIP-90 DVM lifecycle events using nostr_publisher._publish_event().
Events use kind 5900 (CI/CD) and the standard 6900/7000 result/feedback kinds.

All functions are non-fatal — failures are logged but don't crash the pipeline.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import log
from lib.constants import NOSTR_RELAYS
from lib.nostr_publisher import _publish_event

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


def publish_job_request(config: WorkerConfig) -> str:
    global _job_request_event_id, _job_request_event_json

    nsec_file = _get_nsec_file()
    if not nsec_file:
        log.info("DVM job request skipped (no nsec)")
        return ""

    content = json.dumps({
        "run_id": config.run_id,
        "branch": config.sut_branch,
        "backend": config.backend,
        "scope": "cloud-api",
        "portal": config.portal,
    })

    tags = [
        ["t", "ci/cd"],
        ["t", "tollgate"],
        ["param", "branch", config.sut_branch],
        ["param", "backend", config.backend],
        ["param", "scope", "cloud-api"],
    ]

    result = _publish_event(nsec_file, KIND_JOB_REQUEST, content, tags, NOSTR_RELAYS)

    if result.get("success"):
        event = result.get("event", {})
        event_id = event.get("id", "")
        _job_request_event_id = event_id
        _job_request_event_json = json.dumps(event)
        log.info("DVM job request published (kind=%d, id=%s)", KIND_JOB_REQUEST, event_id[:16])
        return event_id

    log.warning("DVM job request failed: %s", result.get("error", "unknown")[:200])
    return ""


def publish_feedback(status: str, extra_info: str = "") -> None:
    if not _job_request_event_id:
        return

    nsec_file = _get_nsec_file()
    if not nsec_file:
        return

    tags = [
        ["status", status],
        ["e", _job_request_event_id],
    ]
    if extra_info:
        tags.append(["info", extra_info[:200]])

    result = _publish_event(nsec_file, KIND_JOB_FEEDBACK, extra_info or "", tags, NOSTR_RELAYS)

    if result.get("success"):
        log.info("DVM feedback published: %s", status)
    else:
        log.warning("DVM feedback failed: %s", result.get("error", "unknown")[:200])


def publish_job_result(config: WorkerConfig, counts: dict[str, Any], result_files: list[Any] | None = None) -> None:
    if not _job_request_event_id:
        return

    nsec_file = _get_nsec_file()
    if not nsec_file:
        return

    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)

    files = result_files or []
    file_urls = [f["url"] if isinstance(f, dict) else str(f) for f in files]

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
        "files": files,
    })

    tags: list[list[str]] = [
        ["e", _job_request_event_id],
        ["param", "branch", config.sut_branch],
        ["param", "passed", str(passed)],
        ["param", "failed", str(failed)],
    ]

    if _job_request_event_json:
        tags.append(["request", _job_request_event_json])
        try:
            req_event = json.loads(_job_request_event_json)
            customer_pubkey = req_event.get("pubkey", "")
            if customer_pubkey:
                tags.append(["p", customer_pubkey])
        except (json.JSONDecodeError, KeyError):
            pass

    for url in file_urls[:10]:
        tags.append(["file", url])

    result = _publish_event(nsec_file, KIND_JOB_RESULT, content, tags, NOSTR_RELAYS)

    if result.get("success"):
        log.info("DVM job result published (kind=%d, %d passed, %d failed, %d files)", KIND_JOB_RESULT, passed, failed, len(files))
    else:
        log.warning("DVM job result failed: %s", result.get("error", "unknown")[:200])
