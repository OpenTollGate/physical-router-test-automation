"""Opt-in full wizard-deploy smoke against a live router.

Runs the REAL installer binary's ``/api/deploy`` against a router and asserts the
job ends ``done`` or ``pending`` — NEVER ``failed`` on a healthy merchant. This is
the end-to-end companion to ``test_installer_health_probe.py`` (probe-only,
default); it exercises flash/install/uplink/health together.

Gated behind ``TOLLGATE_ENABLE_DEPLOY_SMOKE=1`` because it is slow and mutating
(it (re)installs TollGate on the target). Set:

    TOLLGATE_ENABLE_DEPLOY_SMOKE=1
    TOLLGATE_INSTALLER_BIN=/path/to/tollgate-installer-linux-amd64
    TOLLGATE_SSH_HOST=192.168.2.14  TOLLGATE_SSH_PASSWORD=TollGate
    TG_LNURL=tollgate@minibits.cash

The installer is started with ``-trust-host-key`` derived from the router so the
deploy session and the health probe both reach the auth stage.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request

import pytest

pytestmark = [
    pytest.mark.story("O5"),
    pytest.mark.api,
    pytest.mark.hardware,
    pytest.mark.slow,
    pytest.mark.destructive,
]

INSTALLER_BIN = os.environ.get("TOLLGATE_INSTALLER_BIN", "")
LNURL = os.environ.get("TG_LNURL", "tollgate@minibits.cash")
SSH_PORT = int(os.environ.get("TOLLGATE_SSH_PORT", "22"))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _router_hostkey_fingerprint(host: str) -> str:
    """The router's SSH host-key fingerprint as the installer will negotiate it.

    The installer prefers ed25519 (see sshHostKeyAlgorithms), so pin THAT print —
    pinning the RSA one aborts the handshake as an untrusted key and the deploy
    fails at step 0.
    """
    for keytype in ("ed25519", "rsa"):
        out = subprocess.run(["ssh-keyscan", "-t", keytype, host],
                             capture_output=True, text=True, timeout=30).stdout
        if not out.strip():
            continue
        p = subprocess.run(["ssh-keygen", "-lf", "-"], input=out, capture_output=True, text=True)
        parts = p.stdout.split()
        if len(parts) >= 2:
            return parts[1]
    pytest.skip(f"could not read {host}'s SSH host key fingerprint")


def _post(base: str, path: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get(base: str, path: str, timeout: int = 30) -> dict:
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.load(r)


@pytest.fixture
def deploy_enabled():
    if os.environ.get("TOLLGATE_ENABLE_DEPLOY_SMOKE") != "1":
        pytest.skip("opt-in: set TOLLGATE_ENABLE_DEPLOY_SMOKE=1 to run the full deploy smoke")
    if not os.path.isfile(INSTALLER_BIN):
        pytest.skip("set TOLLGATE_INSTALLER_BIN to the installer binary to exercise")


def test_full_deploy_reaches_done_or_pending_not_failed(router, deploy_enabled):
    host = router.host
    fp = _router_hostkey_fingerprint(host)
    port = _free_port()
    log = open(f"/tmp/installer-deploy-smoke-{port}.log", "w")
    proc = subprocess.Popen([INSTALLER_BIN, "-port", str(port), "-ssh-port", str(SSH_PORT),
                             "-trust-host-key", fp],
                            stdout=log, stderr=log, stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                _get(base, "/api/config")
                break
            except Exception:
                time.sleep(1)
        else:
            pytest.fail("the installer UI did not come up")

        resp = _post(base, "/api/deploy",
                     {"ip": host, "password": os.environ.get("TOLLGATE_SSH_PASSWORD", ""),
                      "mode": "wan", "lnurl": LNURL})
        jid = resp.get("job_id") or resp.get("id")
        assert jid, f"/api/deploy returned no job_id: {resp}"

        status = {}
        end = time.time() + 20 * 60
        while time.time() < end:
            status = _get(base, f"/api/status/{jid}")
            if str(status.get("status", "")).lower() in ("done", "pending", "failed", "error"):
                break
            time.sleep(5)
        state = str(status.get("status", "")).lower()
        assert state in ("done", "pending"), (
            f"the deploy ended {state!r} on a healthy router — the probe/gate is still "
            f"failing a working merchant:\n{json.dumps(status)[:1200]}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
