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
from typing import Any

import yaml


REPO_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
PLANS_DIR = REPO_DIR / "plans"
RESULTS_ROOT = REPO_DIR / "results"
TESTS_DIR = REPO_DIR / "tests"


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
    results_dir = resolve_run_dir(args.run_dir, env, run_id)
    runner_dir_name = normalize_runner_dir_name(profile)
    layout = ensure_results_layout(results_dir, runner_dir_name)

    env["TOLLGATE_RUN_ID"] = run_id
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

    test_exit = tee_run(
        command,
        cwd=command_cwd,
        env=command_env,
        log_path=layout["runner"] / "output.log",
        dry_run=args.dry_run,
    )

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
