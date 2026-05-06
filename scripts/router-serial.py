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

START_MARKER = "___SERIAL_START___"
END_MARKER_PREFIX = "___SERIAL_END___:"
LOGIN_PROMPT_RE = re.compile(r"login:", re.IGNORECASE)
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

    def exec_command(self, command, timeout=DEFAULT_TIMEOUT):
        self._ensure_logged_in()
        marker_cmd = (
            f"echo '{START_MARKER}'; {command}; "
            f'echo "{END_MARKER_PREFIX}$?"'
        )
        self.ser.reset_input_buffer()
        self._send_line(marker_cmd)
        buf = ""
        deadline = time.monotonic() + timeout
        found_start = False
        found_end = False
        exit_code = -1
        output_lines = []
        while time.monotonic() < deadline:
            data = self.ser.read(READ_SIZE)
            if data:
                buf += data.decode("utf-8", errors="replace")
                lines = buf.split("\n")
                for line in lines:
                    stripped = line.strip()
                    if START_MARKER in stripped:
                        found_start = True
                        continue
                    if END_MARKER_PREFIX in stripped:
                        match = re.search(
                            re.escape(END_MARKER_PREFIX) + r"(\d+)", stripped
                        )
                        if match:
                            exit_code = int(match.group(1))
                        found_end = True
                        continue
                    if found_start and not found_end:
                        clean = stripped
                        if clean and not BUSYBOX_PROMPT_RE.match(clean):
                            output_lines.append(clean)
                if found_end:
                    return "\n".join(output_lines), exit_code
            else:
                time.sleep(POLL_INTERVAL)
        return "\n".join(output_lines), -1

    def wait_pattern(self, pattern, timeout=120):
        regex = re.compile(pattern)
        deadline = time.monotonic() + timeout
        buf = ""
        while time.monotonic() < deadline:
            data = self.ser.read(READ_SIZE)
            if data:
                text = data.decode("utf-8", errors="replace")
                buf += text
                for line in buf.split("\n"):
                    if regex.search(line):
                        return line.strip(), time.monotonic() - (deadline - timeout)
                if len(buf) > 65536:
                    buf = buf[-32768:]
            else:
                time.sleep(POLL_INTERVAL)
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

    args.func(args)


if __name__ == "__main__":
    main()
