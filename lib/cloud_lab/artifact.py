"""CI artifact pre-flight for cloud lab submit."""

from __future__ import annotations

from lib.cloud_lab.constants import CLOUD_ARCH
from lib.cloud_lab.resolve import RunTarget
from lib.deploy import ensure_artifact


def ensure_target_artifact(target: RunTarget, timeout_s: int = 1800) -> str:
    """Block until upstream CI has a downloadable artifact. Returns run_id."""
    return ensure_artifact(
        branch=target.branch,
        arch=CLOUD_ARCH,
        repo=target.repo,
        workflow=target.workflow,
        commit=target.sut_commit or None,
        timeout_s=timeout_s,
    )
