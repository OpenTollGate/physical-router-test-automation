"""Deprecated DVM event publishing — migrated to kind 30078 per ADR-007.

All NIP-90 DVM functions are now no-ops. The pipeline calls them for
backward compatibility but they only log deprecation warnings. Test
evidence is published exclusively via kind 30078 by result_publisher.py.

The dashboard (tests.tollgate.me) fetches kind 30078 as primary.
Legacy DVM events (5900/6900/7000) remain on relays for historical
visibility only — no new DVM events are published.
"""

from __future__ import annotations

import json
from typing import Any

from lib.cloud_lab.worker.config import WorkerConfig
from lib.cloud_lab.worker.shell import log

_job_request_event_id: str | None = None


def _get_nsec_file() -> str | None:
    return None


def publish_job_request(config: WorkerConfig) -> str:
    log.info("DVM publish_job_request deprecated (ADR-007) — kind 30078 used instead")
    return ""


def publish_feedback(status: str, extra_info: str = "") -> None:
    log.info("DVM publish_feedback deprecated (ADR-007) — kind 30078 has no lifecycle")


def publish_job_result(config: WorkerConfig, counts: dict[str, Any], result_files: list[Any] | None = None) -> None:
    log.info("DVM publish_job_result deprecated (ADR-007) — result_publisher.py handles kind 30078")

