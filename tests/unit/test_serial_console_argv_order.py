"""A pre-existing bug in lib/serial_console.py, pinned.

`scripts/router-serial.py` declares ``--port``/``--baud`` as GLOBAL options
(``parser.add_argument("--port", required=True)`` before the subparsers), so
they must appear before the subcommand. Every method in ``SerialConsole`` built
the command line as ``[<subcommand>, "--port", port, ...]``, which argparse
rejects with:

    router-serial.py: error: the following arguments are required: --port

Measured 2026-11-03: ``exec_command`` had never worked on this path — it raised
RuntimeError for every call. The bug was invisible because ``lib/fleet_health``
did not exist to call it, and the bench probe drives the script directly with
the correct ordering.

These tests pin the *argument order* without opening a serial port: they inject
a runner and assert where ``--port`` sits relative to the subcommand.
"""
from __future__ import annotations

import subprocess

import pytest

from lib import serial_console


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


@pytest.fixture()
def recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec)
    return rec


def _arg_positions(cmd: list[str]) -> tuple[int, int | None]:
    """(index of --port, index of the subcommand), or (-1, None)."""
    port_at = cmd.index("--port") if "--port" in cmd else -1
    sub_at = next(
        (i for i, a in enumerate(cmd) if a in {"exec", "wait", "bootlog", "watch", "login"}),
        None,
    )
    return port_at, sub_at


class TestPortComesBeforeSubcommand:
    def test_exec_command_puts_port_before_exec(self, recorder):
        console = serial_console.SerialConsole("/dev/serial-test", 115200)
        console.exec_command("uptime", timeout=5)
        assert len(recorder.calls) == 1
        cmd = recorder.calls[0]
        port_at, sub_at = _arg_positions(cmd)
        assert port_at != -1, f"no --port in {cmd}"
        assert sub_at is not None, f"no subcommand in {cmd}"
        assert port_at < sub_at, f"--port must precede the subcommand: {cmd}"
        assert cmd[port_at + 1] == "/dev/serial-test"

    def test_wait_pattern_puts_port_before_wait(self, recorder):
        console = serial_console.SerialConsole("/dev/serial-test", 115200)
        console.wait_pattern("root@", timeout=5)
        port_at, sub_at = _arg_positions(recorder.calls[0])
        assert port_at != -1 and sub_at is not None and port_at < sub_at

    def test_bootlog_puts_port_before_bootlog(self, recorder):
        console = serial_console.SerialConsole("/dev/serial-test", 115200)
        console.bootlog(timeout=5)
        port_at, sub_at = _arg_positions(recorder.calls[0])
        assert port_at != -1 and sub_at is not None and port_at < sub_at

    def test_the_payload_command_is_still_last(self, recorder):
        """Ordering must not be fixed by moving the command into an option."""
        console = serial_console.SerialConsole("/dev/serial-test", 115200)
        console.exec_command("ndsctl clients", timeout=5)
        cmd = recorder.calls[0]
        assert cmd[-1] == "ndsctl clients"

    def test_baud_is_passed_when_non_default(self, recorder):
        console = serial_console.SerialConsole("/dev/serial-test", 57600)
        console.exec_command("uptime", timeout=5)
        cmd = recorder.calls[0]
        assert "--baud" in cmd
        assert cmd[cmd.index("--baud") + 1] == "57600"
