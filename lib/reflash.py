"""Post-test fleet reflash via conwrt.

After a test session completes, reflashes every router in the fleet to a
known-clean firmware state by calling conwrt's flashing API. Replaces the
deleted ``post_test_image_flasher`` fixture from ``tollgate-module-basic-go``
and maps to physical-router-test-automation issue #45 (repeatable upgrade
test framework).

Safety: disabled by default. Enable via the ``--post-test-reflash`` pytest
option or ``TOLLGATE_POST_TEST_REFLASH=1`` env var. Requires
``TOLLGATE_REFLASH_IMAGE`` to point at the firmware image file.
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field

log = logging.getLogger("tollgate.reflash")

CONWRT_DIR = os.environ.get("CONWRT_DIR", "/opt/conwrt")
_REFLASH_REBOOT_TIMEOUT_SECONDS = 180


@dataclass
class ReflashResult:
    """Outcome of a fleet reflash operation."""

    reflashed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    image_missing: bool = False
    conwrt_unavailable: bool = False


def _import_conwrt(conwrt_dir: str):
    """Import conwrt's flashing helpers, adding scripts/ to sys.path.

    Returns a (flash_fn, reboot_wait_fn) tuple. Raises ImportError if conwrt
    is not importable.
    """
    scripts_dir = os.path.join(conwrt_dir, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from conwrt.flash_utils import (  # type: ignore[import-not-found]
        _flash_via_sysupgrade,
        _wait_for_sysupgrade_reboot,
    )
    return _flash_via_sysupgrade, _wait_for_sysupgrade_reboot


def reflash_fleet(
    routers,
    firmware_image,
    *,
    enable=False,
    conwrt_dir=CONWRT_DIR,
    flash_fn=None,
    reboot_wait_fn=None,
):
    """Reflash all routers in the fleet to a known-clean firmware state.

    Args:
        routers: ``{router_id: Router}`` dict (e.g. from the ``all_routers``
            pytest fixture). Each ``Router`` must expose a ``host`` attribute.
        firmware_image: filesystem path to the firmware ``.bin``/``.img``.
        enable: must be ``True`` to actually flash. Default ``False`` is a
            safe no-op so the fixture cannot accidentally reflash routers.
        conwrt_dir: path to the conwrt checkout. Defaults to ``$CONWRT_DIR``
            or ``/opt/conwrt``.
        flash_fn: override for conwrt's ``_flash_via_sysupgrade`` (testing).
        reboot_wait_fn: override for ``_wait_for_sysupgrade_reboot`` (testing).

    Returns:
        ``ReflashResult`` describing what happened. Per-router failures are
        recorded individually; one router failing never blocks the others.
    """
    result = ReflashResult()

    if not enable:
        result.disabled = True
        return result

    if not firmware_image or not os.path.isfile(firmware_image):
        result.image_missing = True
        log.error(
            "post-test reflash enabled but firmware image missing or not found: %r",
            firmware_image,
        )
        return result

    if flash_fn is None or reboot_wait_fn is None:
        if not os.path.isdir(os.path.join(conwrt_dir, "scripts")):
            result.conwrt_unavailable = True
            log.error(
                "post-test reflash enabled but conwrt not found at %s. "
                "Clone Amperstrand/conwrt there or set CONWRT_DIR.",
                conwrt_dir,
            )
            return result
        try:
            flash_fn, reboot_wait_fn = _import_conwrt(conwrt_dir)
        except ImportError as exc:
            result.conwrt_unavailable = True
            log.error("failed to import conwrt.flash_utils: %s", exc)
            return result

    log.info("=== POST-TEST FLEET REFLASH ===")
    log.info("reflashing %d routers with %s", len(routers), firmware_image)

    for router_id, router in routers.items():
        host = getattr(router, "host", None)
        if not host:
            result.failed[router_id] = "router has no 'host' attribute"
            log.error("cannot reflash %s: no host attribute", router_id)
            continue
        try:
            log.info("reflashing %s (%s)", router_id, host)
            flashed = flash_fn(host, firmware_image)
            if not flashed:
                result.failed[router_id] = "conwrt _flash_via_sysupgrade returned False"
                log.error("reflash reported failure for %s", router_id)
                continue
            reboot_wait_fn(host, timeout=_REFLASH_REBOOT_TIMEOUT_SECONDS)
            result.reflashed.append(router_id)
            log.info("reflashed %s successfully", router_id)
        except Exception as exc:  # noqa: BLE001 — per-router isolation
            result.failed[router_id] = str(exc)
            log.error("failed to reflash %s: %s", router_id, exc)

    log.info(
        "fleet reflash complete: %d reflashed, %d failed",
        len(result.reflashed),
        len(result.failed),
    )
    return result
