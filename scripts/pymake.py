#!/usr/bin/env python3
"""pymake — Python runner mirroring familiar Makefile test targets."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.hardware_lock import (  # noqa: E402
    acquire_hardware_lock,
    release_hardware_lock,
    require_hardware_lock,
)
from lib.migration_registry import MigrationEntry, get_entry, load_registry  # noqa: E402
from lib.router_env import (  # noqa: E402
    apply_cli_overrides,
    load_router_into_environ,
    resolve_secondary_for_two_router,
)

YELLOW = "\033[33m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _banner(entry: MigrationEntry) -> None:
    if entry.pytest:
        print(f"{YELLOW}{BOLD}>>> This test has moved to pytest: {entry.pytest}{RESET}")
    print(f"{YELLOW}>>> Run: ./scripts/pymake.py {entry.make_target} --router <label>{RESET}")


def _check_requires(entry: MigrationEntry, args: argparse.Namespace) -> None:
    for req in entry.requires:
        if req == "SSID" and not args.ssid:
            raise SystemExit("Error: --ssid required for this target")
        if req == "PASS" and not args.password:
            raise SystemExit("Error: --password required for this target")
        if req == "secondary_router" and not os.environ.get("TOLLGATE_SECONDARY_ROUTER_HOST"):
            raise SystemExit(
                "Error: secondary router not configured. "
                "Set TOLLGATE_SECONDARY_ROUTER_HOST or use a two-router routers.env"
            )
        if req == "risky" and os.environ.get("TOLLGATE_ALLOW_RISKY", "").lower() not in (
            "1",
            "true",
            "yes",
        ):
            raise SystemExit(
                "Error: risky test — set TOLLGATE_ALLOW_RISKY=1 to run "
                "(may strand router; physical recovery may be needed)"
            )
        if req == "SSL_CF_TOKEN" and not (
            os.environ.get("TOLLGATE_CLOUDFLARE_TOKEN")
            or os.environ.get("SSL_CF_TOKEN")
        ):
            raise SystemExit("Error: TOLLGATE_CLOUDFLARE_TOKEN or SSL_CF_TOKEN required")
        if req == "TOLLGATE_SERIAL_PORT" and not os.environ.get("TOLLGATE_SERIAL_PORT"):
            raise SystemExit("Error: TOLLGATE_SERIAL_PORT not set (serial port for router)")
        if req == "cashu":
            pass  # pytest skips if cashu unavailable


def _build_pytest_cmd(entry: MigrationEntry, extra_pytest: list[str]) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *entry.pytest_nodes,
        "-v",
        "--tb=short",
    ]
    if entry.markers:
        cmd.extend(["-m", entry.markers])
    if entry.timeout:
        cmd.extend(["--timeout", str(entry.timeout)])
    cmd.append("--timeout-method=thread")
    cmd.extend(extra_pytest)
    return cmd


def _run_playwright(entry: MigrationEntry) -> int:
    spec = entry.pytest_nodes[0] if entry.pytest_nodes else "tests/protocol/captive-portal.spec.mjs"
    host = os.environ.get("TOLLGATE_SSH_HOST", "")
    env = os.environ.copy()
    env["TOLLGATE_CAPTIVE_PORTAL_HOST"] = host
    env["TOLLGATE_SSH_HOST"] = host
    cmd = ["npx", "playwright", "test", spec, "--config=tests/playwright.config.mjs"]
    print(f"{BOLD}Running:{RESET} {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT_ROOT, env=env).returncode


def _run_make_delegate(entry: MigrationEntry) -> int:
    parts = entry.delegate.split()
    if len(parts) < 2:
        raise SystemExit(f"Invalid delegate spec: {entry.delegate}")
    subdir, target = parts[0], parts[1]
    cmd = ["make", "-C", str(PROJECT_ROOT / subdir), target]
    print(f"{BOLD}Delegating:{RESET} {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode


def _run_serial_ops(target: str, args: argparse.Namespace) -> int:
    from lib.serial_console import SerialConsole

    port = os.environ.get("TOLLGATE_SERIAL_PORT", "")
    if not port:
        raise SystemExit("TOLLGATE_SERIAL_PORT not set")

    console = SerialConsole(port)
    if target == "serial-shell":
        return console.interactive_shell()
    if target == "serial-recovery":
        if not args.cmd:
            raise SystemExit("--cmd required for serial-recovery")
        print(console.exec_command(args.cmd))
        return 0
    if target == "serial-status":
        print(console.exec_command("service tollgate-wrt status; tollgate status"))
        return 0
    raise SystemExit(f"Unknown serial ops target: {target}")


def run_target(target: str, args: argparse.Namespace) -> int:
    entry = get_entry(target)
    if entry is None:
        raise SystemExit(
            f"Unknown target '{target}'. Run ./scripts/pymake.py list-migrated"
        )

    if entry.status == "ops" and target.startswith("serial-"):
        load_router_into_environ(args.router, env_subdir=entry.router_env)
        if entry.lock == "hardware":
            require_hardware_lock()
        return _run_serial_ops(target, args)

    if not entry.is_migrated:
        raise SystemExit(
            f"Target '{target}' is not migrated (status={entry.status}). "
            "Use the Makefile implementation."
        )

    _banner(entry)

    load_router_into_environ(args.router, env_subdir=entry.router_env)
    if entry.requires and "secondary_router" in entry.requires:
        secondary = resolve_secondary_for_two_router(args.router, entry.router_env)
        if secondary:
            load_router_into_environ(
                args.router,
                env_subdir=entry.router_env,
                secondary_label=secondary,
            )

    apply_cli_overrides(ssid=args.ssid, password=args.password, mint=args.mint)
    _check_requires(entry, args)

    lock_held = False
    if entry.lock == "hardware":
        if args.lock_phase:
            acquire_hardware_lock(args.lock_phase)
            lock_held = True
        else:
            require_hardware_lock()

    os.environ["TOLLGATE_USE_HARDWARE_LOCK"] = "1"

    try:
        if entry.runner == "playwright":
            return _run_playwright(entry)
        if entry.runner == "make-delegate":
            return _run_make_delegate(entry)

        cmd = _build_pytest_cmd(entry, args.extra_pytest)
        print(f"{BOLD}Running:{RESET} {' '.join(cmd)}")
        return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode
    finally:
        if lock_held:
            release_hardware_lock()


def cmd_help(_args: argparse.Namespace) -> int:
    registry = load_registry()
    print(f"{BOLD}pymake — migrated Makefile targets{RESET}\n")
    for name in sorted(registry):
        entry = registry[name]
        if entry.is_migrated or entry.is_ops:
            pytest_hint = entry.pytest or entry.runner or entry.notes
            print(f"  {name:<32} [{entry.status}]  {pytest_hint}")
    print(f"\n{YELLOW}Acquire lock first: make lock PHASE='...'{RESET}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    for name, entry in sorted(load_registry().items()):
        print(f"{name}\t{entry.status}\t{entry.pytest or entry.runner}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run migrated Makefile tests via pytest")
    parser.add_argument("target", nargs="?", help="Makefile target name (e.g. smoke-degraded)")
    parser.add_argument("--router", default=os.environ.get("TOLLGATE_ROUTER_ID", "alpha"))
    parser.add_argument("--ssid", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--mint", default=None)
    parser.add_argument("--lock-phase", default=None, help="Acquire hardware.lock for this run")
    parser.add_argument("--cmd", default=None, help="Command for serial-recovery")
    args, extra_pytest = parser.parse_known_args(argv)
    if extra_pytest and extra_pytest[0] == "--":
        extra_pytest = extra_pytest[1:]
    args.extra_pytest = extra_pytest
    if args.target in (None, "help"):
        if args.target == "help":
            return cmd_help(args)
        parser.print_help()
        return 0
    if args.target == "list-migrated":
        return cmd_list(args)
    return run_target(args.target, args)


if __name__ == "__main__":
    raise SystemExit(main())
