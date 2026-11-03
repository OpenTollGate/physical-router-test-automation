"""
Serial console — delegates to tollgate_lab when available.

Canonical: tollgate_lab/drivers/serial_console.py
"""

# `from __future__` must be the first statement after the module docstring.
# This file previously had the delegation try/except ABOVE the future import,
# which is a hard SyntaxError ("from __future__ imports must occur at the
# beginning of the file") — the module could not be imported at all, so every
# test importing it died at collection. `ast.parse()` does NOT catch this;
# only `compile()` does, which is why it survived unnoticed. Guarded by
# tests/unit/test_serial_console_importable.py.
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    from tollgate_lab.drivers.serial_console import *  # noqa: F401,F403
except ImportError:
    pass  # Fall through to local implementation

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SERIAL_SCRIPT = _PROJECT_ROOT / "scripts" / "router-serial.py"


class SerialConsole:
    def __init__(self, port: str, baud: int = 115200) -> None:
        self.port = port
        self.baud = baud

    def _python(self) -> str:
        venv = os.environ.get("TOLLGATE_PYTHON_VENV", "")
        if venv:
            candidate = Path(venv).expanduser() / "bin" / "python3"
            if candidate.is_file():
                return str(candidate)
        return sys.executable

    def _argv(self, args: list[str]) -> list[str]:
        """Build the argv with GLOBAL options before the subcommand.

        ``scripts/router-serial.py`` declares ``--port``/``--baud`` on the parent
        parser (``parser.add_argument("--port", required=True)`` appears before
        the subparsers), so argparse requires them BEFORE the subcommand. Every
        method here used to build ``[<subcommand>, "--port", ...]``, which
        argparse rejects with "the following arguments are required: --port" —
        i.e. ``exec_command`` never worked. Measured 2026-11-03; pinned by
        tests/unit/test_serial_console_argv_order.py.
        """
        return [
            self._python(),
            str(_SERIAL_SCRIPT),
            "--port",
            self.port,
            "--baud",
            str(self.baud),
            *args,
        ]

    def _run(self, args: list[str], *, timeout: int | None = 30) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self._argv(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_PROJECT_ROOT,
        )

    def exec_command(self, command: str, timeout: int = 30) -> str:
        result = self._run(["exec", command], timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(
                f"serial exec failed ({result.returncode}): {result.stderr.strip()[:500]}"
            )
        return result.stdout

    def wait_pattern(self, pattern: str, timeout: int = 120) -> str:
        result = self._run(
            ["wait", "--pattern", pattern, "--timeout", str(timeout)],
            timeout=timeout + 10,
        )
        return result.stdout + result.stderr

    def bootlog(self, timeout: int = 180, end_pattern: str = "login:") -> str:
        result = self._run(
            ["bootlog", "--timeout", str(timeout), "--end-pattern", end_pattern],
            timeout=timeout + 30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"bootlog failed: {result.stderr.strip()[:500]}")
        return result.stdout

    def reboot_and_bootlog(self, timeout: int = 180) -> str:
        try:
            self.exec_command("reboot", timeout=15)
        except Exception:
            pass
        return self.bootlog(timeout=timeout)

    def interactive_shell(self) -> int:
        """Launch picocom or router-serial watch."""
        try:
            return subprocess.run(
                [self._python(), str(_SERIAL_SCRIPT), "watch", "--port", self.port],
                cwd=_PROJECT_ROOT,
            ).returncode
        except FileNotFoundError:
            pass
        for binary in ("picocom", "screen"):
            try:
                return subprocess.run(
                    [binary, self.port, "b115200"],
                    cwd=_PROJECT_ROOT,
                ).returncode
            except FileNotFoundError:
                continue
        print("Install picocom or use: python3 scripts/router-serial.py watch --port", self.port)
        return 1
