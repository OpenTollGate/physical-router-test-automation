"""Transport controls for QR acceptance observations; no device is contacted."""
import subprocess
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
