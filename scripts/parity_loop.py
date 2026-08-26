#!/usr/bin/env python3
"""Iterative Go/Rust TollGate parity test loop.

Builds both backends, runs the parity pytest suite, parses results,
auto-categorizes failures, writes structured per-wave reports, and
tracks divergences across waves.

Usage::

    python3 scripts/parity_loop.py --dry-run
    python3 scripts/parity_loop.py --wave 1
    python3 scripts/parity_loop.py --max-waves 3 --verbose

Exit code 0 if every test in the final wave passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PRTA_ROOT = SCRIPT_DIR.parent
REPORTS_DIR = PRTA_ROOT / "reports" / "parity"
DIVERGENCES_FILE = REPORTS_DIR / "divergences.json"

GO_SRC = Path("/home/ubuntu/src/tollgate-module-basic-go/src")
GO_BINARY_OUT = Path("/tmp/tollgate-wrt-parity")
GO_LDFLAGS = (
    '-X github.com/OpenTollGate/tollgate-module-basic-go/src/config_manager.GitBranch=main'
)

RUST_SRC = Path("/home/ubuntu/src/tollgate-module-basic-rust")
RUST_BINARY = RUST_SRC / "target" / "release" / "tollgate-module-basic-rust"

PARITY_TEST = "tests/api/test_go_rust_basic_parity.py"

# Map a test-name keyword to the HTTP endpoint it exercises.  Used when
# building the structured failure report.
ENDPOINT_MAP: list[tuple[str, str]] = [
    ("discovery", "GET /"),
    ("balance", "GET /balance"),
    ("usage", "GET /usage"),
    ("whoami", "GET /whoami"),
    ("invalid_token", "POST /"),
    ("content_types", "cross-endpoint"),
    ("cli", "Unix socket"),
]


# ---------------------------------------------------------------------------
# Build phase
# ---------------------------------------------------------------------------


def build_go(verbose: bool = False) -> bool:
    """Build the Go binary to /tmp/tollgate-wrt-parity. Return True on success."""
    print("[build] Go: CGO_ENABLED=0 go build ...")
    cmd = [
        "go", "build",
        "-ldflags", GO_LDFLAGS,
        "-o", str(GO_BINARY_OUT),
        ".",
    ]
    env = {**os.environ, "CGO_ENABLED": "0"}
    result = subprocess.run(
        cmd, cwd=str(GO_SRC), env=env,
        capture_output=not verbose, text=True,
    )
    if result.returncode != 0:
        print(f"[build] Go FAILED (exit {result.returncode})", file=sys.stderr)
        if not verbose and result.stderr:
            print(result.stderr, file=sys.stderr)
        return False
    print(f"[build] Go OK -> {GO_BINARY_OUT}")
    return True


def build_rust(verbose: bool = False) -> bool:
    """Build the Rust binary in release mode. Return True on success."""
    print("[build] Rust: cargo build --release")
    result = subprocess.run(
        ["cargo", "build", "--release"],
        cwd=str(RUST_SRC),
        capture_output=not verbose, text=True,
    )
    if result.returncode != 0:
        print(f"[build] Rust FAILED (exit {result.returncode})", file=sys.stderr)
        if not verbose and result.stderr:
            print(result.stderr[-2000:], file=sys.stderr)
        return False
    print(f"[build] Rust OK -> {RUST_BINARY}")
    return True


def build_both(verbose: bool = False) -> bool:
    """Build Go then Rust. Return True only if both succeed."""
    if not build_go(verbose):
        return False
    if not build_rust(verbose):
        return False
    return True


# ---------------------------------------------------------------------------
# Test phase
# ---------------------------------------------------------------------------


def _pytest_json_available() -> bool:
    """Return True if the pytest-json-report plugin is importable."""
    try:
        import importlib.util
        return importlib.util.find_spec("pytest_jsonreport") is not None
    except Exception:
        return False


def run_pytest(wave: int, verbose: bool = False) -> tuple[dict | None, str, int]:
    """Run the parity pytest suite.

    Returns ``(json_report, stdout_text, returncode)``.  ``json_report``
    is the parsed report dict when pytest-json-report is available, else
    ``None`` (caller must fall back to stdout parsing).
    """
    json_path = Path(f"/tmp/parity_wave_{wave}.json")
    # Remove stale report so we can detect a fresh write.
    if json_path.exists():
        json_path.unlink()

    cmd = [
        sys.executable, "-m", "pytest",
        PARITY_TEST,
        "--no-deploy",
        "--tb=short",
        "-q",
        "--timeout=60",
    ]

    use_json = _pytest_json_available()
    if use_json:
        cmd += ["--json-report", f"--json-report-file={json_path}"]
    else:
        print("[test] pytest-json-report not installed — falling back to stdout parsing")
        # parse_stdout needs per-test verbose lines; -q emits dots only.
        cmd += ["-v"]

    env = {
        **os.environ,
        "TOLLGATE_GO_BINARY": str(GO_BINARY_OUT),
        "TOLLGATE_BINARY_PATH": str(RUST_BINARY),
    }

    print(f"[test] wave {wave}: {' '.join(cmd[:6])} ...")
    result = subprocess.run(
        cmd, cwd=str(PRTA_ROOT), env=env,
        capture_output=True, text=True,
    )
    stdout = result.stdout + ("\n" + result.stderr if result.stderr else "")

    if verbose:
        print(stdout)

    json_report: dict | None = None
    if use_json and json_path.exists():
        try:
            json_report = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[test] WARN: could not parse {json_path}: {exc}", file=sys.stderr)
            json_report = None

    if use_json and json_report is None:
        print("[test] JSON report missing/unparseable — falling back to stdout", file=sys.stderr)

    return json_report, stdout, result.returncode


# ---------------------------------------------------------------------------
# Parse phase
# ---------------------------------------------------------------------------

# pytest -v line:  path::test_name STATUS [ pct]
_RE_PYTEST_LINE = re.compile(
    r"([^\s:]+)::(test_\S+)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)"
)


def parse_json_report(report: dict) -> list[dict[str, Any]]:
    """Extract per-test result dicts from a pytest-json-report object."""
    tests: list[dict[str, Any]] = []
    for entry in report.get("tests", []):
        name = entry.get("nodeid", "").split("::")[-1] or "unknown"
        outcome = entry.get("outcome", "unknown")
        # pytest-json-report uses "failed"/"passed"/"skipped".
        message = ""
        call = entry.get("call", {})
        if outcome == "failed" and call:
            crash = call.get("crash", {})
            message = crash.get("message", "")
            if not message:
                longrepr = call.get("longrepr", "")
                if isinstance(longrepr, str):
                    message = longrepr
        elif outcome == "skipped":
            # Skipped tests carry a reason in setup/teardown call.
            for phase in ("setup", "call", "teardown"):
                phase_data = entry.get(phase)
                if phase_data and phase_data.get("outcome") == "skipped":
                    message = phase_data.get("longrepr", "") or phase_data.get("crash", {}).get("message", "")
                    break
        tests.append({"name": name, "outcome": outcome, "message": message})
    return tests


def parse_stdout(stdout: str) -> list[dict[str, Any]]:
    """Fallback: parse ``pytest -v`` stdout into per-test result dicts.

    Handles the short ``-q`` style lines (``...F.s..``) is too lossy, so we
    re-scan for the verbose node lines that pytest emits even under ``-q``
    when ``-v`` is in addopts (it is, via pytest.ini addopts).
    """
    tests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in stdout.splitlines():
        m = _RE_PYTEST_LINE.search(line)
        if not m:
            continue
        _, name, status = m.groups()
        if name in seen:
            continue
        seen.add(name)
        outcome = status.lower()
        tests.append({"name": name, "outcome": outcome, "message": ""})

    # Fill in failure messages from the FAILURES section.
    _augment_failures_from_stdout(stdout, tests)
    return tests


def _augment_failures_from_stdout(stdout: str, tests: list[dict[str, Any]]) -> None:
    """Populate ``message`` for failed tests by scanning the short summary."""
    failed_names = {t["name"] for t in tests if t["outcome"] == "failed"}
    if not failed_names:
        return

    # pytest prints a short test summary info block:
    #   FAILED tests/...::test_name - AssertionError: ...
    # One line per failure under -q --tb=short.
    for line in stdout.splitlines():
        if not line.startswith("FAILED"):
            continue
        # Line format: "FAILED path::test_name - <message>"
        body = line[len("FAILED"):].strip()
        # Split on " - " (pytest separator) or "==" (some configs)
        for sep in (" - ", "=="):
            if sep in body:
                nodeid, _, msg = body.partition(sep)
                break
        else:
            nodeid, msg = body, ""
        name = nodeid.split("::")[-1].strip()
        if name in failed_names:
            for t in tests:
                if t["name"] == name:
                    t["message"] = msg.strip()[:1000]
                    break

    # If short summary was absent, try the _ short traceback blocks.
    if any(t["outcome"] == "failed" and not t["message"] for t in tests):
        _augment_from_traceback_blocks(stdout, tests)


def _augment_from_traceback_blocks(stdout: str, tests: list[dict[str, Any]]) -> None:
    """Extract the assertion message from ``____ test_name ____`` blocks."""
    header_re = re.compile(r"_{3,}\s+(test_\S+)\s+_{3,}")
    lines = stdout.splitlines()
    name_to_test = {t["name"]: t for t in tests}
    i = 0
    while i < len(lines):
        m = header_re.search(lines[i])
        if m and m.group(1) in name_to_test:
            name = m.group(1)
            # Collect lines until next blank-dominated boundary or next header.
            buf: list[str] = []
            for j in range(i + 1, min(i + 80, len(lines))):
                if header_re.search(lines[j]):
                    break
                buf.append(lines[j])
            block = "\n".join(buf)
            # Extract the most useful line: AssertionError / assert message.
            msg = ""
            for bline in buf:
                if "PARITY CHECK FAILED" in bline or "assert" in bline.lower():
                    msg = bline.strip()
                    break
            if not msg:
                # last non-empty line
                for bline in reversed(buf):
                    if bline.strip():
                        msg = bline.strip()
                        break
            if msg and not name_to_test[name]["message"]:
                name_to_test[name]["message"] = msg[:1000]
            i += len(buf) + 1
        else:
            i += 1


# ---------------------------------------------------------------------------
# Categorize phase
# ---------------------------------------------------------------------------

# Infrastructure error signatures (case-insensitive substring).
_INFRA_PATTERNS = [
    "permission denied",
    "no such file or directory",
    "not found",
    "port 2121 in use",
    "port already in use",
    "address already in use",
    "connection refused",
    "binary exited early",
    "binary did not bind",
    "binary not found",
    "errno ",
    "eacces",
    "enoent",
    "operation not permitted",
    "port conflict",
]


def categorize_failure(test_name: str, message: str) -> str:
    """Classify a failure into one of the four categories.

    Returns one of: ``real_divergence``, ``infrastructure``, ``stub``,
    ``protocol_diff``.
    """
    name_l = test_name.lower()
    msg_l = message.lower()

    # CLI protocol difference — highest priority because it shadows real
    # divergences with a known protocol incompatibility.
    if "cli" in name_l:
        return "protocol_diff"

    # Stub detection: test name or message references a stub/hardcoded reply.
    if "stub" in name_l or "stub" in msg_l or "hardcoded" in msg_l or "not implemented" in msg_l:
        return "stub"

    # Infrastructure problems: permissions, missing binaries, port conflicts.
    for pat in _INFRA_PATTERNS:
        if pat in msg_l:
            return "infrastructure"

    # Default: a genuine status-code or body diff between backends.
    return "real_divergence"


def extract_endpoint(test_name: str) -> str:
    """Map a test name to the endpoint it exercises."""
    name_l = test_name.lower()
    for keyword, endpoint in ENDPOINT_MAP:
        if keyword in name_l:
            return endpoint
    return "unknown"


def extract_go_rust_values(message: str) -> str:
    """Pull a compact ``Go=X, Rust=Y`` summary from an assertion message."""
    # Common forms from _assert_parity and direct asserts:
    #   "Go   = 500" / "Rust = 200"
    #   "Go=/usage body... Rust=..."
    go = rust = ""
    for line in message.splitlines():
        ll = line.strip().lower()
        if ll.startswith("go") and "=" in line:
            go = line.split("=", 1)[1].strip()
        elif ll.startswith("rust") and "=" in line:
            rust = line.split("=", 1)[1].strip()
    if go or rust:
        return f"Go={go}, Rust={rust}"
    # Fallback: first 120 chars of the message.
    return message.strip().replace("\n", " ")[:120]


# ---------------------------------------------------------------------------
# Report phase
# ---------------------------------------------------------------------------


def build_wave_report(wave: int, tests: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the structured wave report dict."""
    passed = sum(1 for t in tests if t["outcome"] == "passed")
    failed = sum(1 for t in tests if t["outcome"] == "failed")
    skipped = sum(1 for t in tests if t["outcome"] in ("skipped", "xfailed"))

    failures: list[dict[str, Any]] = []
    for t in tests:
        if t["outcome"] != "failed":
            continue
        msg = t.get("message", "")
        failures.append({
            "test": t["name"],
            "category": categorize_failure(t["name"], msg),
            "message": extract_go_rust_values(msg),
            "endpoint": extract_endpoint(t["name"]),
        })

    return {
        "wave": wave,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(tests),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "failures": failures,
    }


def write_wave_report(report: dict[str, Any]) -> Path:
    """Write wave_N.json into reports/parity/. Return the path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"wave_{report['wave']}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"[report] wrote {path}")
    return path


# ---------------------------------------------------------------------------
# Divergence tracking
# ---------------------------------------------------------------------------


def load_divergences() -> dict[str, Any]:
    """Load divergences.json, returning an empty structure if absent."""
    if DIVERGENCES_FILE.exists():
        try:
            return json.loads(DIVERGENCES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"divergences": [], "history": []}


def update_divergences(report: dict[str, Any]) -> dict[str, Any]:
    """Update divergences.json with this wave's results.

    - New failures are added with ``status=open``.
    - Previously-open divergences absent from this wave are marked ``fixed``.
    """
    data = load_divergences()
    # Legacy divergence entries (schema <2) are keyed by id/endpoint, not test.
    existing: dict[str, Any] = {}
    for d in data.get("divergences", []):
        key = d.get("test") or d.get("id") or d.get("endpoint")
        if key:
            existing[key] = d
    current_failures = {f["test"]: f for f in report.get("failures", [])}

    # Mark fixed: were open before, not failing now.
    now = report["timestamp"]
    for test_name, div in existing.items():
        if div.get("status") == "open" and test_name not in current_failures:
            div["status"] = "fixed"
            div["fixed_at"] = now
            div["fixed_in_wave"] = report["wave"]

    # Add or refresh open divergences.
    for test_name, failure in current_failures.items():
        if test_name in existing:
            existing[test_name].update({
                "status": "open",
                "last_message": failure["message"],
                "category": failure["category"],
                "last_seen_wave": report["wave"],
                "last_seen": now,
            })
        else:
            existing[test_name] = {
                "test": test_name,
                "status": "open",
                "category": failure["category"],
                "message": failure["message"],
                "endpoint": failure["endpoint"],
                "first_seen_wave": report["wave"],
                "last_seen_wave": report["wave"],
                "first_seen": now,
                "last_seen": now,
            }

    data["divergences"] = sorted(
        existing.values(), key=lambda d: d.get("test") or d.get("id") or d.get("endpoint") or ""
    )
    data.setdefault("history", [])
    data["history"].append({
        "wave": report["wave"],
        "timestamp": now,
        "passed": report["passed"],
        "failed": report["failed"],
        "skipped": report["skipped"],
    })

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DIVERGENCES_FILE.write_text(json.dumps(data, indent=2) + "\n")
    print(f"[report] updated {DIVERGENCES_FILE}")
    return data


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary_table(report: dict[str, Any]) -> None:
    """Print a human-readable summary table for a wave."""
    wave = report["wave"]
    total = report["total"]
    passed = report["passed"]
    failed = report["failed"]
    skipped = report["skipped"]
    failures = report.get("failures", [])

    print()
    print("=" * 78)
    print(f"  WAVE {wave} SUMMARY   {report['timestamp']}")
    print("=" * 78)
    print(f"  total={total}  passed={passed}  failed={failed}  skipped={skipped}")
    print("-" * 78)

    if not failures:
        print("  No failures. All parity tests passed.")
    else:
        # Group by category.
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for f in failures:
            by_cat.setdefault(f["category"], []).append(f)

        for cat in ("real_divergence", "infrastructure", "stub", "protocol_diff"):
            items = by_cat.get(cat, [])
            if not items:
                continue
            print(f"\n  [{cat}] ({len(items)})")
            for f in items:
                msg = f["message"][:56]
                print(f"    {f['test']:<42} {f['endpoint']:<16} {msg}")

    fixed = [d for d in load_divergences().get("divergences", [])
             if d.get("fixed_in_wave") == wave]
    if fixed:
        print(f"\n  [fixed this wave] ({len(fixed)})")
        for d in fixed:
            print(f"    {d['test']:<42}")

    print("=" * 78)
    print()


# ---------------------------------------------------------------------------
# Wave execution
# ---------------------------------------------------------------------------


def run_wave(wave: int, verbose: bool = False) -> dict[str, Any]:
    """Execute one full wave: build, test, parse, categorize, report.

    Returns the wave report dict.
    """
    print(f"\n{'#' * 78}")
    print(f"#  WAVE {wave}")
    print(f"{'#' * 78}")

    # --- Build phase ---
    if not build_both(verbose):
        report = {
            "wave": wave,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "failures": [],
            "error": "build failed",
        }
        write_wave_report(report)
        print_summary_table(report)
        return report

    # --- Test phase ---
    json_report, stdout, _ = run_pytest(wave, verbose)

    # --- Parse phase ---
    if json_report is not None:
        tests = parse_json_report(json_report)
        print(f"[parse] extracted {len(tests)} results from JSON report")
    else:
        tests = parse_stdout(stdout)
        print(f"[parse] extracted {len(tests)} results from stdout")

    if not tests:
        print("[parse] WARNING: no test results parsed — dumping pytest output:", file=sys.stderr)
        print(stdout[-2000:], file=sys.stderr)

    # --- Categorize + report phase ---
    report = build_wave_report(wave, tests)
    write_wave_report(report)

    # --- Divergence tracking ---
    update_divergences(report)

    # --- Summary ---
    print_summary_table(report)
    return report


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


def print_dry_run(max_waves: int) -> None:
    """Print the wave plan without executing anything."""
    print("PARITY LOOP — DRY RUN")
    print("=" * 78)
    print(f"  max waves : {max_waves}")
    print(f"  prta root : {PRTA_ROOT}")
    print(f"  reports   : {REPORTS_DIR}")
    print()
    print("  BUILD PHASE (each wave):")
    print(f"    Go   : go build -ldflags ... -o {GO_BINARY_OUT}")
    print(f"           cwd={GO_SRC}")
    print(f"    Rust : cargo build --release")
    print(f"           cwd={RUST_SRC}")
    print()
    print("  TEST PHASE (each wave):")
    print(f"    pytest {PARITY_TEST} --no-deploy --tb=short -q --timeout=60")
    print(f"    TOLLGATE_GO_BINARY={GO_BINARY_OUT}")
    print(f"    TOLLGATE_BINARY_PATH={RUST_BINARY}")
    json_ok = _pytest_json_available()
    print(f"    pytest-json-report: {'available' if json_ok else 'NOT installed (stdout fallback)'}")
    print()
    print("  REPORT PHASE:")
    print(f"    wave report  -> {REPORTS_DIR}/wave_N.json")
    print(f"    divergences  -> {DIVERGENCES_FILE}")
    print()
    print("  WAVES PLANNED:")
    for w in range(1, max_waves + 1):
        print(f"    wave {w}: build -> test -> parse -> categorize -> report")
    print()
    print("  CATEGORIES: real_divergence, infrastructure, stub, protocol_diff")
    print("=" * 78)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Iterative Go/Rust TollGate parity test loop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the wave plan without executing.",
    )
    parser.add_argument(
        "--wave", type=int, default=None,
        help="Run a single specific wave number.",
    )
    parser.add_argument(
        "--max-waves", type=int, default=5,
        help="Maximum number of waves to run (default: 5).",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show all build and test output.",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        print_dry_run(args.max_waves)
        return 0

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.wave is not None:
        report = run_wave(args.wave, args.verbose)
        return 0 if report["failed"] == 0 and "error" not in report else 1

    # Loop mode: run waves until all pass or max_waves reached.
    last_report: dict[str, Any] | None = None
    for wave in range(1, args.max_waves + 1):
        report = run_wave(wave, args.verbose)
        last_report = report
        if report["failed"] == 0 and report["total"] > 0:
            print(f"[loop] wave {wave}: all tests passed — stopping.")
            break
        if wave < args.max_waves:
            print(f"[loop] wave {wave}: {report['failed']} failures — continuing to wave {wave + 1}")

    if last_report is None:
        return 1
    return 0 if last_report["failed"] == 0 and "error" not in last_report else 1


if __name__ == "__main__":
    sys.exit(main())
