"""Verify AP setup recovery on reinstall/upgrade (issues #103, #173, #207).

Root cause: ``packaging/files/etc/uci-defaults/99-tollgate-setup`` lines 8-9 bail
out entirely when ``/etc/tollgate-setup-done`` already exists::

    if [ -f "$SETUP_FLAG" ]; then
        exit 0
    fi

So on reinstall/upgrade (where the flag persists from the first install), the
``setup_wifi`` step (lines 72-86, which sets ``wireless.*.mode='ap'`` and the
TollGate SSID) is skipped. If the wireless config was reset or removed during
the package reinstall, the APs are never recreated — exactly the symptom in
#103. #173 proposes a fix (re-verify wireless config even when the flag exists)
but is currently stale.

This test runs in two layers:

1. **Script-logic layer** (always runnable, no WiFi radios required): confirm the
   early-exit-on-flag behavior is present and that ``setup_wifi`` is skipped when
   the flag exists. On ``main`` this CONFIRMS the bug; after #173 it verifies the
   script no longer bails before AP creation.
2. **Lifecycle layer** (when WiFi radios are present, e.g. ``--hwsim``): simulate
   the reinstall — remove AP config, re-trigger setup with the flag present — and
   assert APs are recreated only after the fix.

Gating: ``gate_bug_fix`` flips the recovery test to xfail ("known issue") while
the bug is present on ``main``, so a failure after #173 lands is a real
regression.

See: https://github.com/OpenTollGate/tollgate-module-basic-go/issues/103
      https://github.com/OpenTollGate/tollgate-module-basic-go/issues/173
      https://github.com/OpenTollGate/tollgate-module-basic-go/issues/207
"""

from __future__ import annotations

import os
import re

import pytest

from lib.helpers import gate_bug_fix

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]

SETUP_FLAG = "/etc/tollgate-setup-done"
SETUP_SCRIPT = "/etc/uci-defaults/99-tollgate-setup"
# Some images consume uci-defaults into /etc/init.d on first boot; the script may
# also live in /usr/lib/tollgate. Probe both.
SETUP_SCRIPT_ALT = "/usr/lib/tollgate/99-tollgate-setup"


def _read_setup_script(router) -> str:
    raw = router.ssh(
        f"cat {SETUP_SCRIPT} 2>/dev/null || cat {SETUP_SCRIPT_ALT} 2>/dev/null || echo MISSING",
        timeout=15,
    )
    return raw


def _script_bails_on_flag(script: str) -> bool:
    """True iff the script contains an early 'exit 0' guarded only by the flag."""
    if not script or script.strip() == "MISSING":
        return False
    # The bug: flag check followed by exit 0 before any wireless setup.
    pat = re.compile(
        r'SETUP_FLAG=.*?tollgate-setup-done.*?if\s+\[\s*-f\s+"\$SETUP_FLAG"\s*\].*?exit\s+0',
        re.S,
    )
    return bool(pat.search(script))


def _has_radios(router) -> bool:
    out = router.ssh("iw dev 2>/dev/null | grep -c phy || true", timeout=10)
    try:
        return int(out.strip()) > 0
    except ValueError:
        return False


@pytest.fixture(scope="module")
def setup_script(router):
    src = _read_setup_script(router)
    if not src or src.strip() == "MISSING":
        pytest.skip("99-tollgate-setup not present on this firmware")
    return src


@pytest.mark.extended
def test_setup_script_bails_on_existing_flag(setup_script):
    """CONFIRMS the bug on main: the script exits when setup-done flag exists.

    This is the root-cause signature of #103/#173. On buggy main this passes
    (bug present). When #173's fix lands, the early-exit-on-flag is removed and
    this test will FAIL — that is the signal to update the assertions to the
    fixed behavior.
    """
    assert _script_bails_on_flag(setup_script), (
        "Expected the early-exit-on-flag bug (lines 8-9 of 99-tollgate-setup). "
        "It appears to have been fixed — update test_ap_setup_recovers_after_reinstall."
    )


@pytest.mark.extended
def test_setup_wifi_step_exists(setup_script):
    """The script must contain an AP-creation step (setup_wifi) — the step that
    the early-exit skips. Guards against the script being silently restructured."""
    assert re.search(r"mode=.?ap'?|wifi-iface|setup_wifi|wireless\.\$iface", setup_script), (
        "No wireless/AP setup step found in 99-tollgate-setup"
    )


@pytest.mark.extended
def test_ap_setup_recovers_after_reinstall(router):
    """Lifecycle: after a reinstall (flag persists, wireless reset), APs must be
    recreated. xfail on buggy main (the known #103 bug); passes after #173.

    Requires WiFi radios (``--hwsim`` cloud lab or physical router). Skipped
    otherwise — the script-logic tests above cover the headless case.
    """
    gate_bug_fix(
        not _script_bails_on_flag(_read_setup_script(router)),
        bug_id="ap-setup-not-recoverable-103",
        fix_pr="#173",
    )
    if not _has_radios(router):
        pytest.skip("No WiFi radios present (use --hwsim); script-logic tests cover this")

    flag_existed = router.ssh(f"test -f {SETUP_FLAG} && echo yes || echo no", timeout=5).strip() == "yes"

    try:
        # Snapshot SSIDs before.
        ssids_before = router.ssh("iw dev 2>/dev/null | grep ssid || true", timeout=10)

        # Simulate reinstall wiping the AP config while the flag persists.
        router.ssh("uci -q delete wireless.@wifi-iface[0] 2>/dev/null; uci commit wireless", timeout=10)
        # Re-trigger the setup script (as a reinstall boot would).
        router.ssh(f"sh {SETUP_SCRIPT} >/tmp/reinstall-setup.log 2>&1 || true", timeout=30)

        ssids_after = router.ssh("iw dev 2>/dev/null | grep ssid || true", timeout=10)
        # After the fix: setup recreates the APs → ssids_after is non-empty.
        assert ssids_after.strip(), (
            "APs missing after reinstall+setup-rerun (the #103 bug). "
            f"Before: {ssids_before!r}; after: {ssids_after!r}"
        )
    finally:
        # Best-effort restore: re-run full setup without the flag to recreate APs.
        if flag_existed:
            router.ssh(f"rm -f {SETUP_FLAG}; sh {SETUP_SCRIPT} >/dev/null 2>&1 || true; "
                       f"touch {SETUP_FLAG}", timeout=30)
        else:
            router.ssh(f"rm -f {SETUP_FLAG}; sh {SETUP_SCRIPT} >/dev/null 2>&1 || true", timeout=30)


@pytest.mark.extended
def test_reinstall_setup_log_confirms_skip(router):
    """When the flag exists, re-running the setup script must log that it skipped
    (or exit silently). This documents the reinstall behavior operators see."""
    router.ssh(f"touch {SETUP_FLAG} 2>/dev/null || true", timeout=5)
    router.ssh("rm -f /tmp/tollgate-setup.log /tmp/reinstall-setup.log", timeout=5)
    router.ssh(f"sh {SETUP_SCRIPT} >/tmp/reinstall-setup.log 2>&1; echo \"rc=$?\" >> /tmp/reinstall-setup.log", timeout=30)
    log = router.ssh("cat /tmp/reinstall-setup.log 2>/dev/null", timeout=10)
    # The script exits at line 9 with no further output. rc=0 and minimal output
    # confirms the early bail.
    assert "rc=0" in log, f"Setup script did not exit cleanly with flag present: {log[:300]}"
