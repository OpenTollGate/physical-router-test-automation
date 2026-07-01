"""conwrt test suite — tests conwrt configure against virtual and physical routers.

Fixtures use the parent repo's Router abstraction (lib.router.Router) for SSH
access. Tests verify that conwrt's use case presets produce correct, functional
configurations on real OpenWrt systems.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")

_env_file = os.path.join(SCRIPT_DIR, ".env")
if os.path.isfile(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

CONWRT_REPO = Path(os.environ.get("CONWRT_REPO", str(Path.home() / "src" / "conwrt")))
ROUTER_HOST = os.environ.get("CONWRT_ROUTER_HOST", os.environ.get("TOLLGATE_SSH_HOST", "192.168.1.1"))
ROUTER_KEY = os.environ.get("CONWRT_ROUTER_KEY", os.environ.get("TOLLGATE_SSH_KEY", ""))
ROUTER_PORT = int(os.environ.get("CONWRT_ROUTER_PORT", "22"))
CLIENT_HOST = os.environ.get("CONWRT_CLIENT_HOST", "")


def _ssh_router(command: str, timeout: int = 30) -> str:
    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        "-o", "LogLevel=ERROR",
        "-p", str(ROUTER_PORT),
    ]
    if ROUTER_KEY:
        args.extend(["-i", os.path.expanduser(ROUTER_KEY)])
    args.append(f"root@{ROUTER_HOST}")
    args.append(command)
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def _ssh_client(command: str, timeout: int = 30) -> str:
    if not CLIENT_HOST:
        pytest.skip("CONWRT_CLIENT_HOST not set — client tests need a second host")
    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=5",
        f"root@{CLIENT_HOST}",
        command,
    ]
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


@pytest.fixture(scope="session")
def router_host():
    return ROUTER_HOST


@pytest.fixture(scope="session")
def ssh_router():
    return _ssh_router


@pytest.fixture(scope="session")
def ssh_client():
    return _ssh_client


@pytest.fixture(scope="session")
def conwrt_repo():
    if not CONWRT_REPO.exists():
        pytest.skip(f"conwrt repo not found at {CONWRT_REPO}")
    return CONWRT_REPO
