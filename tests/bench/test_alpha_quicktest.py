"""Tests for ``scripts/bench/alpha-quicktest.sh`` — the one-command
quick-test cycle entry point for the COVR-X1860 alpha rig (tollgate-AFA5,
the wired ALPHA seat). See ``docs/E2E-TIMINGS.md`` for the cycle anatomy
and measured timings.

Two layers:

* **Hardware-free unit tests** (this file's first section) run in CI:
  the script parses (``bash -n``), its usage contract holds, and it
  carries no hardcoded passwords/secrets. They only exercise
  exit-before-side-effects paths — ``usage()`` is reached before the
  lock, the result dir, or any SSH.
* **Hardware-gated integration tests** (second section) actually run
  the cycle against the physical bench. They are an A/B pair:

  - ``reinstall`` — the full flash-equivalent cycle: ``apk del`` +
    ``apk add`` of the verified feed artifact (this rig's "flash"),
    measuring the from-scratch install path (~2.2 min/cycle).
  - ``steady`` — same cycle with ``--no-reinstall``: acquire + verify +
    setup + test over the already-running install, measuring the
    cheap re-test path (~64 s/cycle measured).

  Both assert the machine-readable ``result.json`` carries all five
  phases (acquire/install/setup/test/teardown) as positive numbers and
  that the payment transcript shows a ``kind:1022`` success notice.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "bench" / "alpha-quicktest.sh"

# Usage-error exit code from the script header: "Exit: 0 ok; 2 usage".
USAGE_EXIT = 2

# Variable assignments whose name contains "pass" (PASS=, PASSWORD=,
# WPA_PASS=, passwd=, …) — a hardcoded credential shape.
SECRET_ASSIGNMENT_RE = re.compile(r"(?im)^\s*(?:export\s+)?\w*pass\w*\s*=\s*\S")

# The five phases the cycle's summary python writes into result.json.
PHASES = ("acquire", "install", "setup", "test", "teardown")


# ---------------------------------------------------------------------------
# hardware-free unit tests (CI-safe)
# ---------------------------------------------------------------------------


def test_script_passes_bash_syntax_check():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_help_prints_usage_mentioning_release(tmp_path):
    """--help prints the header usage block.

    The script's ``usage()`` prints the usage and exits 2 for ``--help``
    exactly as for a usage error (header contract: "Exit: 0 ok; 2 usage"),
    so the pinned exit code is 2, not 0.
    """
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == USAGE_EXIT
    assert "--release" in result.stdout
    assert "--no-reinstall" in result.stdout


def test_no_args_is_a_usage_error(tmp_path):
    """No arguments (missing mandatory --release) exits 2 with usage."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == USAGE_EXIT
    assert "Usage" in result.stdout
    assert "--release" in result.stdout


def test_script_contains_no_hardcoded_passwords():
    """No PASS-shaped assignments and no benchpw default anywhere.

    Credentials doctrine (AGENTS.md): secrets never live in tracked
    files — the rig authenticates via the operator's ssh key
    (ALPHA_SMALL_KEY), not an embedded password.
    """
    text = SCRIPT.read_text()
    offenders = SECRET_ASSIGNMENT_RE.findall(text)
    assert offenders == []
    assert "benchpw" not in text.lower()


# ---------------------------------------------------------------------------
# hardware-gated integration tests (physical bench — do NOT run in CI)
# ---------------------------------------------------------------------------

_QUICKTEST_ENABLED = os.environ.get("PRTA_ALPHA_QUICKTEST", "") == "1"

# NOTE the double gate: this module is marked ``rust_only`` and the repo's
# conftest skips rust_only tests unless TOLLGATE_BACKEND=rust (the default
# is "go"). Running the A/B pair therefore needs BOTH env vars:
#   PRTA_ALPHA_QUICKTEST=1 TOLLGATE_BACKEND=rust pytest tests/bench/...


@pytest.mark.skipif(
    not _QUICKTEST_ENABLED,
    reason="physical bench only: set PRTA_ALPHA_QUICKTEST=1 to run the "
    "alpha-quicktest cycle against the COVR-X1860 alpha rig (tollgate-AFA5)",
)
@pytest.mark.slow
@pytest.mark.rust_only
@pytest.mark.timeout(900)
@pytest.mark.parametrize("mode", ["reinstall", "steady"])
def test_alpha_quicktest_cycle(mode, tmp_path):
    """Run the full quick-test cycle on the physical alpha rig.

    ``reinstall`` measures the flash-equivalent path (full ``apk del`` +
    ``apk add`` reinstall of the feed artifact); ``steady`` adds
    ``--no-reinstall`` and measures the re-test path over the running
    install. See the module docstring and docs/E2E-TIMINGS.md.
    """
    release = os.environ.get("ALPHA_QUICKTEST_RELEASE", "v0.6.0-rc6")
    result_dir = tmp_path / "result"
    cmd = [
        "bash",
        str(SCRIPT),
        "--release",
        release,
        "--result",
        str(result_dir),
    ]
    if mode == "steady":
        cmd.append("--no-reinstall")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=840,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    runs = sorted(result_dir.glob("run-*/result.json"))
    assert runs, f"no result.json written under {result_dir}"
    run_dir = runs[-1].parent

    data = json.loads((run_dir / "result.json").read_text())
    phases = data["phases"]
    assert set(phases) == set(PHASES)
    for phase in PHASES:
        assert isinstance(phases[phase], (int, float)), f"{phase}={phases[phase]!r}"
        assert phases[phase] > 0, f"{phase}={phases[phase]!r}"

    payment = run_dir / "payment.out"
    assert payment.exists(), f"no payment.out in {run_dir}"
    # the script's own success gate greps the JSON-serialized notice
    assert '"kind":1022' in payment.read_text()
