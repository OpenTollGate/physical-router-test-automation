"""pytest configuration and fixtures for MeshCore smoke tests."""

import os
import sys
import json
import subprocess
import time
from pathlib import Path
from datetime import datetime, timezone

import pytest

# Add parent dir to path
sys.path.insert(0, str(Path(__file__).parent))

from boards import BOARDS, LOCK_SCRIPT, MESHCORE_DIR
from lib.meshcore_cli import MeshCoreCLI
from lib.serial_reader import SerialReader


def pytest_addoption(parser):
    parser.addoption("--flash", action="store_true", default=True,
                      help="Flash firmware before tests (default: True)")
    parser.addoption("--no-flash", action="store_true",
                      help="Skip flashing, use existing firmware")
    parser.addoption("--board-a", default="/dev/ttyACM1",
                      help="Serial port for board A")
    parser.addoption("--board-b", default="/dev/ttyACM2",
                      help="Serial port for board B")


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: quick smoke tests")
    config.addinivalue_line("markers", "boot: boot verification")
    config.addinivalue_line("markers", "discovery: advert discovery")
    config.addinivalue_line("markers", "chat: encrypted chat")
    config.addinivalue_line("markers", "repeater: repeater mode")


# ─── Board Lock Management ───

_lock_held = False


def acquire_board_lock(purpose="meshcore-smoke-test"):
    """Acquire the balloon board mutex lock."""
    global _lock_held
    result = subprocess.run(
        ["python3", LOCK_SCRIPT, "acquire", "--purpose", purpose, "--timeout", "30"],
        capture_output=True, text=True, timeout=40,
    )
    if result.returncode != 0:
        pytest.skip(f"Could not acquire board lock: {result.stderr}")
    _lock_held = True
    return True


def release_board_lock():
    """Release the board mutex lock."""
    global _lock_held
    if _lock_held:
        subprocess.run([LOCK_SCRIPT, "release", "--force"], capture_output=True, timeout=10)
        _lock_held = False


@pytest.fixture(scope="session", autouse=True)
def board_lock(request):
    """Session-scoped fixture that acquires/releases the board lock."""
    acquire_board_lock("meshcore-2device-smoke-test")
    yield
    release_board_lock()


# ─── Flashing ───

@pytest.fixture(scope="session", autouse=True)
def flash_boards(request):
    """Flash both boards with companion_radio_usb firmware."""
    if request.config.getoption("--no-flash"):
        yield
        return

    port_a = request.config.getoption("--board-a")
    port_b = request.config.getoption("--board-b")

    for port, label in [(port_a, "A"), (port_b, "B")]:
        result = subprocess.run(
            ["pio", "run", "-t", "upload",
             "-e", "LR2021_companion_radio_usb",
             "--upload-port", port],
            capture_output=True, text=True,
            cwd=MESHCORE_DIR,
            timeout=180,
        )
        if result.returncode != 0:
            pytest.skip(f"Failed to flash board {label} on {port}: {result.stderr[-200:]}")
        time.sleep(2)  # let board settle after flash

    yield


# ─── CLI Fixtures ───

@pytest.fixture(scope="session")
def cli_a(request) -> MeshCoreCLI:
    """MeshCore CLI for board A."""
    port = request.config.getoption("--board-a")
    return MeshCoreCLI(port)


@pytest.fixture(scope="session")
def cli_b(request) -> MeshCoreCLI:
    """MeshCore CLI for board B."""
    port = request.config.getoption("--board-b")
    return MeshCoreCLI(port)


@pytest.fixture(scope="session")
def reader_a(request) -> SerialReader:
    """Serial reader for board A."""
    port = request.config.getoption("--board-a")
    return SerialReader(port)


@pytest.fixture(scope="session")
def reader_b(request) -> SerialReader:
    """Serial reader for board B."""
    port = request.config.getoption("--board-b")
    return SerialReader(port)


# ─── Result Persistence ───

RESULTS_DIR = Path(__file__).parent / "results"


@pytest.fixture(scope="session", autouse=True)
def results_dir():
    """Ensure results directory exists."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return RESULTS_DIR / timestamp


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Save serial captures and test metadata after session."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    result_file = RESULTS_DIR / f"{timestamp}_smoke.json"

    # Gather terminal report
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    summary = {
        "timestamp": timestamp,
        "exitstatus": exitstatus,
        "tests": {},
    }

    if reporter:
        for test_id, report in reporter.stats.get("passed", []):
            summary["tests"][test_id] = "passed"
        for test_id, report in reporter.stats.get("failed", []):
            summary["tests"][test_id] = "failed"
        for test_id, report in reporter.stats.get("skipped", []):
            summary["tests"][test_id] = "skipped"

    result_file.write_text(json.dumps(summary, indent=2))
