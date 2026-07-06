"""Test 1: Boot + radio init + identity generation."""

import time
import pytest

pytestmark = pytest.mark.smoke


class TestBootAndRadioInit:

    @pytest.mark.boot
    def test_board_a_boots(self, reader_a):
        """Board A: boots, radio inits, identity generated."""
        capture = reader_a.reset_and_capture(duration=10)
        assert capture, "No serial output from board A"

        checks = reader_a.check_boot(capture)
        assert checks["boot_rom"], f"ESP32-C3 boot ROM not found in output:\n{capture[:500]}"
        assert checks["flash_boot"], "SPI flash boot not detected"
        assert checks["entry_point"], "Firmware entry point not reached"

    @pytest.mark.boot
    def test_board_b_boots(self, reader_b):
        """Board B: boots, radio inits, identity generated."""
        capture = reader_b.reset_and_capture(duration=10)
        assert capture, "No serial output from board B"

        checks = reader_b.check_boot(capture)
        assert checks["boot_rom"], f"ESP32-C3 boot ROM not found in output:\n{capture[:500]}"
        assert checks["flash_boot"], "SPI flash boot not detected"
        assert checks["entry_point"], "Firmware entry point not reached"

    @pytest.mark.boot
    def test_board_a_identity(self, reader_a):
        """Board A generates an Ed25519 identity."""
        # Wait a bit for identity generation to complete
        time.sleep(5)
        capture = reader_a.capture(duration=10)
        assert capture, "No serial output"

        # Look for identity hash (hex string)
        assert any(
            len(line.strip()) >= 32 and all(c in "0123456789abcdefABCDEF" for c in line.strip())
            for line in capture.split("\n")
        ), f"No Ed25519 identity hash found in output:\n{capture[:500]}"

    @pytest.mark.boot
    def test_board_b_identity(self, reader_b):
        """Board B generates an Ed25519 identity."""
        time.sleep(5)
        capture = reader_b.capture(duration=10)
        assert capture, "No serial output"

        assert any(
            len(line.strip()) >= 32 and all(c in "0123456789abcdefABCDEF" for c in line.strip())
            for line in capture.split("\n")
        ), f"No Ed25519 identity hash found in output:\n{capture[:500]}"
