"""Serial reader for MeshCore boot log verification."""

import os
import time
import subprocess
from typing import Optional


class SerialReader:
    """Read serial output from an ESP32 board, with boot detection."""

    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate

    def reset_and_capture(self, duration: int = 10) -> str:
        """Reset the board via DTR/RTS and capture serial output.

        Returns the full captured text.
        """
        script = f"""
import serial, time, sys

port = "{self.port}"
baud = {self.baudrate}
duration = {duration}

try:
    s = serial.Serial(port, baud, timeout=0.5)
    # ESP32 hard reset via DTR/RTS
    s.dtr = False
    s.rts = True
    time.sleep(0.1)
    s.dtr = True
    s.rts = False
    time.sleep(0.1)
    s.dtr = False

    start = time.time()
    buf = b""
    while time.time() - start < duration:
        data = s.read(256)
        if data:
            buf += data
    s.close()
    sys.stdout.buffer.write(buf)
except Exception as e:
    sys.stderr.write(f"Serial error: {{e}}\\n")
    sys.exit(1)
"""
        result = subprocess.run(
            ["uv", "run", "--with", "pyserial", "python3", "-c", script],
            capture_output=True,
            text=False,  # bytes
            timeout=duration + 30,
        )
        if result.returncode != 0:
            return result.stderr.decode(errors="replace")
        return result.stdout.decode(errors="replace")

    def capture(self, duration: int = 5) -> str:
        """Capture serial output without reset."""
        script = f"""
import serial, time, sys

port = "{self.port}"
baud = {self.baudrate}
duration = {duration}

try:
    s = serial.Serial(port, baud, timeout=0.5)
    start = time.time()
    buf = b""
    while time.time() - start < duration:
        data = s.read(256)
        if data:
            buf += data
    s.close()
    sys.stdout.buffer.write(buf)
except Exception as e:
    sys.stderr.write(f"Serial error: {{e}}\\n")
    sys.exit(1)
"""
        result = subprocess.run(
            ["uv", "run", "--with", "pyserial", "python3", "-c", script],
            capture_output=True,
            text=False,
            timeout=duration + 30,
        )
        if result.returncode != 0:
            return result.stderr.decode(errors="replace")
        return result.stdout.decode(errors="replace")

    def check_boot(self, capture: str) -> dict:
        """Verify boot sequence from captured serial output.

        Returns dict with check results.
        """
        checks = {
            "boot_rom": "ESP-ROM:esp32c3" in capture,
            "flash_boot": "SPI_FAST_FLASH_BOOT" in capture,
            "entry_point": "entry 0x403cc710" in capture,
            "radio_init_ok": "Repeater ID:" in capture or "Companion" in capture,
            "has_identity": bool(
                # Ed25519 key (64 hex chars after "ID:" or "identity")
                any(
                    line.strip().startswith(("Repeater ID:", "Companion ID:"))
                    and len(line.split(":")[-1].strip()) >= 32
                    for line in capture.split("\n")
                )
            ),
        }
        checks["all_passed"] = all(checks.values())
        return checks
