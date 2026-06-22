import socket
import subprocess
import pytest

pytestmark = [pytest.mark.api, pytest.mark.smoke]


def _port_open(host, port, timeout=5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _grab_banner(host, port, timeout=5):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)
        try:
            banner = s.recv(256).decode("utf-8", errors="replace")
        except socket.timeout:
            banner = ""
        finally:
            s.close()
        return banner
    except (socket.timeout, ConnectionRefusedError, OSError):
        return ""


def test_port_2222_listening(gateway_host):
    assert _port_open(gateway_host, 2222), \
        f"Port 2222 (tollgate SSH) not open on {gateway_host}"


def test_ssh_banner_on_2222(gateway_host):
    banner = _grab_banner(gateway_host, 2222)
    assert banner, f"No banner received from {gateway_host}:2222"
    assert "SSH-2.0" in banner, f"Unexpected SSH banner: {banner.strip()}"


def test_invalid_username_rejected(gateway_host):
    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "-o", "PasswordAuthentication=no",
        "-o", "PreferredAuthentications=none",
        "-p", "2222",
        f"nonexistent_user_xyz@{gateway_host}",
        "echo should_fail",
    ]
    r = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert r.returncode != 0, "SSH with invalid user should fail"


def test_admin_ssh_on_port_22(gateway_ssh):
    r = gateway_ssh("echo admin_ssh_ok", timeout=15)
    assert r.returncode == 0, f"Admin SSH on port 22 failed: {r.stderr[:200]}"
    assert "admin_ssh_ok" in r.stdout, f"Unexpected response: {r.stdout}"
