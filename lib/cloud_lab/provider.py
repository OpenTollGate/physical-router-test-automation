"""Abstract cloud provider interface for the cloud test lab."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lib.cloud_lab.resolve import RunTarget


class CloudProvider(ABC):
    """Interface that all cloud lab providers must implement.

    Each provider manages VM (or container) lifecycle and worker execution
    for running TollGate test suites in an isolated environment.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short provider identifier (e.g. 'gcp', 'vps')."""

    @abstractmethod
    def vm_up(self, name: str, **kwargs: Any) -> int:
        """Create or start a test runner environment. Returns 0 on success."""

    @abstractmethod
    def vm_down(self, name: str, **kwargs: Any) -> int:
        """Stop and optionally delete the test runner environment."""

    @abstractmethod
    def vm_status(self, name: str, **kwargs: Any) -> str | None:
        """Return the status string of the environment, or None if not found."""

    @abstractmethod
    def vm_external_ip(self, name: str, **kwargs: Any) -> str | None:
        """Return the external IP of the environment, or None."""

    @abstractmethod
    def submit_run(
        self,
        target: RunTarget,
        *,
        publish: bool = False,
        artifact_timeout_s: int = 1800,
        reseller_scenarios: bool = False,
        secondary_router_host: str = "",
        secondary_router_port: str = "",
        keep_vm_on_failure: bool = False,
        **kwargs: Any,
    ) -> dict[str, str]:
        """Pre-flight checks, then launch the autonomous test worker.

        Returns a dict with run metadata (run_id, log_hint, etc.).
        """

    @abstractmethod
    def status_run(self, run_id: str, **kwargs: Any) -> int:
        """Check the status of a submitted run. Returns 0 if found/finished."""

    @abstractmethod
    def cleanup_stale(self, max_age_hours: int = 2, **kwargs: Any) -> int:
        """Clean up stale test environments older than max_age_hours."""

    @abstractmethod
    def cleanup_all(self, **kwargs: Any) -> int:
        """Clean up all test environments regardless of age."""

    @abstractmethod
    def ssh_command(self, name: str, user: str = "root") -> list[str]:
        """Return an argv list for SSHing into the environment."""
