"""Offline rail for the lab-VM stability contract (prta#21).

Pins the harness pieces that cut the wedge→hard-reset→overlay-rot chain:

* ``scripts/lab_vm_canary.sh`` — the regression canary the suite asserts
  (wedge signature, wireless-config consistency, #246 zero-byte rot) and its
  exact exit-code contract;
* ``scripts/virtual-lab.py`` — the ``net-restart`` ordered-restart path, the
  stop-poc overlay-damage tripwire, and the doctor's broadened zero-byte scan;
* ``scripts/run-local-tests.sh`` — the per-file canary hook.

Everything here runs without a lab: shell syntax checks, source-contract
greps, and a mocked-SSH functional pass over the canary's decision table.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CANARY = REPO / "scripts" / "lab_vm_canary.sh"
VLAB = REPO / "scripts" / "virtual-lab.py"
RUNNER = REPO / "scripts" / "run-local-tests.sh"


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    """Builds a fake `sshpass` whose DUT answers come from a state dir.

    The canary shells out through `sshpass -p pw ssh <opts> root@host <cmd>`.
    The fake parses the remote command and answers from files under
    ``state/`` so each failure mode can be staged deterministically.
    """
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    bin_dir.mkdir()
    state.mkdir()
    fake = bin_dir / "sshpass"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"state = Path({str(state)!r})\n"
        "cmd = sys.argv[-1]\n"
        "if cmd == 'true':\n"
        "    sys.exit(0)\n"
        "if cmd == 'test -f /etc/config/wireless':\n"
        "    sys.exit(0 if (state / 'wireless').exists() else 1)\n"
        "if cmd.startswith('ubus call network.interface.lan status'):\n"
        "    sys.stdout.write((state / 'lan_up').read_text() if (state / 'lan_up').exists() else '')\n"
        "    sys.exit(0)\n"
        "if cmd.startswith('ubus call network.wireless status'):\n"
        "    sys.stdout.write((state / 'wifi_down').read_text() if (state / 'wifi_down').exists() else '0')\n"
        "    sys.exit(0)\n"
        "if cmd.startswith('find /usr/bin'):\n"
        "    sys.stdout.write((state / 'zeros').read_text() if (state / 'zeros').exists() else '')\n"
        "    sys.exit(0)\n"
        "sys.exit(0)\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}")
    return state


def run_canary(state=None, **files):
    if state is not None:
        for name, content in files.items():
            (state / name).write_text(content)
    return subprocess.run(
        ["bash", str(CANARY), "10.99.99.1", "tollgate"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_canary_passes_on_healthy_venue(fake_ssh):
    fake_ssh.joinpath("lan_up").write_text('"up": true')
    r = run_canary()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CANARY OK" in r.stdout


def test_canary_flags_wedged_lan(fake_ssh):
    fake_ssh.joinpath("lan_up").write_text('"up": false')
    r = run_canary()
    assert r.returncode == 2
    assert "wedge" in r.stdout
    assert "DO NOT hard-reset" in r.stdout


def test_canary_flags_wireless_down_with_config_present(fake_ssh):
    fake_ssh.joinpath("lan_up").write_text('"up": true')
    fake_ssh.joinpath("wireless").write_text("config wifi-device")
    fake_ssh.joinpath("wifi_down").write_text("2")
    r = run_canary()
    assert r.returncode == 2
    assert "up:false" in r.stdout


def test_canary_flags_overlay_rot(fake_ssh):
    fake_ssh.joinpath("lan_up").write_text('"up": true')
    fake_ssh.joinpath("zeros").write_text("/usr/lib/opkg/status ")
    r = run_canary()
    assert r.returncode == 3
    assert "#246" in r.stdout


def test_canary_exit_contract_is_pinned_in_help_text():
    body = CANARY.read_text()
    for token in ("exit 2", "exit 3", "exit 4"):
        assert token in body, f"canary lost its {token} contract line"
    assert "DO NOT hard-reset" in body


def test_vlab_has_ordered_net_restart():
    src = VLAB.read_text()
    assert "def net_restart(" in src
    # the ordered sequence: wifi down first when wireless is configured,
    # bounded SSH wait, wifi up only afterwards
    assert "wifi down" in src
    assert "wifi up" in src
    assert "did not answer SSH within 90s" in src
    assert "lab_vm_canary.sh" in src


def test_vlab_stop_poc_checks_overlay():
    src = VLAB.read_text()
    assert "qemu-img check" in src
    assert "OVERLAY DAMAGE" in src
    assert "start-poc --fresh" in src


def test_vlab_doctor_scans_all_system_trees():
    src = VLAB.read_text()
    assert "/usr/bin /usr/sbin /usr/lib /lib -type f -size 0" in src
    # opkg metadata (legitimately-empty .list files) must be excluded
    assert 'grep -v "/opkg/info/"' in src


def test_runner_asserts_canary_per_file():
    src = RUNNER.read_text()
    assert "assert_lab_vm_canary" in src
    assert 'assert_lab_vm_canary "${t}" || exit 1' in src


def test_shell_scripts_parse():
    for script in (CANARY, RUNNER):
        r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert r.returncode == 0, f"{script}: {r.stderr}"
