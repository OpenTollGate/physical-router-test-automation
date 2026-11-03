#!/usr/bin/env python3
"""Unified profile-driven test runner."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import replace
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.claim_handoff import (  # noqa: E402  (path set above)
    ENV_OWNER,
    ENV_PLACE,
    ENV_RUN_ID,
    ReleaseReason,
    ReleaseState,
    evaluate_handoff,
    release_verdict,
)
from lib.run_admission import (  # noqa: E402  (path set above)
    Admission,
    AdmissionRefused,
    AdmissionRequest,
    Target,
    demand_from_mapping,
    resolve_target,
)
from lib.supervised_run import SupervisionError, supervised_tee_run  # noqa: E402

REPO_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
PLANS_DIR = REPO_DIR / "plans"
RESULTS_ROOT = REPO_DIR / "results"
TESTS_DIR = REPO_DIR / "tests"

#: Exit code for a run that never started because admission refused it.
#: Distinct from pytest's own codes so a caller can tell "no capacity" from
#: "tests failed" without parsing output.
ADMISSION_EXIT = 75

#: Exit code for a run whose *supervision* failed while the child was running
#: (lease could not be renewed, or the claim was no longer ours). Distinct from
#: ADMISSION_EXIT (nothing ran) and from any pytest status (the tests ran but
#: their verdict is not the reason we stopped).
SUPERVISION_EXIT = 76

#: Scope labels ``tests/conftest.py`` uses to decide auto-skip. Mirrors
#: ``lib.fleet_binding.AUTO_SKIP_SCOPES``; a run outside these acquires.
_SKIP_SCOPES = ("unit", "parity")


class FleetPlan:
    """A fully bound, authorized lane — everything a supervised run needs.

    Built only after BOTH gates have passed: manifest authorization
    (:func:`admission_from_args`) and the DUT binding (:func:`bind_fleet_run`).
    Holding one of these means the runner has proved that the lane it is about
    to reserve host budget for is the same place the pytest fixture will own.

    A plain class, not a dataclass: this module is loaded by tests through
    ``importlib`` under a synthetic name that is never registered in
    ``sys.modules``, and ``@dataclass`` cannot resolve string annotations in
    that situation.
    """

    __slots__ = ("admission", "binding", "run_id", "ttl_s")

    def __init__(self, admission: Admission, binding: Any, run_id: str, ttl_s: float) -> None:
        self.admission = admission
        self.binding = binding
        self.run_id = run_id
        self.ttl_s = ttl_s

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"FleetPlan(run_id={self.run_id!r}, lane={getattr(self.binding, 'resource_id', '?')!r})"

    def claim_env(self) -> dict[str, str]:
        """The claim the child's fixture must honour (never an acquisition).

        ``binding.as_env()`` forces the venue to THIS place, so the fixture
        cannot fall back to direct/legacy underneath the reservation; the
        ``TOLLGATE_FLEET_CLAIM_*`` triple is what ``tests/conftest.py``'s
        ``_claim_handoff_guard`` verifies before ``venue_mutex`` takes anything.
        """
        claim = self.binding.claim
        return {
            **self.binding.as_env(),
            ENV_PLACE: claim.place,
            ENV_OWNER: claim.owner,
            ENV_RUN_ID: self.run_id,
        }


def scope_from_profile(profile: dict[str, Any]) -> str:
    """The session scope ``conftest`` would use, from the profile's testpaths.

    ``conftest`` auto-skips the venue on a unit-only/parity-only session, which
    would leave a fleet reservation with no place owner. The binding check needs
    the same answer the fixture will reach, so this mirrors
    ``_session_args_only`` over the command the runner is about to build.
    """
    pytest_cfg = profile.get("pytest")
    if not isinstance(pytest_cfg, dict):
        return "full"
    testpaths = pytest_cfg.get("testpaths")
    names = [testpaths] if isinstance(testpaths, str) else list(testpaths or ())
    if not names:
        return "full"
    for scope in _SKIP_SCOPES:
        if all(scope in str(name).replace(os.sep, "/") for name in names):
            return scope
    return "full"


def _inventory_for_binding(env: dict[str, str]):
    """Load the inventory ``plan_venue`` would load, for the same env.

    Deliberately the real loader: the binding check must describe the inventory
    this host would actually use, so the path comes from the *child's* env
    (``TOLLGATE_LAB_INVENTORY``, the same override ``plan_venue`` honours) and
    never from a private default.
    """
    from lib.lab_inventory import inventory_path, load_inventory

    override = (env.get("TOLLGATE_LAB_INVENTORY") or "").strip()
    return load_inventory(Path(override).expanduser() if override else inventory_path())


def bind_fleet_run(
    args: argparse.Namespace,
    *,
    run_id: str,
    env: dict[str, str],
    profile: dict[str, Any],
) -> Any:
    """Prove the manifest lane IS the place the fixture will own, or refuse.

    Requirement 1 on the production path. Before this existed the runner could
    reserve host budget for fleet A's router while the fixture drove fleet B's —
    or acquired nothing at all and silently used the legacy direct path
    underneath the reservation.

    Every input problem is a refusal carrying a stable
    :class:`lib.fleet_binding.Refusal` code; none of them is a warning.
    """
    from lib.fleet_binding import BindingRefused, check_binding
    from lib.fleet_capacity import load_manifest
    from lib.labgrid_venue import ClientIdentity

    manifest = load_manifest(args.fleet_manifest)
    try:
        inventory = _inventory_for_binding(env)
    except Exception as exc:  # noqa: BLE001 - a missing inventory is a refusal
        raise AdmissionRefused(
            "inventory_unavailable",
            f"the venue inventory could not be loaded ({type(exc).__name__}: {exc}); "
            "a lane cannot be bound to a place without it",
        ) from None

    # The owner the CHILD's fixture will acquire as, computed from the env the
    # child will receive — not from this process's environment. If these two
    # disagree the claim is attributed to an identity that never takes the
    # place, and the handoff guard would refuse mid-run.
    owner = ClientIdentity.current(env).owner

    try:
        return check_binding(
            manifest=manifest,
            inventory=inventory,
            environ=env,
            fleet_id=args.fleet,
            resource_id=args.resource,
            lane_id=args.lane,
            owner=owner,
            profile_endpoint=(env.get("TOLLGATE_SSH_HOST") or "").strip(),
            session_scope=scope_from_profile(profile),
            xdist_worker=bool(env.get("PYTEST_XDIST_WORKER")),
            operator_host=(env.get("TOLLGATE_SSH_HOST") or "").strip() or None,
        )
    except BindingRefused as exc:
        raise AdmissionRefused(exc.code, exc.detail) from None


def verify_claim_handoff(binding: Any, *, run_id: str, env: dict[str, str]) -> None:
    """Re-run the fixture's own handoff decision before spending a reservation.

    ``tests/conftest.py:_claim_handoff_guard`` will make this decision anyway —
    at rc=4, after the lane is reserved. Making it here as well means a claim
    the fixture would refuse is refused before host budget is taken, with the
    runner's own exit code. Same function, same inputs, so they cannot disagree.
    """
    from lib.labgrid_venue import ClientIdentity

    decision = evaluate_handoff(
        environ={**env, ENV_PLACE: binding.place, ENV_OWNER: binding.owner, ENV_RUN_ID: run_id},
        resolved_place=binding.place,
        owns_place=True,
        identity_owner=ClientIdentity.current(env).owner,
    )
    if decision.refused:
        raise AdmissionRefused(f"claim_{decision.code.value}", decision.message)


def authorize_fleet_target(args: argparse.Namespace) -> tuple[Any, Target, Any]:
    """Manifest authorization: may this run have this lane? Pure, no store.

    Returns ``(manifest, target, demand)``. Deliberately does NOT construct
    ``FleetAuthority`` — that constructor mints the SQLite file, and a refused
    run must create nothing at all. ``build_admission`` does the minting, and
    only after this and the binding check have both passed.
    """
    from lib.fleet_capacity import load_manifest

    manifest = load_manifest(args.fleet_manifest)
    target = resolve_target(
        fleet_id=args.fleet,
        lane_id=args.lane,
        resource_id=args.resource or args.lane,
        domains=tuple(args.domain or ()),
        manifest=manifest,
    )
    demand = demand_from_mapping(
        {
            "vcpus": args.demand_vcpus,
            "ram_mib": args.demand_ram_mib,
            "disk_mib": args.demand_disk_mib,
            "inodes": args.demand_inodes,
        },
    )
    return manifest, target, demand


def build_admission(args: argparse.Namespace, run_id: str, target: Target, demand: Any) -> Admission:
    """Mint the store and wrap one run's owned reservation. The last gate step."""
    from lib.fleet_authority import FleetAuthority

    authority = FleetAuthority(Path(args.authority_store))
    return Admission(
        authority,
        AdmissionRequest(
            run_id=run_id,
            target=target,
            demand=demand,
            ttl_s=float(args.lease_ttl),
        ),
    )


def admission_from_args(args: argparse.Namespace, run_id: str) -> Admission | None:
    """Authorize then build the reservation, or None when no lane is requested.

    The one-call form, kept for callers that only need authorization semantics
    (``tests/unit/test_runner_manifest_authorization.py``). ``main()`` uses the
    split form so the binding check runs before the store is minted.
    """
    if not args.fleet_manifest:
        return None
    _, target, demand = authorize_fleet_target(args)
    return build_admission(args, run_id, target, demand)


def plan_fleet_run(
    args: argparse.Namespace,
    *,
    run_id: str,
    env: dict[str, str],
    profile: dict[str, Any],
) -> FleetPlan | None:
    """Both gates, before any store is minted or lane reserved.

    Order is a safety property, not style:

    1. **authorization** — pure; an observe-only or undeclared manifest is
       refused here, so it never reaches a reservation;
    2. **binding** — pure; the lane must BE the place the fixture will own;
    3. **handoff** — the fixture's own decision, re-run early so a claim it
       would refuse is refused before host budget is taken;
    4. **build** — ``FleetAuthority.__init__`` mints the SQLite file, so it is
       last: every refusal above creates nothing.

    Returns ``None`` for a run that takes no lane (no ``--fleet-manifest``).
    """
    if not args.fleet_manifest:
        return None
    _, target, demand = authorize_fleet_target(args)
    binding = bind_fleet_run(args, run_id=run_id, env=env, profile=profile)
    verify_claim_handoff(binding, run_id=run_id, env=env)
    admission = build_admission(args, run_id, target, demand)
    return FleetPlan(
        admission=admission,
        binding=binding,
        run_id=run_id,
        ttl_s=float(args.lease_ttl),
    )


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        try:
            parts = shlex.split(value, posix=True)
            if len(parts) == 1:
                return parts[0]
        except ValueError:
            pass
        return value[1:-1]
    return value


def load_dotenv(dotenv_path: Path, target_env: dict[str, str]) -> None:
    if not dotenv_path.is_file():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in target_env:
            continue
        target_env[key] = strip_quotes(value)


def resolve_profile_path(profile_name: str) -> Path:
    profile_path = PLANS_DIR / f"{profile_name}.yaml"
    if not profile_path.is_file():
        raise FileNotFoundError(f"Profile not found: {profile_name} (expected {profile_path})")
    return profile_path


def load_profile(profile_name: str) -> dict[str, Any]:
    profile_path = resolve_profile_path(profile_name)
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Profile must be a YAML mapping: {profile_path}")
    return data


def require_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Profile field '{field_name}' must be a mapping")
    return value


def require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Profile field '{field_name}' must be a non-empty string")
    return value.strip()


def normalize_runner_dir_name(profile: dict[str, Any]) -> str:
    runner_kind = require_string(profile.get("runner"), "runner")
    if runner_kind == "playwright":
        return "playwright"

    pytest_cfg = require_mapping(profile.get("pytest"), "pytest")
    testpaths = pytest_cfg.get("testpaths")
    if isinstance(testpaths, str) and testpaths.strip():
        return Path(testpaths.strip()).name

    matrix = require_mapping(profile.get("matrix"), "matrix")
    tier = require_string(matrix.get("tier"), "matrix.tier")
    return tier.replace("/", "-")


def git_short_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip() or "unknown"
    except Exception:
        return "unknown"


def git_full_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_DIR,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip() or "unknown"
    except Exception:
        return "unknown"


def resolve_run_dir(cli_run_dir: str | None, env: dict[str, str], run_id: str) -> Path:
    if cli_run_dir:
        return Path(cli_run_dir).expanduser().resolve()
    env_results_dir = env.get("TOLLGATE_RESULTS_DIR")
    if env_results_dir:
        return Path(env_results_dir).expanduser().resolve()
    return (RESULTS_ROOT / run_id).resolve()


def ensure_results_layout(results_dir: Path, runner_dir_name: str) -> dict[str, Path]:
    paths = {
        "root": results_dir,
        "raw": results_dir / "raw",
        "runner": results_dir / "raw" / runner_dir_name,
        "report": results_dir / "report",
        "artifacts": results_dir / "artifacts",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def tee_run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    dry_run: bool,
) -> int:
    rendered = " ".join(shlex.quote(part) for part in command)
    print(f"==> Command: {rendered}")
    print(f"==> CWD:     {cwd}")
    print(f"==> Log:     {log_path}")

    if dry_run:
        return 0

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log_file.write(line)
        return process.wait()


def re_read_ownership(plan: "FleetPlan | None", env: dict[str, str]) -> str | None:
    """Ask the coordinator who holds the bound place NOW. Read-only.

    This is ``labgrid-client show`` (``LabgridBench.owner()``): it reads the
    ``acquired:`` line and never acquires, releases or powers anything. It is
    the *coordinator half* of release evidence — without it a clean release
    could only be asserted, never shown.

    Returns ``""`` when the place is free, the holder's identity string when it
    is held, and ``None`` when ownership could not be read. ``None`` is not
    "free": an unreadable release is an unproven release, and the fail-closed
    rule in :func:`lib.claim_handoff.release_verdict` turns it into
    ``CLEANUP_PENDING``.

    Any failure (no client binary, coordinator unreachable, unparseable output)
    is folded into ``None`` here rather than raised, because the caller's
    reaction to all of them is identical: do not claim clean.
    """
    if plan is None:
        return None
    try:
        from lib.labgrid_venue import ClientIdentity, LabgridBench

        bench = LabgridBench(
            plan.binding.coordinator,
            plan.binding.place,
            identity=ClientIdentity.current(env),
        )
        return bench.owner()
    except Exception:  # noqa: BLE001 - unreadable ownership is not "free"
        return None


def release_inputs(
    plan: "FleetPlan | None",
    *,
    error: BaseException | None,
    ever_acquired: bool,
    env: dict[str, str],
) -> dict[str, Any]:
    """The four facts :func:`lib.claim_handoff.release_verdict` decides on.

    Kept as its own function so the decision is testable without a runner, and
    so the one piece of evidence this process has to go and get — the
    coordinator's ownership re-read — is in exactly one place.
    """
    return {
        "acquired": ever_acquired,
        "cleanup_pending": False,
        "error": error,
        "owner_after": re_read_ownership(plan, env) if ever_acquired and error is None else None,
        "our_owner": getattr(plan.binding, "owner", "") if plan is not None else "",
        "place": getattr(plan.binding, "place", "") if plan is not None else "",
    }


def write_release_evidence(path: Path, *, run_id: str, verdict: Any) -> None:
    """Persist the release verdict next to the run's output log.

    Evidence, not a log line: an operator reconciling a lane needs the reason
    and the post-release owner string, and neither survives a rotated stdout.
    A write failure is reported but never masks the run's own outcome.
    """
    record = {"run_id": run_id, **verdict.to_dict()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 - evidence write must not mask the run
        eprint(f"==> WARNING: could not write release evidence to {path}: {exc}")


def build_pytest_command(
    profile: dict[str, Any],
    runner_dir: Path,
    results_dir: Path,
    extra_args: list[str],
) -> list[str]:
    pytest_cfg = require_mapping(profile.get("pytest"), "pytest")
    testpaths = pytest_cfg.get("testpaths")
    markers = pytest_cfg.get("markers")

    command = [
        sys.executable,
        "-m",
        "pytest",
        f"--junitxml={runner_dir / 'junit.xml'}",
        f"--html={runner_dir / 'report.html'}",
        "--self-contained-html",
        f"--results={results_dir}",
    ]

    if testpaths:
        if isinstance(testpaths, str):
            command.append(testpaths)
        elif isinstance(testpaths, list):
            command.extend(str(item) for item in testpaths)
        else:
            raise ValueError("Profile field 'pytest.testpaths' must be string, list, or null")

    if markers:
        if not isinstance(markers, str):
            raise ValueError("Profile field 'pytest.markers' must be string or null")
        command.extend(["-m", markers])

    command.extend(extra_args)
    return command


def normalize_playwright_glob(test_glob: str) -> str:
    normalized = test_glob.strip()
    if normalized.startswith("tests/"):
        return normalized[len("tests/"):]
    return normalized


def build_playwright_command(
    profile: dict[str, Any],
    runner_dir: Path,
    extra_args: list[str],
) -> tuple[list[str], dict[str, str]]:
    playwright_cfg = require_mapping(profile.get("playwright"), "playwright")
    test_glob = require_string(playwright_cfg.get("test_glob"), "playwright.test_glob")

    command = [
        "npx",
        "playwright",
        "test",
        normalize_playwright_glob(test_glob),
        "--config=playwright.config.mjs",
        "--reporter=json,html",
        f"--output={runner_dir}",
    ]
    command.extend(extra_args)

    extra_env = {
        "PLAYWRIGHT_JSON_OUTPUT_NAME": str((runner_dir / "results.json").resolve()),
    }
    return command, extra_env


def move_playwright_report(runner_dir: Path) -> None:
    destination = runner_dir / "report"
    if destination.is_dir():
        return

    candidates = [
        TESTS_DIR / "report",
        TESTS_DIR / "playwright-report",
        REPO_DIR / "playwright-report",
        REPO_DIR / "report",
    ]
    for candidate in candidates:
        if candidate.is_dir() and candidate.resolve() != destination.resolve():
            shutil.move(str(candidate), str(destination))
            return


def collect_command(
    *,
    env: dict[str, str],
    profile: dict[str, Any],
    profile_name: str,
    run_id: str,
    results_dir: Path,
    runner_dir_name: str,
    started_at: str,
    finished_at: str,
) -> tuple[list[str], list[str]]:
    matrix = require_mapping(profile.get("matrix"), "matrix")
    runner_kind = require_string(profile.get("runner"), "runner")
    test_plan = env.get("TOLLGATE_TEST_PLAN") or profile_name

    base_command = [
        sys.executable,
        str(SCRIPTS_DIR / "collect-results.py"),
        "--run-dir",
        str(results_dir),
        "--run-id",
        run_id,
        "--sut-commit",
        env.get("TOLLGATE_SUT_COMMIT", ""),
        "--sut-branch",
        env.get("TOLLGATE_BRANCH", ""),
        "--sut-backend",
        env.get("TOLLGATE_BACKEND", "go"),
        "--router-id",
        env.get("TOLLGATE_ROUTER_ID", ""),
        "--router-model",
        env.get("TOLLGATE_ROUTER_MODEL", ""),
        "--router-arch",
        env.get("TOLLGATE_ROUTER_ARCH", ""),
        "--client-type",
        env.get("TOLLGATE_CLIENT_TYPE", ""),
        "--viewport",
        env.get("TOLLGATE_VIEWPORT", "desktop"),
        "--test-plan",
        test_plan,
        "--started-at",
        started_at,
        "--finished-at",
        finished_at,
        "--allow-failures",
    ]

    sut_pr = env.get("TOLLGATE_PR")
    if sut_pr:
        base_command.extend(["--sut-pr", sut_pr])

    query_router = env.get("TOLLGATE_SSH_HOST")
    if query_router:
        base_command.extend(["--query-router", query_router])

    if truthy(env.get("TOLLGATE_VIRTUAL_LAB")):
        base_command.append("--virtual-lab")

    if runner_kind == "pytest":
        base_command.extend(["--pytest", f"{runner_dir_name}=raw/{runner_dir_name}/junit.xml"])
    elif runner_kind == "playwright":
        base_command.extend(["--playwright", f"{runner_dir_name}=raw/{runner_dir_name}/results.json"])
    else:
        raise ValueError(f"Unsupported runner: {runner_kind}")

    extra_metadata_args = [
        f"--tier={require_string(matrix.get('tier'), 'matrix.tier')}",
        f"--scope={require_string(matrix.get('scope'), 'matrix.scope')}",
        f"--lab-type={require_string(matrix.get('lab'), 'matrix.lab')}",
        f"--profile={profile_name}",
    ]
    return base_command, extra_metadata_args


def run_collect_results(
    *,
    env: dict[str, str],
    profile: dict[str, Any],
    profile_name: str,
    run_id: str,
    results_dir: Path,
    runner_dir_name: str,
    started_at: str,
    finished_at: str,
    log_path: Path,
    dry_run: bool,
) -> int:
    base_command, extra_metadata_args = collect_command(
        env=env,
        profile=profile,
        profile_name=profile_name,
        run_id=run_id,
        results_dir=results_dir,
        runner_dir_name=runner_dir_name,
        started_at=started_at,
        finished_at=finished_at,
    )

    first_exit = tee_run(
        base_command + extra_metadata_args,
        cwd=REPO_DIR,
        env=env,
        log_path=log_path,
        dry_run=dry_run,
    )
    if dry_run or first_exit == 0:
        return first_exit

    eprint("==> collect-results.py rejected profile metadata flags; retrying without them")
    return tee_run(
        base_command,
        cwd=REPO_DIR,
        env=env,
        log_path=log_path,
        dry_run=False,
    )


def update_run_json_metadata(results_dir: Path, profile: dict[str, Any], profile_name: str) -> None:
    run_json_path = results_dir / "run.json"
    if not run_json_path.is_file():
        return

    matrix = require_mapping(profile.get("matrix"), "matrix")
    data = json.loads(run_json_path.read_text(encoding="utf-8"))
    data["profile"] = profile_name
    data["tier"] = require_string(matrix.get("tier"), "matrix.tier")
    data["scope"] = require_string(matrix.get("scope"), "matrix.scope")
    data["lab_type"] = require_string(matrix.get("lab"), "matrix.lab")
    run_json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def render_command(results_dir: Path) -> list[str]:
    return [sys.executable, str(SCRIPTS_DIR / "render-report.py"), "--run-dir", str(results_dir)]


def print_summary(
    *,
    profile_name: str,
    run_id: str,
    results_dir: Path,
    runner_kind: str,
    test_exit: int,
    collect_exit: int | None,
    render_exit: int | None,
) -> None:
    print("")
    print("=====================================================================")
    print("  PROFILE RUN SUMMARY")
    print("=====================================================================")
    print(f"  Profile:    {profile_name}")
    print(f"  Runner:     {runner_kind}")
    print(f"  Run ID:     {run_id}")
    print(f"  Results:    {results_dir}")
    print(f"  Test exit:  {test_exit}")
    if collect_exit is not None:
        print(f"  Collect:    {collect_exit}")
    if render_exit is not None:
        print(f"  Render:     {render_exit}")
    print("=====================================================================")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run tests from a profile",
        usage="python3 run-profile.py --profile <name> [--dry-run] [--run-dir DIR] [--no-render] [extra pytest args...]",
    )
    parser.add_argument("--profile", required=True, help="Profile name from plans/<name>.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them")
    parser.add_argument("--run-dir", default=None, help="Reuse or force a specific run directory")
    parser.add_argument("--no-render", action="store_true", help="Skip collect-results.py and render-report.py")
    parser.add_argument(
        "--fleet-manifest",
        default=None,
        help=(
            "Authorized fleet manifest. When given, the run MUST be granted a lane "
            "reservation before any test executes; refusing is the default on any problem."
        ),
    )
    parser.add_argument("--fleet", default=None, help="Fleet id from the manifest")
    parser.add_argument("--lane", default=None, help="Lane id to reserve (inferred if the manifest has one)")
    parser.add_argument("--resource", default=None, help="Resource id this run drives")
    parser.add_argument("--domain", action="append", default=None, help="Exclusive conflict domain (repeatable)")
    parser.add_argument(
        "--authority-store",
        default=str(REPO_DIR / ".fleet-authority.sqlite3"),
        help="Fleet authority SQLite store used for admission",
    )
    parser.add_argument("--lease-ttl", type=float, default=900.0, help="Reservation TTL in seconds")
    parser.add_argument("--demand-vcpus", type=int, default=None, help="Lane demand: vCPUs")
    parser.add_argument("--demand-ram-mib", type=int, default=None, help="Lane demand: RAM MiB")
    parser.add_argument("--demand-disk-mib", type=int, default=None, help="Lane demand: disk MiB")
    parser.add_argument("--demand-inodes", type=int, default=None, help="Lane demand: inodes")
    args, extra_args = parser.parse_known_args()

    os.chdir(REPO_DIR)

    env = dict(os.environ)
    load_dotenv(REPO_DIR / ".env", env)

    profile = load_profile(args.profile)
    profile_name = require_string(profile.get("name"), "name")
    matrix = require_mapping(profile.get("matrix"), "matrix")
    runner_kind = require_string(profile.get("runner"), "runner")
    profile_env = require_mapping(profile.get("env"), "env")

    for key, value in profile_env.items():
        env[str(key)] = "" if value is None else str(value)

    env["TOLLGATE_PROFILE"] = profile_name
    env["TOLLGATE_LAB_TYPE"] = require_string(matrix.get("lab"), "matrix.lab")

    run_id = env.get("TOLLGATE_RUN_ID") or f"{utc_stamp()}-{profile_name}-{git_short_sha()}"
    env["TOLLGATE_RUN_ID"] = run_id

    # --- fleet lifecycle gate: refuse BEFORE any local or coordinator side effect
    # Two independent gates, both before anything is created:
    #   (1) binding (`bind_fleet_run`) — is this lane the DUT the fixture owns?
    #   (2) authorization (`admission_from_args`) — may this run have this lane?
    # Binding runs first because it is pure: the SQLite store is minted inside
    # `admission_from_args`, so a mis-bound or unauthorized run creates no
    # store, no results tree, no reservation and no child.
    #
    # Dry runs deliberately pass: they execute nothing, so they must neither be
    # blocked nor acquire anything (the branch below skips plan construction).
    plan: FleetPlan | None = None
    if args.dry_run:
        if args.fleet_manifest:
            eprint("==> DRY RUN: no lane reservation taken and no authority store touched.")
    elif args.fleet_manifest:
        try:
            plan = plan_fleet_run(args, run_id=run_id, env=env, profile=profile)
        except AdmissionRefused as exc:
            eprint(f"==> ADMISSION REFUSED: {exc.reason}: {exc.detail}")
            eprint("==> No test was executed, no lane was reserved, no store was created.")
            return ADMISSION_EXIT
        except Exception as exc:  # noqa: BLE001 - manifest/config errors are refusals too
            eprint(f"==> ADMISSION REFUSED: {type(exc).__name__}: {exc}")
            eprint("==> No test was executed, no lane was reserved, no store was created.")
            return ADMISSION_EXIT

    results_dir = resolve_run_dir(args.run_dir, env, run_id)
    runner_dir_name = normalize_runner_dir_name(profile)
    layout = ensure_results_layout(results_dir, runner_dir_name)

    env["TOLLGATE_RESULTS_DIR"] = str(results_dir)

    started_at = utc_iso()

    print(f"==> Profile:    {profile_name}")
    print(f"==> Runner:     {runner_kind}")
    print(f"==> Run ID:     {run_id}")
    print(f"==> Results:    {results_dir}")
    print(f"==> Git SHA:    {git_full_sha()}")

    if runner_kind == "pytest":
        command = build_pytest_command(profile, layout["runner"], results_dir, extra_args)
        command_env = env
        command_cwd = REPO_DIR
    elif runner_kind == "playwright":
        command, extra_runner_env = build_playwright_command(profile, layout["runner"], extra_args)
        command_env = dict(env)
        command_env.update(extra_runner_env)
        command_cwd = TESTS_DIR
    else:
        raise ValueError(f"Unsupported runner: {runner_kind}")

    # --- the claim the fixture must honour -------------------------------
    # `binding.as_env()` forces the venue to THIS place and the
    # `TOLLGATE_FLEET_CLAIM_*` triple attributes it to this run, so the
    # fixture's `_claim_handoff_guard` verifies the same place the runner
    # reserved. The runner still acquires NOTHING: the place belongs to pytest.
    if plan is not None:
        command_env = dict(command_env)
        command_env.update(plan.claim_env())
        print(
            f"==> Bound:     lane {plan.binding.resource_id} "
            f"-> place {plan.binding.place} @ {plan.binding.coordinator} "
            f"(owner {plan.binding.owner}, origin {plan.binding.origin})"
        )

    # --- admission: reserve the lane BEFORE anything executes --------------
    # This is the seam that stops two runs fighting over one DUT. A refusal
    # here ends the run with a distinct exit code and no test executes.
    #
    # A dry run is excluded on purpose. It executes nothing, so it must debit
    # nothing: previously it acquired, activated and *completed* a real
    # reservation, so the authority recorded a clean run that never happened and
    # any concurrent run sharing a conflict domain was refused `domain_conflict`
    # for the whole lease TTL. `plan` is only built when `not args.dry_run`, so
    # no store was minted either.
    if args.dry_run:
        admission = None
    else:
        admission = plan.admission if plan is not None else None

    if admission is not None:
        try:
            allocation = admission.acquire()
        except AdmissionRefused as exc:
            eprint(f"==> ADMISSION REFUSED: {exc.reason}: {exc.detail}")
            eprint("==> No test was executed. The lane was not reserved.")
            return ADMISSION_EXIT
        print(f"==> Reserved:  {allocation.lane_id} fence={allocation.fence} state={allocation.state.value}")

    # Supervision: the lease is renewed while the child runs, and the child's
    # process group is terminated if renewal fails or the claim stops being
    # ours. Without this a child that outlives its TTL keeps mutating a DUT the
    # store has already taken away (the audit's "CHILD FINISHED AFTER TTL /
    # RENEW CALLS 0").
    #
    # `mark_started()` runs INSIDE the try whose finally releases. It can raise
    # (`AdmissionRefused("not_acquired")`, or an `AuthorityError`/`FenceMismatch`
    # from `authority.activate`), and when it sat above this `try` a raise leaked
    # the lease for the whole TTL and exited with a traceback instead of the
    # documented ADMISSION_EXIT.
    supervision_error: SupervisionError | None = None
    test_exit = 0
    activated = False
    try:
        if admission is not None:
            try:
                admission.mark_started()
            except AdmissionRefused as exc:
                eprint(f"==> ADMISSION REFUSED: {exc.reason}: {exc.detail}")
                eprint("==> No test was executed. The lease was never activated.")
                return ADMISSION_EXIT
            except Exception as exc:  # noqa: BLE001 - activate failures are refusals too
                eprint(f"==> ADMISSION REFUSED: {type(exc).__name__}: {exc}")
                eprint("==> No test was executed. The lease was never activated.")
                return ADMISSION_EXIT
            activated = True
            allocation = admission.allocation
            assert allocation is not None
            test_exit = supervised_tee_run(
                command,
                cwd=command_cwd,
                env=command_env,
                log_path=layout["runner"] / "output.log",
                dry_run=args.dry_run,
                authority=admission.authority,
                run_id=run_id,
                fence=allocation.fence,
                ttl_s=plan.ttl_s,
            )
        else:
            test_exit = tee_run(
                command,
                cwd=command_cwd,
                env=command_env,
                log_path=layout["runner"] / "output.log",
                dry_run=args.dry_run,
            )
    except SupervisionError as exc:
        # Requirements 3/4: supervision could not continue, so the child's group
        # was terminated and this must NOT be reported as a test verdict. The
        # exit code is supervision's own (76 renewal / 77 authority).
        supervision_error = exc
        test_exit = exc.exit_code
        eprint(f"==> SUPERVISION FAILED: {exc.reason}: {exc.detail}")
        eprint("==> The child's process group was terminated; this is not a test verdict.")
    finally:
        if admission is not None:
            # Cleanliness is DERIVED, never asserted: `release_verdict` turns a
            # raised teardown, a re-read still showing us, or a missing re-read
            # into CLEANUP_PENDING. Only a place the coordinator reports free is
            # clean, so a failed run can never free a lane it did not clean.
            verdict = release_verdict(
                **release_inputs(plan, error=supervision_error, ever_acquired=activated, env=command_env)
            )
            try:
                admission.release(clean=verdict.clean, detail=f"run-profile: {verdict.reason.value}")
            except AdmissionRefused as exc:
                eprint(f"==> RELEASE UNCONFIRMED: {exc.reason}: {exc.detail}")
                eprint("==> The lane stays occupied until this is reconciled.")
                verdict = replace(verdict, state=ReleaseState.CLEANUP_PENDING, reason=ReleaseReason.RELEASE_RAISED)
            if not verdict.clean:
                eprint(
                    f"==> WARNING: lane not released cleanly ({verdict.reason.value}: "
                    f"{verdict.detail}); capacity remains consumed."
                )
            write_release_evidence(
                layout["runner"] / "release.json", run_id=run_id, verdict=verdict
            )

    if supervision_error is not None:
        # Nothing downstream may reinterpret a supervision failure as a test
        # verdict: no collect, no render, no summary claiming completion.
        eprint(f"==> Supervision exit: {supervision_error.exit_code}")
        return supervision_error.exit_code


    if runner_kind == "playwright" and not args.dry_run:
        move_playwright_report(layout["runner"])

    finished_at = utc_iso()

    collect_exit: int | None = None
    render_exit: int | None = None

    if not args.no_render:
        collect_exit = run_collect_results(
            env=env,
            profile=profile,
            profile_name=profile_name,
            run_id=run_id,
            results_dir=results_dir,
            runner_dir_name=runner_dir_name,
            started_at=started_at,
            finished_at=finished_at,
            log_path=layout["artifacts"] / "collect-results.log",
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            update_run_json_metadata(results_dir, profile, profile_name)

        if collect_exit == 0:
            render_exit = tee_run(
                render_command(results_dir),
                cwd=REPO_DIR,
                env=env,
                log_path=layout["artifacts"] / "render-report.log",
                dry_run=args.dry_run,
            )
        elif args.dry_run:
            render_exit = tee_run(
                render_command(results_dir),
                cwd=REPO_DIR,
                env=env,
                log_path=layout["artifacts"] / "render-report.log",
                dry_run=True,
            )

    print_summary(
        profile_name=profile_name,
        run_id=run_id,
        results_dir=results_dir,
        runner_kind=runner_kind,
        test_exit=test_exit,
        collect_exit=collect_exit,
        render_exit=render_exit,
    )

    if args.dry_run:
        return 0
    if render_exit not in (None, 0):
        return render_exit
    if collect_exit not in (None, 0):
        return collect_exit
    return test_exit


if __name__ == "__main__":
    sys.exit(main())