#!/usr/bin/env python3
"""End-to-end proof of the requirement-2 handoff seam, without a bench.

Runs REAL pytest sessions (subprocesses, real ``tests/conftest.py``, real
``router`` fixture) that import a throwaway unit test, and captures two facts
per session:

* the process exit code (``4`` = the seam refused, per ``pytest.exit``);
* whether the ``venue_mutex`` seam was ever entered — recorded by a sitecustomize
  shim that wraps ``lib.labgrid_venue.venue_mutex`` and writes a marker file,
  so "refused BEFORE acquiring" is measured, not inferred from a log line.

No coordinator, no ``labgrid-client`` binary, no place: ``plan_venue`` is
patched to return the plan printed below, which is what the real planner would
return for the given inventory/env.

Usage: PYTHONPATH= python3 evidence/prta-claim-handoff/handoff-probe.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SHIM = textwrap.dedent(
    '''
    """sitecustomize: wrap the venue acquisition seam so a probe can see it."""
    import os

    MARKER = os.environ.get("PROBE_ACQUIRE_MARKER")

    if MARKER:
        def _install():
            try:
                import lib.labgrid_venue as venue
            except Exception:
                return
            original = venue.venue_mutex

            # A context manager that records the acquisition before delegating.
            class _Spy:
                def __init__(self, binding, phase, file_lock=True):
                    self._inner = original(binding, phase, file_lock=file_lock)

                def __enter__(self):
                    with open(MARKER, "a", encoding="utf-8") as fh:
                        fh.write(f"{self._inner}\\n")
                    return self._inner.__enter__()

                def __exit__(self, *exc):
                    return self._inner.__exit__(*exc)

            venue.venue_mutex = _Spy

        # conftest imports the venue module at fixture time, so install lazily
        # on the first import of lib.labgrid_venue rather than at startup.
        import builtins

        _real_import = builtins.__import__

        def _import(name, *args, **kwargs):
            module = _real_import(name, *args, **kwargs)
            if name == "lib.labgrid_venue" or name.startswith("lib.labgrid_venue"):
                _install()
            return module

        builtins.__import__ = _import
    '''
)

STUB_TEST = "def test_placeholder(router):\n    assert router is None or True\n"

#: A plan_venue stand-in: resolves the given place, acquires nothing by itself.
PLAN_SHIM = textwrap.dedent(
    '''
    import os

    import lib.labgrid_venue as venue
    from lib.lab_inventory import CoordinatorEntry, LabInventory, RouterEntry, SwitchEntry

    _PLACE = os.environ["PROBE_PLAN_PLACE"]
    _ACTION = os.environ.get("PROBE_PLAN_ACTION", "venue")

    def _entry(place):
        return RouterEntry(name=place, poe_port="3", address="192.0.2.10", place=place)

    def _project_plan(*, environ=None, operator_host=None):
        if _ACTION != "venue":
            return venue.VenuePlan("legacy", detail="TOLLGATE_VENUE='direct' keeps the legacy path")
        binding = venue.VenueBinding(bench=None, entry=_entry(_PLACE))
        return venue.VenuePlan("venue", binding=binding, detail=_PLACE, origin="forced")

    venue.plan_venue = _project_plan
    '''
)


def run_case(name: str, env: dict[str, str], args: list[str] | None = None) -> dict[str, object]:
    """One real pytest session; returns exit code and whether it acquired.

    The stub test lives INSIDE the repo tree (``tests/unit/``) so the real
    ``tests/conftest.py`` — and therefore the real ``router`` fixture — is
    collected; it is removed again in the ``finally`` regardless of outcome.
    """
    marker = Path(tempfile.mkdtemp(prefix="claim-probe-"))
    slug = "".join(ch if ch.isalnum() else "_" for ch in name)[:40]
    stub = REPO / "tests" / "unit" / f"test_zz_probe_{os.getpid()}_{slug}.py"
    try:
        stub.write_text(STUB_TEST, encoding="utf-8")
        (marker / "sitecustomize.py").write_text(SHIM, encoding="utf-8")
        (marker / "plan_shim.py").write_text(PLAN_SHIM, encoding="utf-8")
        acquired_marker = marker / "acquired.txt"

        full_env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("TOLLGATE_FLEET_CLAIM")},
            "PYTHONPATH": f"{marker}:{REPO}",
            "PROBE_ACQUIRE_MARKER": str(acquired_marker),
            "TOLLGATE_SKIP_ROUTER_INVENTORY": "1",
            "TOLLGATE_LAB_TYPE": "physical",
            "TOLLGATE_VENUE": "labgrid",
            **env,
        }
        # -p plan_shim patches plan_venue before conftest's fixture runs.
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "plan_shim",
            "-p",
            "no:cacheprovider",
            "-q",
            "--no-header",
            "-o",
            "addopts=",
            *(args or [str(stub.relative_to(REPO))]),
        ]
        proc = subprocess.run(cmd, cwd=REPO, env=full_env, capture_output=True, text=True)
        tail = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()][-4:]
        return {
            "name": name,
            "rc": proc.returncode,
            "acquired": acquired_marker.read_text(encoding="utf-8").splitlines()
            if acquired_marker.exists()
            else [],
            "tail": tail,
        }
    finally:
        stub.unlink(missing_ok=True)
        for leftover in marker.rglob("*"):
            if leftover.is_file():
                leftover.unlink()
        marker.rmdir()


CASES = [
    (
        "no claim env (today's behaviour)",
        {},
        {"TOLLGATE_FLEET_CLAIM_PLACE": "", "PROBE_PLAN_PLACE": "x1860-1"},
    ),
    (
        "claim agrees with the venue resolution",
        {"TOLLGATE_FLEET_CLAIM_PLACE": "x1860-1",
         "TOLLGATE_FLEET_CLAIM_OWNER": "probe-host/prta-probe-run",
         "TOLLGATE_FLEET_RUN_ID": "probe-run"},
        {"TOLLGATE_FLEET_CLAIM_PLACE": "", "PROBE_PLAN_PLACE": "x1860-1"},
    ),
    (
        "claim names x1860-2, venue resolves x1860-1",
        {"TOLLGATE_FLEET_CLAIM_PLACE": "x1860-2",
         "TOLLGATE_FLEET_CLAIM_OWNER": "probe-host/prta-probe-run",
         "TOLLGATE_FLEET_RUN_ID": "probe-run"},
        {"TOLLGATE_FLEET_CLAIM_PLACE": "", "PROBE_PLAN_PLACE": "x1860-1"},
    ),
    (
        "claim with a legacy/direct venue outcome",
        {"TOLLGATE_FLEET_CLAIM_PLACE": "x1860-1",
         "TOLLGATE_FLEET_CLAIM_OWNER": "probe-host/prta-probe-run",
         "TOLLGATE_FLEET_RUN_ID": "probe-run"},
        {"TOLLGATE_FLEET_CLAIM_PLACE": "", "PROBE_PLAN_PLACE": "x1860-1",
         "PROBE_PLAN_ACTION": "legacy"},
    ),
]


def main() -> int:
    print(f"repo      : {REPO}")
    print(f"python    : {sys.version.split()[0]}")
    print(f"base      : e2fd71f43ffb577d7c31dc826d4ce9195275edef")
    print()
    failures = 0
    for name, env, extra in CASES:
        # `extra` first, then the case env: a case that sets the claim must win.
        merged = {**extra, **env}
        merged = {k: v for k, v in merged.items() if v != ""}
        result = run_case(name, merged)
        acquired = result["acquired"]
        if "no claim env" in name:
            expected_rc, expected_acquire = 0, True
        elif "agrees" in name:
            expected_rc, expected_acquire = 0, True
        else:
            expected_rc, expected_acquire = 4, False
        ok = result["rc"] == expected_rc and bool(acquired) == expected_acquire
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        print(f"        exit code          : {result['rc']} (expected {expected_rc})")
        print(f"        venue acquired     : {bool(acquired)} (expected {expected_acquire})")
        for line in result["tail"]:
            print(f"        | {line}")
        print()
    print(f"{len(CASES) - failures}/{len(CASES)} cases behaved as specified")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
