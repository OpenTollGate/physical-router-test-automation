"""Post-test fleet reset: conwrt firmware reflash OR firstboot state reset.

Firmware mode (``TOLLGATE_REFLASH_IMAGE`` set) flashes a specific ``.bin`` to
each router via conwrt's ``_flash_via_sysupgrade``. State-reset mode (no image)
calls ``lib.deploy.firstboot_reset`` — faster, no conwrt dependency, sufficient
when the next session's ``--tollgate-factory-reset`` redeploy would clean up.

Disabled by default; enable via ``--post-test-reflash`` or
``TOLLGATE_POST_TEST_REFLASH=1``. Replaces the deleted
``post_test_image_flasher`` from tollgate-module-basic-go; closes #45.
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
    reflashed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    image_missing: bool = False
    conwrt_unavailable: bool = False
    method: str = ""


def _import_conwrt(conwrt_dir: str):
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
    reset_fn=None,
):
    """Return every router in the fleet to a known-clean state.

    Firmware reflash when ``firmware_image`` is set (requires conwrt);
    otherwise firstboot state reset via ``lib.deploy.firstboot_reset``.
    """
    result = ReflashResult()

    if not enable:
        result.disabled = True
        return result

    use_firmware = bool(firmware_image)
    result.method = "firmware" if use_firmware else "reset"

    if use_firmware:
        if not os.path.isfile(firmware_image):
            result.image_missing = True
            log.error("firmware image not found: %r", firmware_image)
            return result
        if flash_fn is None or reboot_wait_fn is None:
            if not os.path.isdir(os.path.join(conwrt_dir, "scripts")):
                result.conwrt_unavailable = True
                log.error(
                    "firmware reflash requested but conwrt not found at %s; "
                    "clone Amperstrand/conwrt or set CONWRT_DIR, or omit "
                    "TOLLGATE_REFLASH_IMAGE to use firstboot reset instead",
                    conwrt_dir,
                )
                return result
            try:
                flash_fn, reboot_wait_fn = _import_conwrt(conwrt_dir)
            except ImportError as exc:
                result.conwrt_unavailable = True
                log.error("failed to import conwrt.flash_utils: %s", exc)
                return result
        log.info("=== POST-SESSION FIRMWARE REFLASH via conwrt ===")
    else:
        if reset_fn is None:
            from lib import deploy as deploy_lib
            reset_fn = deploy_lib.firstboot_reset
        log.info("=== POST-SESSION STATE RESET via firstboot_reset ===")

    log.info("processing %d routers (method=%s)", len(routers), result.method)

    for router_id, router in routers.items():
        try:
            if use_firmware:
                host = getattr(router, "host", None)
                if not host:
                    result.failed[router_id] = "router has no 'host' attribute"
                    log.error("cannot reflash %s: no host attribute", router_id)
                    continue
                log.info("reflashing %s (%s)", router_id, host)
                flashed = flash_fn(host, firmware_image)
                if not flashed:
                    result.failed[router_id] = "conwrt _flash_via_sysupgrade returned False"
                    log.error("reflash reported failure for %s", router_id)
                    continue
                reboot_wait_fn(host, timeout=_REFLASH_REBOOT_TIMEOUT_SECONDS)
            else:
                log.info("resetting %s", router_id)
                reset_fn(router)
            result.reflashed.append(router_id)
            log.info("%s %s succeeded", result.method, router_id)
        except Exception as exc:  # noqa: BLE001 — per-router isolation
            result.failed[router_id] = str(exc)
            log.error("failed to %s %s: %s", result.method, router_id, exc)

    log.info(
        "fleet %s complete: %d succeeded, %d failed",
        result.method,
        len(result.reflashed),
        len(result.failed),
    )
    return result
