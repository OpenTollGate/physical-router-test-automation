"""Shared helpers for reseller-mode tests.

The helpers in this module intentionally use only SSH/UCI/CLI operations so
they work against physical routers, the local QEMU virtual lab, and the GCP
cloud lab. WiFi scan/association checks belong in physical-only tests.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager

from lib.router import Router


RESELLER_UCI_KEY = "tollgate.config.reseller_mode"


def get_reseller_mode(router: Router) -> str:
    """Return the raw reseller_mode UCI value, defaulting to "0"."""
    value = router.ssh(f"uci get {RESELLER_UCI_KEY} 2>/dev/null || echo 0", timeout=10)
    return value.strip() or "0"


def is_reseller_mode_enabled(router: Router) -> bool:
    return get_reseller_mode(router) in {"1", "true", "yes", "on"}


def set_reseller_mode(router: Router, enabled: bool, restart: bool = True) -> None:
    value = "1" if enabled else "0"
    router.ssh(
        f"uci set {RESELLER_UCI_KEY}={value}; uci commit tollgate",
        timeout=15,
    )
    if restart:
        router.restart_backend(timeout=45)


@contextmanager
def reseller_mode(router: Router, enabled: bool = True, restart: bool = True) -> Iterator[None]:
    """Temporarily set reseller_mode and restore the previous value."""
    previous = get_reseller_mode(router)
    set_reseller_mode(router, enabled=enabled, restart=restart)
    try:
        yield
    finally:
        router.ssh(
            f"uci set {RESELLER_UCI_KEY}={previous}; uci commit tollgate",
            timeout=15,
        )
        if restart:
            router.restart_backend(timeout=45)


def get_status_text(router: Router) -> str:
    """Return status output serialized to lowercase text for feature checks."""
    try:
        status = router.get_tollgate_status()
    except Exception:
        return ""
    return json.dumps(status, sort_keys=True).lower()


def has_degraded_mode_support(router: Router) -> bool:
    raw = get_status_text(router)
    return any(token in raw for token in ("degraded", "reachable", "mint_health"))


def block_host_via_hosts(router: Router, hostname: str) -> None:
    command = (
        f"grep -q '0.0.0.0 {hostname}' /etc/hosts 2>/dev/null || "
        f"echo '0.0.0.0 {hostname}' >> /etc/hosts"
    )
    router.ssh(command, timeout=10)


def unblock_host_via_hosts(router: Router, hostname: str) -> None:
    router.ssh(f"sed -i '/0.0.0.0 {hostname}/d' /etc/hosts", timeout=10)


def restart_and_wait(router: Router, settle_seconds: int = 8) -> None:
    router.restart_backend(timeout=45)
    time.sleep(settle_seconds)


def wait_for_status_without(router: Router, forbidden: str, timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = get_status_text(router)
        if raw and forbidden.lower() not in raw:
            return True
        time.sleep(3)
    return False
