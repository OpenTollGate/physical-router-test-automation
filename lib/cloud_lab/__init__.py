"""Multi-provider cloud test lab (GCP, VPS)."""

from lib.cloud_lab.provider import CloudProvider
from lib.cloud_lab.resolve import RunTarget, resolve_target

__all__ = ["CloudProvider", "RunTarget", "resolve_target"]
