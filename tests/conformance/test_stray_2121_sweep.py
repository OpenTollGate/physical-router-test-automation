"""O3 rail: a stray :2121 holder cannot outlive a service restart.

Mirror of Amperstrand/tollgate-module-basic-go PR #98's escalation test
(packaging/tests/initd_stop_test.sh), asserted at PRTA level against the
vendored init script (tests/conformance/tbmg/init.d.tollgate-wrt — byte
copy of the fork's, see PROVENANCE.md). With TBMG_CHECKOUT set, the
vendored copy is also diffed against the live fork file so drift fails
loudly here instead of silently in production packaging.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.failure, pytest.mark.story("O3"), pytest.mark.api, pytest.mark.extended]

HERE = Path(__file__).parent
VENDORED = HERE / "tbmg" / "init.d.tollgate-wrt"

RC_COMMON = """#!/bin/sh
# test harness standing in for /etc/rc.common
. "$1"
kill()  { "$BIN/kill"  "$@"; }
sleep() { "$BIN/sleep" "$@"; }
stop_service
"""

PGREP = """#!/bin/sh
[ -f "$(dirname "$0")/../stray.alive" ] && echo 4242
exit 0
"""

KILL = """#!/bin/sh
log="$(dirname "$0")/../kill.log"
sig=TERM
case "$1" in
    -*) sig="${1#-}"; shift ;;
esac
for pid in "$@"; do
    echo "$sig $pid" >> "$log"
done
if [ "$sig" = "KILL" ] || [ "$sig" = "9" ]; then
    rm -f "$(dirname "$0")/../stray.alive"
fi
exit 0
"""

SLEEP = """#!/bin/sh
exit 0
"""


def _build_stub_env(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("pgrep", PGREP), ("kill", KILL), ("sleep", SLEEP)):
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    rc = tmp_path / "rc.common"
    rc.write_text(RC_COMMON)
    (tmp_path / "stray.alive").touch()
    return tmp_path


def _run_stop_service(tmp_path: Path) -> None:
    stubs = str(tmp_path / "bin")
    env = dict(os.environ, BIN=stubs, PATH=stubs + os.pathsep + os.environ["PATH"])
    subprocess.run(
        ["sh", str(tmp_path / "rc.common"), str(VENDORED)],
        env=env, capture_output=True, timeout=30,
    )


def test_static_contract_stop_service_defined():
    assert any(
        line.startswith("stop_service()")
        for line in VENDORED.read_text().splitlines()
    ), "procd calls stop_service() before terminating its instances; without it there is no place to sweep strays"


def test_static_contract_stop_not_overridden():
    assert not any(
        line.startswith("stop()")
        for line in VENDORED.read_text().splitlines()
    ), "an overridden stop() replaces rc.common's procd_kill teardown (tbmg issue #27 root cause)"


def test_static_contract_pidfile_registered():
    assert "procd_set_param pidfile" in VENDORED.read_text()


def test_stop_service_sweeps_term_immune_stray(tmp_path):
    tmp = _build_stub_env(tmp_path)
    _run_stop_service(tmp)

    kill_log = (tmp / "kill.log").read_text().splitlines()
    assert "TERM 4242" in kill_log, f"no SIGTERM to the stray: {kill_log}"
    assert any(line in ("KILL 4242", "9 4242") for line in kill_log), (
        f"a TERM-immune stray must be SIGKILLed: {kill_log}"
    )
    assert not (tmp / "stray.alive").exists(), "stray outlived stop_service"


def _checkout() -> Path | None:
    env = os.environ.get("TBMG_CHECKOUT", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    default = Path.home() / "src/tollgate-module-basic-go"
    marker = default / "packaging/files/etc/init.d/tollgate-wrt"
    return default if marker.exists() else None


@pytest.mark.skipif(_checkout() is None, reason="no tbmg checkout (TBMG_CHECKOUT or ~/src default)")
def test_vendored_init_script_has_not_drifted(tmp_path):
    # #301: this rail had never run in an executed lane; its first run
    # (2026-11-25) caught the vendored script matching NO reachable ref —
    # an off-branch assembly. The source is now PINNED to the exact SHA
    # (PROVENANCE.md); a moving-ref comparison could not fail loudly
    # about its own ambiguity. Skips where the pinned object is absent
    # (host without the tbmg checkout / PR ref fetched).
    pin = "b25d92ae"  # OpenTollGate/tollgate-module-basic-go PR #1250 head
    have = subprocess.run(
        ["git", "-C", str(_checkout()), "cat-file", "-e", f"{pin}^{{tree}}"],
        capture_output=True, timeout=30,
    )
    if have.returncode != 0:
        pytest.skip(f"checkout lacks the pinned object {pin} — fetch origin pull/1250/head:pr-1250")
    source = subprocess.run(
        ["git", "-C", str(_checkout()), "show", f"{pin}:packaging/files/etc/init.d/tollgate-wrt"],
        capture_output=True, text=True, timeout=30,
    )
    if source.returncode != 0:
        pytest.skip(f"pinned ref {pin} lacks the init script path")
    assert source.stdout == VENDORED.read_text(), (
        f"vendored init.d.tollgate-wrt differs from the pinned source {pin} — "
        "refresh it (see tests/conformance/tbmg/PROVENANCE.md)"
    )
