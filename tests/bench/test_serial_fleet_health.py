"""Serial-fleet bench health — prove every enrolled unit is alive and is what it claims.

Data-driven from configs/labgrid/x280-serial-fleet.json (the enrollment
contract). Two legs per unit, because a bench that can only serial is a bench
that cannot ship binaries:

  1. console: FTDI by-id port answers with a shell and reports the contracted
     identity (model / distro / arch / target / hostname),
  2. ssh: the bench publickey works and reports the same identity.

Dark ports (enrolled=false) are asserted *present in the contract* and
reported as skipped — a vanishing port should be a visible decision, not a
silent gap.

Runs only on the bench host: the whole module skips when no FTDI by-id ports
exist (e.g. CI, a laptop, the cloud lab), so this file can live next to the
rest of the suite without breaking it.

Usage:
    ./.venv/bin/python -m pytest tests/bench/test_serial_fleet_health.py -v
    TOLLGATE_BENCH_FLEET=my.json ./scripts/run-local-tests.sh tests/bench/test_serial_fleet_health.py
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLEET_DEFAULT = REPO_ROOT / "configs" / "labgrid" / "x280-serial-fleet.json"
PROBE = REPO_ROOT / "scripts" / "bench" / "serial-fleet-probe.py"

pytestmark = [pytest.mark.physical_hardware, pytest.mark.smoke]

FTDI_GLOB = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_*-if00-port0"


def _fleet_path() -> Path:
    return Path(os.environ.get("TOLLGATE_BENCH_FLEET", FLEET_DEFAULT))


def _venv_python() -> str:
    override = os.environ.get("TOLLGATE_PYTHON_VENV", "")
    if override:
        candidate = Path(override).expanduser() / "bin" / "python3"
        if candidate.is_file():
            return str(candidate)
    return sys.executable


@pytest.fixture(scope="module")
def fleet():
    path = _fleet_path()
    if not path.is_file():
        pytest.skip(f"bench fleet contract missing: {path}")
    if not glob.glob(FTDI_GLOB):
        pytest.skip("no FTDI consoles on this host — not the serial-fleet bench")
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def probe_report(fleet):
    """One probe run for the whole module; per-unit tests read from it."""
    r = subprocess.run(
        [ _venv_python(), str(PROBE), "--json", "--fleet", str(_fleet_path())],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode not in (0, 1):  # probe crashes, not fails
        pytest.fail(f"probe crashed rc={r.returncode}: {r.stderr[-400:]}")
    return json.loads(r.stdout)


def test_contract_shape(fleet):
    units = fleet["units"]
    assert units, "empty fleet contract"
    for u in units:
        assert "name" in u and "ftdi_serial" in u, f"unit missing name/ftdi_serial: {u}"
        if u.get("enrolled", True):
            assert u.get("identity"), f"{u['name']}: enrolled unit has no identity contract"
            assert (u.get("ssh") or {}).get("address"), f"{u['name']}: enrolled unit has no ssh address"


def test_dark_ports_are_recorded(fleet, probe_report):
    """A dark port must stay in the contract as an explicit decision."""
    dark = {u["name"] for u in fleet["units"] if not u.get("enrolled", True)}
    reported = {u["unit"] for u in probe_report["units"] if not u.get("enrolled", True)}
    assert dark == reported, f"contract/probe disagree on dark units: {dark} vs {reported}"


@pytest.mark.parametrize("unit_name", [
    u["name"] for u in json.loads(FLEET_DEFAULT.read_text())["units"]
    if u.get("enrolled", True)
] if FLEET_DEFAULT.is_file() else [])
def test_unit_console_and_ssh(fleet, probe_report, unit_name):
    entry = next(u for u in probe_report["units"] if u["unit"] == unit_name)
    assert entry.get("console_ok"), f"{unit_name}: console failed: {entry.get('console_error')}"
    assert entry.get("console_identity") == "identity ok", \
        f"{unit_name}: console identity: {entry.get('console_identity')}"
    assert entry.get("ssh_ok"), f"{unit_name}: ssh failed: {entry.get('ssh_error')}"
    assert entry.get("ssh_identity") == "identity ok", \
        f"{unit_name}: ssh identity: {entry.get('ssh_identity')}"