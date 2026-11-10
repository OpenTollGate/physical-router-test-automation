"""Transport controls for QR acceptance observations; no device is contacted."""
import subprocess
import sys
import traceback
from unittest.mock import patch
import pytest
from lib.clients.adb import ADBDevice


def test_checked_shell_preserves_empty_success():
    device = ADBDevice(serial="test-device")
    assert hasattr(device, "shell_checked"), "QR observations need exit-aware ADB"
    with patch("lib.clients.adb.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
        assert device.shell_checked("cmd wifi status", timeout=7) == ""
    run.assert_called_once_with(["adb", "-s", "test-device", "shell", "cmd wifi status"], capture_output=True, text=True, timeout=7)


@pytest.mark.parametrize("code", [1, 255])
def test_transport_failure_never_becomes_empty_observation(code):
    device = ADBDevice()
    assert hasattr(device, "shell_checked"), "QR observations need exit-aware ADB"
    with patch("lib.clients.adb.subprocess.run", return_value=subprocess.CompletedProcess([], code, "private SSID", "private serial")):
        with pytest.raises(RuntimeError, match=f"ADB observation failed.*exit {code}") as exc:
            device.shell_checked("cmd wifi status")
    assert "private" not in str(exc.value)


def test_timeout_is_infrastructure_error_not_wifi_negative():
    device = ADBDevice()
    assert hasattr(device, "shell_checked"), "QR observations need exit-aware ADB"
    with patch("lib.clients.adb.subprocess.run", side_effect=subprocess.TimeoutExpired("private command", 3)):
        with pytest.raises(RuntimeError, match="ADB observation timed out") as exc:
            device.shell_checked("cmd wifi status", timeout=3)
    assert "private" not in str(exc.value)


def test_checked_shell_returns_observation():
    device = ADBDevice()
    assert hasattr(device, "shell_checked"), "QR observations need exit-aware ADB"
    with patch("lib.clients.adb.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "Wi-Fi is enabled\n", "")):
        assert device.shell_checked("cmd wifi status") == "Wi-Fi is enabled"


@pytest.mark.parametrize("program, expected", [("pass", ""), ("print('enabled')", "enabled")])
def test_real_process_success(program, expected):
    device = ADBDevice()
    device._base = [sys.executable, "-c", program]
    assert device.shell_checked("unused") == expected


def test_real_process_nonzero_is_sanitized():
    device = ADBDevice()
    device._base = [sys.executable, "-c", "import sys; print('private'); sys.exit(7)"]
    with pytest.raises(RuntimeError, match="ADB observation failed: exit 7") as exc:
        device.shell_checked("private command")
    assert "private" not in "".join(traceback.format_exception_only(exc.type, exc.value))


def test_real_process_timeout_is_sanitized():
    device = ADBDevice()
    device._base = [sys.executable, "-c", "import time; time.sleep(10)"]
    with pytest.raises(RuntimeError, match="ADB observation timed out") as exc:
        device.shell_checked("private command", timeout=1)
    assert exc.value.__suppress_context__


def test_real_process_launch_failure_is_sanitized(tmp_path):
    device = ADBDevice()
    device._base = [str(tmp_path / "private-missing-adb")]
    with pytest.raises(RuntimeError, match="ADB observation could not start") as exc:
        device.shell_checked("private command")
    assert "private" not in str(exc.value)
    assert exc.value.__suppress_context__
