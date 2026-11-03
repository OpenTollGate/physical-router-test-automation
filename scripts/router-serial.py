#!/usr/bin/env python3
"""
router-serial.py — Serial console automation for OpenWrt routers.

Provides reliable command execution, pattern waiting, and boot log capture
over USB-TTL serial connections to GL.iNet MT3000 routers.

Usage:
    router-serial exec    --port /dev/serial-alpha "command"
    router-serial wait    --port /dev/serial-alpha --pattern "Merchant ready" --timeout 120
    router-serial bootlog --port /dev/serial-alpha --timeout 180
    router-serial watch   --port /dev/serial-alpha
    router-serial login   --port /dev/serial-alpha
"""

import argparse
import re
import sys
import time

import serial

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from lib.protected_devices import (  # noqa: E402  (path set above)
    ProtectedDeviceError,
    assert_not_protected,
    is_protected,
)

START_MARKER = "___SERIAL_START___"
END_MARKER_PREFIX = "___SERIAL_END___:"
LOGIN_PROMPT_RE = re.compile(r"login:", re.IGNORECASE)
#: The real sentinel: the whole line is the marker plus the exit status. The
#: board emits "___SERIAL_END___:0". Anchored, because the echoed command line
#: contains the marker text as a literal and must never be mistaken for it.
_END_PATTERN = re.compile(re.escape(END_MARKER_PREFIX) + r"(\d+)$")
#: The echoed command line, whose sentinel text still holds an unexpanded "$?".
_END_ECHO_PATTERN = re.compile(re.escape(END_MARKER_PREFIX) + r".*\$")
SHELL_PROMPT_RE = re.compile(r"[#\$]\s*$")
BUSYBOX_PROMPT_RE = re.compile(r"\S+@[^\s#]+:[^\s#]*#\s*$")
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 30
LOGIN_USERNAME = "root"
READ_SIZE = 4096
POLL_INTERVAL = 0.05
SETTLE_DELAY = 0.3


class SerialConnection:
    def __init__(self, port, baud=DEFAULT_BAUD, timeout=1.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser = None

    def __enter__(self):
        self.ser = serial.Serial(
            port=self.port,
            baudrate=self.baud,
            timeout=self.timeout,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
        time.sleep(SETTLE_DELAY)
        self.ser.reset_input_buffer()
        return self

    def __exit__(self, *args):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _read_until_prompt(self, timeout, pattern=None):
        deadline = time.monotonic() + timeout
        buf = ""
        while time.monotonic() < deadline:
            data = self.ser.read(READ_SIZE)
            if data:
                buf += data.decode("utf-8", errors="replace")
                if pattern:
                    for line in buf.split("\n"):
                        if re.search(pattern, line):
                            return buf
            else:
                time.sleep(POLL_INTERVAL)
        return buf

    def _send_line(self, line):
        self.ser.write((line + "\n").encode("utf-8"))
        self.ser.flush()
        time.sleep(POLL_INTERVAL * 2)

    def _ensure_logged_in(self):
        self._send_line("")
        time.sleep(0.5)
        data = self.ser.read(READ_SIZE)
        if not data:
            return
        text = data.decode("utf-8", errors="replace")
        if LOGIN_PROMPT_RE.search(text):
            self._send_line(LOGIN_USERNAME)
            time.sleep(1.0)
            self.ser.read(READ_SIZE)

    def assert_not_protected_target(self) -> None:
        """Refuse to drive a protected production/control device.

        The device identifies itself; we do not trust the port name or an
        operator-supplied label. Both queries are read-only. An unreadable
        identity is NOT a licence to proceed on a port that looks lab-like —
        but it is also not proof of protection, so an unreadable device is
        allowed only when the caller has not asserted it is a test target.
        """
        model, model_rc = self.exec_command("cat /tmp/sysinfo/model", timeout=10)
        board, board_rc = self.exec_command("cat /tmp/sysinfo/board_name", timeout=10)
        identity = {
            "model": (model or "").strip(),
            "board_name": (board or "").strip(),
        }
        if model_rc != 0 and board_rc != 0:
            raise ProtectedDeviceError(
                f"could not read the identity of {self.port!r} "
                f"(model rc={model_rc}, board rc={board_rc}); refusing rather than "
                "assuming this is a lab device. A production gateway must never be "
                "driven because its identity was unreadable."
            )
        assert_not_protected(**{k: v for k, v in identity.items() if v})
        self.identity = identity

    def exec_command(self, command, timeout=DEFAULT_TIMEOUT):
        self._ensure_logged_in()
        marker_cmd = (
            f"echo '{START_MARKER}'; {command}; "
            f'echo "{END_MARKER_PREFIX}$?"'
        )
        self.ser.reset_input_buffer()
        self._send_line(marker_cmd)
        # Consume COMPLETE lines from a pending buffer. Re-splitting the whole
        # accumulated buffer on every read reprocessed every earlier line and
        # treated a half-received line as output — which truncated a real
        # answer mid-token on the MT3000.
        pending = ""
        deadline = time.monotonic() + timeout
        found_start = False
        exit_code = -1
        output_lines = []
        while time.monotonic() < deadline:
            data = self.ser.read(READ_SIZE)
            if not data:
                time.sleep(POLL_INTERVAL)
                continue
            pending += data.decode("utf-8", errors="replace")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                stripped = line.strip()
                # ANCHORED matches only. The board echoes our command line back,
                # and that echo CONTAINS both markers as literals
                # ("echo '___SERIAL_START___'; ... echo \"___SERIAL_END___:$?\"").
                # A substring test therefore matched the echo and treated it as
                # the sentinel, returning rc=-1 with no output — measured on a
                # real board, where the first two of three sequential commands
                # failed and the third only worked because an earlier read had
                # already consumed the echo.
                if stripped == START_MARKER:
                    found_start = True
                    continue
                end_match = _END_PATTERN.match(stripped)
                if end_match:
                    exit_code = int(end_match.group(1))
                    return "\n".join(output_lines), exit_code
                if end_match is None and _END_ECHO_PATTERN.match(stripped):
                    # A wrapped echo of the sentinel's own text: still not output.
                    continue
                if found_start:
                    if stripped and not BUSYBOX_PROMPT_RE.match(stripped):
                        output_lines.append(stripped)
        return "\n".join(output_lines), -1

    def wait_pattern(self, pattern, timeout=120):
        regex = re.compile(pattern)
        deadline = time.monotonic() + timeout
        # Same rule as exec_command: only a COMPLETE line can match. Matching a
        # half-received line lets the caller act on a truncated token.
        pending = ""
        while time.monotonic() < deadline:
            data = self.ser.read(READ_SIZE)
            if not data:
                time.sleep(POLL_INTERVAL)
                continue
            pending += data.decode("utf-8", errors="replace")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                if regex.search(line):
                    return line.strip(), time.monotonic() - (deadline - timeout)
            if len(pending) > 65536:
                pending = pending[-32768:]
        return None, timeout

    def capture_bootlog(self, timeout=180, end_pattern="login:"):
        regex = re.compile(end_pattern, re.IGNORECASE)
        deadline = time.monotonic() + timeout
        lines = []
        buf = ""
        while time.monotonic() < deadline:
            data = self.ser.read(READ_SIZE)
            if data:
                text = data.decode("utf-8", errors="replace")
                buf += text
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    clean = line.rstrip("\r")
                    if clean:
                        lines.append(clean)
                    if regex.search(clean):
                        elapsed = time.monotonic() - (deadline - timeout)
                        return lines, elapsed
            else:
                time.sleep(POLL_INTERVAL)
        elapsed = time.monotonic() - (deadline - timeout)
        return lines, elapsed


def cmd_exec(args):
    with SerialConnection(args.port, args.baud) as conn:
        output, exit_code = conn.exec_command(args.command, timeout=args.timeout)
        if output:
            print(output)
        sys.exit(exit_code if exit_code >= 0 else 1)


def cmd_wait(args):
    with SerialConnection(args.port, args.baud) as conn:
        conn.ser.reset_input_buffer()
        result, elapsed = conn.wait_pattern(args.pattern, timeout=args.timeout)
        if result:
            print(result)
            print(f"\nMatched after {elapsed:.1f}s", file=sys.stderr)
            sys.exit(0)
        else:
            print(f"Timeout ({args.timeout}s) waiting for: {args.pattern}", file=sys.stderr)
            sys.exit(1)


def cmd_bootlog(args):
    with SerialConnection(args.port, args.baud, timeout=0.5) as conn:
        print(f"Waiting for boot log (timeout {args.timeout}s, end pattern: '{args.end_pattern}')...",
              file=sys.stderr)
        print("Send 'reboot' now or power-cycle the router.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        lines, elapsed = conn.capture_bootlog(
            timeout=args.timeout, end_pattern=args.end_pattern
        )
        for line in lines:
            print(line)
        print("=" * 60, file=sys.stderr)
        print(f"Boot complete after {elapsed:.1f}s ({len(lines)} lines)", file=sys.stderr)


def cmd_watch(args):
    try:
        with SerialConnection(args.port, args.baud, timeout=0.1) as conn:
            print(f"Watching {args.port} (Ctrl+C to stop)...", file=sys.stderr)
            while True:
                data = conn.ser.read(READ_SIZE)
                if data:
                    sys.stdout.write(data.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)


def cmd_login(args):
    with SerialConnection(args.port, args.baud) as conn:
        conn.ser.reset_input_buffer()
        conn._send_line("")
        time.sleep(0.5)
        data = conn.ser.read(READ_SIZE)
        if data:
            text = data.decode("utf-8", errors="replace")
            print(text, end="")
            if LOGIN_PROMPT_RE.search(text):
                conn._send_line(LOGIN_USERNAME)
                time.sleep(1.0)
                resp = conn.ser.read(READ_SIZE)
                if resp:
                    print(resp.decode("utf-8", errors="replace"), end="")
                print("Logged in.", file=sys.stderr)
            else:
                print("Already at shell prompt (or unknown state).", file=sys.stderr)
        else:
            print("No response from serial port.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Serial console automation for OpenWrt routers"
    )
    parser.add_argument("--port", required=True, help="Serial port (e.g. /dev/serial-alpha)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument(
        "--identity-only",
        action="store_true",
        help="report the attached device's identity and exit without running the subcommand",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    p_exec = subparsers.add_parser("exec", help="Execute a command and capture output")
    p_exec.add_argument("cmd", nargs="?", help="Command to execute")
    p_exec.add_argument("--command", dest="command_alt", help="Command to execute (alt flag)")
    p_exec.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Command timeout in seconds (default: {DEFAULT_TIMEOUT})")
    p_exec.set_defaults(func=cmd_exec)

    p_wait = subparsers.add_parser("wait", help="Wait for a pattern in serial output")
    p_wait.add_argument("--pattern", required=True, help="Regex pattern to wait for")
    p_wait.add_argument("--timeout", type=int, default=120,
                        help="Timeout in seconds (default: 120)")
    p_wait.set_defaults(func=cmd_wait)

    p_boot = subparsers.add_parser("bootlog", help="Capture full boot log until end pattern")
    p_boot.add_argument("--timeout", type=int, default=180,
                        help="Boot timeout in seconds (default: 180)")
    p_boot.add_argument("--end-pattern", default="login:",
                        help="Pattern that signals boot is complete (default: 'login:')")
    p_boot.set_defaults(func=cmd_bootlog)

    p_watch = subparsers.add_parser("watch", help="Interactive serial output (Ctrl+C to stop)")
    p_watch.set_defaults(func=cmd_watch)

    p_login = subparsers.add_parser("login", help="Send login credentials if at login prompt")
    p_login.set_defaults(func=cmd_login)

    args = parser.parse_args()

    if args.identity_only:
        try:
            with SerialConnection(args.port, args.baud) as conn:
                model, _ = conn.exec_command("cat /tmp/sysinfo/model", timeout=10)
                board, _ = conn.exec_command("cat /tmp/sysinfo/board_name", timeout=10)
            ident = {"model": (model or "").strip(), "board_name": (board or "").strip()}
            print(f"model:      {ident['model'] or '?'}")
            print(f"board_name: {ident['board_name'] or '?'}")
            print(f"protected:  {is_protected(**{k: v for k, v in ident.items() if v})}")
            sys.exit(0)
        except Exception as e:  # noqa: BLE001
            print(f"identity unreadable: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(78)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "exec":
        cmd_text = args.cmd or args.command_alt
        if not cmd_text:
            print("Error: command is required for exec", file=sys.stderr)
            p_exec.print_help()
            sys.exit(1)
        args.command = cmd_text
    else:
        args.command = args.command

    # Protected-device gate. Every subcommand passes through here, including
    # the ones that flash, power-cycle or run state prep -- so the check sits at
    # the transport entry, not in each verb. --identity-only exists so a caller
    # can ask what a port is without being allowed to drive it.
    if not getattr(args, "identity_only", False):
        try:
            with SerialConnection(args.port, args.baud) as conn:
                conn.assert_not_protected_target()
                identity = getattr(conn, "identity", {})
        except ProtectedDeviceError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            sys.exit(78)
        except Exception as e:  # noqa: BLE001 - unreadable identity is a refusal too
            print(f"REFUSED: could not verify {args.port!r}: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(78)
        if identity.get("model") or identity.get("board_name"):
            print(f"==> Target: {identity.get('model','?')} ({identity.get('board_name','?')})", file=sys.stderr)

    args.func(args)


if __name__ == "__main__":
    main()
