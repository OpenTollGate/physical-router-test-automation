#!/usr/bin/env python3
"""conwrt Rust CLI test suite — PRTA drives the conwrt 2.0 engine.

This is the hardware-verification lane for the conwrt Rust migration
(docs/rust-migration.md configure row): PRTA exercises the real
`conwrt` binary — plan, fingerprint, apply — against mock scenarios
(host-only) and against a live OpenWrt venue (physical router or QEMU
VM), including a real WireGuard tunnel round-trip against the lab WG
test endpoint.

Tiers
-----
- HOST-ONLY (always run, no router): `conwrt models` corpus validation,
  `conwrt plan` rendering for the migrated profiles (mock provider,
  I/O-free), param-validation and unconfirmed-identity refusal gates.
- ROUTER VENUE (auto-skipped without a reachable target): `conwrt
  fingerprint` identity confirmation, `conwrt apply` dry-run shape, and
  — with CONWRT_APPLY_EXECUTE=1 — mutating applies + a WireGuard
  client tunnel end-to-end (router pings the WG endpoint through wg0).

Environment
-----------
CONWRT_BIN
    Path to the conwrt binary. Default: build from CONWRT_REPO.
CONWRT_REPO
    conwrt checkout (worktrees fine). Default ~/src/conwrt.
CONWRT_ROUTER_HOST / CONWRT_ROUTER_KEY / CONWRT_ROUTER_PORT
    Venue target (defaults 192.168.1.1 / unset / 22). Falls back to
    TOLLGATE_SSH_HOST / TOLLGATE_SSH_KEY.
CONWRT_APPLY_EXECUTE=1
    Opt-in for the mutating apply tests (dry-run is the default).
CONWRT_WG_ENDPOINT_HOST / CONWRT_WG_ENDPOINT_PORT
    WireGuard test endpoint (default inr2.cashu.exchange :51821).
CONWRT_WG_SSH
    SSH alias for managing the endpoint peer (default inr2.cashu.exchange).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.story("O6"), pytest.mark.api]

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]

_env_file = REPO_ROOT / ".env"
if _env_file.is_file():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

CONWRT_REPO = Path(os.environ.get("CONWRT_REPO", str(Path.home() / "src" / "conwrt")))
CONWRT_BIN = os.environ.get("CONWRT_BIN", "")
ROUTER_HOST = os.environ.get("CONWRT_ROUTER_HOST", os.environ.get("TOLLGATE_SSH_HOST", "192.168.1.1"))
ROUTER_KEY = os.environ.get("CONWRT_ROUTER_KEY", os.environ.get("TOLLGATE_SSH_KEY", ""))
ROUTER_PORT = os.environ.get("CONWRT_ROUTER_PORT", "22")
APPLY_EXECUTE = os.environ.get("CONWRT_APPLY_EXECUTE", "") == "1"
WG_ENDPOINT_HOST = os.environ.get("CONWRT_WG_ENDPOINT_HOST", "inr2.cashu.exchange")
WG_ENDPOINT_PORT = os.environ.get("CONWRT_WG_ENDPOINT_PORT", "51821")
WG_SSH = os.environ.get("CONWRT_WG_SSH", "inr2.cashu.exchange")
WG_SUBNET = "10.77.77"

MODELS_DIR = CONWRT_REPO / "models"
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=5",
    "-o", "LogLevel=ERROR",
]


def run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ssh_router(command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    args = ["ssh", *SSH_OPTS, "-p", str(ROUTER_PORT)]
    if ROUTER_KEY:
        args += ["-i", os.path.expanduser(ROUTER_KEY)]
    args += [f"root@{ROUTER_HOST}", command]
    return run(args, timeout=timeout)


def ssh_wg(command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return run(["ssh", *SSH_OPTS, WG_SSH, command], timeout=timeout)


def tcp_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def conwrt() -> str:
    """Path to the conwrt binary (env override, else build from the repo)."""
    if CONWRT_BIN:
        if Path(CONWRT_BIN).is_file():
            return CONWRT_BIN
        pytest.skip(f"CONWRT_BIN={CONWRT_BIN} does not exist")
    if not (CONWRT_REPO / "Cargo.toml").is_file():
        pytest.skip(f"conwrt repo not found at {CONWRT_REPO} (set CONWRT_REPO)")
    if not shutil.which("cargo"):
        pytest.skip("cargo not on PATH and CONWRT_BIN unset")
    r = run(["cargo", "build", "--release", "-p", "conwrt-cli"], timeout=900)
    binpath = CONWRT_REPO / "target" / "release" / "conwrt"
    if not binpath.is_file():
        pytest.fail(f"conwrt build failed: {r.stderr[-400:]}")
    return str(binpath)


@pytest.fixture(scope="session")
def venue():
    """A reachable OpenWrt SSH venue, or skip. Returns (host, key_arg)."""
    if not tcp_open(ROUTER_HOST, int(ROUTER_PORT)):
        pytest.skip(f"venue {ROUTER_HOST}:{ROUTER_PORT} not reachable — router tests skipped")
    probe = ssh_router("cat /etc/openwrt_release 2>/dev/null | head -1; echo SSH_OK")
    if "SSH_OK" not in probe.stdout:
        pytest.skip(f"venue {ROUTER_HOST} SSH failed: {probe.stderr[-200:]}")
    return ROUTER_HOST


@pytest.fixture(scope="session")
def venue_model_id(conwrt, venue) -> str:
    """Resolve the conwrt model id matching the venue's live board_name."""
    fp = run([conwrt, "fingerprint", "--ip", ROUTER_HOST, *key_args()])
    board = None
    for line in fp.stdout.splitlines():
        if line.startswith("board_name:"):
            board = line.split(":", 1)[1].strip()
    if not board:
        pytest.skip(f"fingerprint gave no board_name: {fp.stdout[-200:]} {fp.stderr[-200:]}")
    for mf in sorted(MODELS_DIR.glob("*.json")):
        try:
            data = json.loads(mf.read_text())
        except json.JSONDecodeError:
            continue
        ow = data.get("openwrt", {})
        names = {ow.get("device"), ow.get("board_name"), *(ow.get("board_names") or [])}
        names.discard(None)
        if board in names:
            return data["id"]
    pytest.skip(f"no conwrt model matches venue board_name {board!r} (fingerprint OK)")


def key_args() -> list[str]:
    args: list[str] = []
    if ROUTER_KEY:
        args += ["--key", os.path.expanduser(ROUTER_KEY)]
    return args


@pytest.fixture(scope="module")
def venue_none_key() -> list[str]:
    """Credential args for the refused-apply test (any existing host key)."""
    hostkey = Path.home() / ".ssh" / "id_ed25519"
    if not hostkey.is_file():
        pytest.skip("no ~/.ssh/id_ed25519 for conwrt apply auth")
    return ["--key", str(hostkey)]


@pytest.fixture(scope="session")
def wg_endpoint():
    """The lab WireGuard test endpoint: server pubkey + resolved IP, or skip."""
    probe = ssh_wg("wg show wgtest public-key 2>/dev/null; echo END")
    lines = [l.strip() for l in probe.stdout.splitlines() if l.strip()]
    if probe.returncode != 0 or not lines or lines[-1] != "END" or len(lines) != 2:
        pytest.skip(f"WG test endpoint wgtest not available on {WG_SSH} (runtime interface gone?)")
    server_pub = lines[0]
    try:
        ip = socket.gethostbyname(WG_ENDPOINT_HOST)
    except socket.gaierror:
        pytest.skip(f"cannot resolve WG endpoint {WG_ENDPOINT_HOST}")
    return {"server_pub": server_pub, "ip": ip, "port": WG_ENDPOINT_PORT}


def wg_add_peer(client_pub: str, tunnel_ip: str) -> None:
    r = ssh_wg(
        f"wg set wgtest peer {client_pub} allowed-ips {tunnel_ip}/32 2>&1 && echo PEER_OK"
    )
    assert "PEER_OK" in r.stdout, f"wg set peer failed: {r.stdout} {r.stderr}"


def wg_del_peer(client_pub: str) -> None:
    ssh_wg(f"wg set wgtest peer {client_pub} remove 2>/dev/null || true")


# --------------------------------------------------------------------------
# HOST-ONLY: models + plan (mock provider, I/O-free)
# --------------------------------------------------------------------------

def test_models_corpus_validates(conwrt):
    r = run([conwrt, "models", "--dir", str(MODELS_DIR)])
    assert r.returncode == 0, r.stderr
    assert "model(s) valid" in r.stdout


def test_plan_sqm_renders_1x_shape(conwrt):
    r = run([
        conwrt, "plan", "--profile", "sqm", "--model-id", "glinet-gl-ar150",
        "--download-kbps", "50000", "--upload-kbps", "10000",
        "--scenario", "openwrt", "--dir", str(MODELS_DIR),
    ])
    assert r.returncode == 0, r.stderr
    assert "plan:     sqm" in r.stdout
    assert "sqm.wan.download=50000" in r.stdout
    assert "sqm.wan.upload=10000" in r.stdout


def test_plan_wireguard_client_renders(conwrt):
    r = run([
        conwrt, "plan", "--profile", "wireguard-client", "--model-id", "glinet-gl-ar150",
        "--endpoint-host", "vpn.example.com", "--peer-public-key", "EXITKEY",
        "--no-kill-switch", "--scenario", "openwrt", "--dir", str(MODELS_DIR),
    ])
    assert r.returncode == 0, r.stderr
    assert "network.wg0_peer.endpoint_host=vpn.example.com" in r.stdout
    assert "firewall.wg_client_vpn.name=vpn" in r.stdout
    # no kill switch: neither the rule nor the handshake guard may appear
    assert "killswitch" not in r.stdout
    assert "wg show wg0" not in r.stdout


def test_plan_cashu_vpn_client_renders(conwrt):
    """New profile from the 2026-10-24 port (conwrt PR #524)."""
    r = run([
        conwrt, "plan", "--profile", "cashu-vpn-client", "--model-id", "glinet-gl-ar150",
        "--endpoint-host", "vpn.example.com", "--peer-public-key", "EXITKEY",
        "--peer-ip", "10.66.42.2/32",
        "--scenario", "openwrt", "--dir", str(MODELS_DIR),
    ])
    assert r.returncode == 0, r.stderr
    assert "network.wg0.addresses=10.66.42.2/32" in r.stdout
    assert "network.wg0.dns=1.1.1.1" in r.stdout
    assert "firewall.cashu_vpn_zone.name=vpn" in r.stdout
    assert "firewall.cashu_vpn_killswitch.target=REJECT" in r.stdout
    # fail-open guard: handshake watch removes the kill switch on timeout
    assert "uci delete firewall.cashu_vpn_killswitch" in r.stdout


def test_plan_refuses_unconfirmed_identity(conwrt):
    r = run([
        conwrt, "plan", "--profile", "sqm", "--model-id", "glinet-gl-ar150",
        "--download-kbps", "50000", "--upload-kbps", "10000",
        "--scenario", "ssh-only", "--dir", str(MODELS_DIR),
    ])
    assert r.returncode == 1
    assert "refused" in r.stderr
    assert "does not confirm OpenWrt identity" in r.stderr


def test_plan_rejects_hostile_params(conwrt):
    r = run([
        conwrt, "plan", "--profile", "wireguard-client", "--model-id", "glinet-gl-ar150",
        "--endpoint-host", "vpn; rm -rf /", "--peer-public-key", "EXITKEY",
        "--scenario", "openwrt", "--dir", str(MODELS_DIR),
    ])
    assert r.returncode == 1
    assert "refused" in r.stderr
    assert "endpoint_host" in r.stderr

    r2 = run([
        conwrt, "plan", "--profile", "cashu-vpn-client", "--model-id", "glinet-gl-ar150",
        "--endpoint-host", "vpn.example.com", "--scenario", "openwrt",
        "--dir", str(MODELS_DIR),
    ])
    assert r2.returncode == 2
    assert "required" in r2.stderr


def test_apply_never_plans_without_fingerprint(conwrt, venue_none_key):
    """A live-device refusal: apply must not render a plan when the target
    cannot be fingerprinted (instant TCP refusal on 127.0.0.1:1)."""
    r = run([
        conwrt, "apply", "--profile", "sqm", "--model-id", "glinet-gl-ar150",
        "--download-kbps", "50000", "--upload-kbps", "10000",
        "--ip", "127.0.0.1", "--port", "1", *venue_none_key,
        "--dir", str(MODELS_DIR),
    ], timeout=60)
    assert r.returncode == 2
    assert "connect failed" in r.stderr
    assert "uci set" not in r.stdout


# --------------------------------------------------------------------------
# VENUE: fingerprint + apply dry-run (read-only against the device)
# --------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_fingerprint_confirms_openwrt(conwrt, venue):
    r = run([conwrt, "fingerprint", "--ip", ROUTER_HOST, *key_args()], timeout=90)
    assert r.returncode == 0, r.stderr
    assert "OpenWrt CONFIRMED" in r.stdout, r.stdout
    assert "board_name:" in r.stdout


@pytest.mark.timeout(180)
def test_apply_sqm_dryrun_renders_for_venue(conwrt, venue, venue_model_id):
    r = run([
        conwrt, "apply", "--profile", "sqm", "--model-id", venue_model_id,
        "--download-kbps", "50000", "--upload-kbps", "10000",
        "--ip", ROUTER_HOST, "--port", ROUTER_PORT, *key_args(),
        "--dir", str(MODELS_DIR),
    ], timeout=120)
    assert r.returncode == 0, r.stderr
    assert "dry-run: no changes made" in r.stdout
    assert "uci set sqm." in r.stdout


# --------------------------------------------------------------------------
# VENUE + WG endpoint: real tunnel e2e (opt-in via CONWRT_APPLY_EXECUTE=1)
# --------------------------------------------------------------------------

WG_SECTIONS_NETWORK = ["wg0", "wg0_peer"]
WG_SECTIONS_FIREWALL = ["wg_client_vpn", "wg_client_fwd", "wg_client_killswitch",
                        "cashu_vpn_zone", "cashu_vpn_fwd", "cashu_vpn_killswitch"]


def _venue_backup(venue) -> dict[str, str]:
    out = {}
    for cfg in ("network", "firewall"):
        r = ssh_router(f"cat /etc/config/{cfg}")
        out[cfg] = r.stdout
    return out


def _wg_teardown(client_pub: str | None) -> None:
    sections = " ".join(
        [f"network.{s}" for s in WG_SECTIONS_NETWORK]
        + [f"firewall.{s}" for s in WG_SECTIONS_FIREWALL]
    )
    ssh_router(
        f"for s in {sections}; do uci -q delete $s; done; "
        "uci -q commit network; uci -q commit firewall; "
        "/etc/init.d/network restart >/dev/null 2>&1 & "
        "echo TEARDOWN_DONE"
    )
    if client_pub:
        wg_del_peer(client_pub)


def _apply_and_tunnel(conwrt, venue, venue_model_id, wg_endpoint, profile: str,
                      tunnel_ip: str, extra_args: list[str]) -> None:
    """Shared e2e: apply the profile, register the router as a wgtest peer,
    then ping the endpoint through the tunnel from the router."""
    if not APPLY_EXECUTE:
        pytest.skip("CONWRT_APPLY_EXECUTE != 1 — mutating venue tests skipped")

    # The venue must be able to reach the WG endpoint at all.
    reach = ssh_router(f"ping -c1 -W3 {wg_endpoint['ip']} >/dev/null 2>&1 && echo REACH_OK")
    if "REACH_OK" not in reach.stdout:
        _wg_teardown(None)
        pytest.skip(f"venue {ROUTER_HOST} cannot reach {wg_endpoint['ip']} (no WAN path?)")

    r = run([
        conwrt, "apply", "--profile", profile, "--model-id", venue_model_id,
        "--endpoint-host", wg_endpoint["ip"], "--endpoint-port", wg_endpoint["port"],
        "--peer-public-key", wg_endpoint["server_pub"],
        *extra_args,
        "--no-kill-switch",
        "--ip", ROUTER_HOST, "--port", ROUTER_PORT, *key_args(),
        "--execute", "--dir", str(MODELS_DIR),
    ], timeout=300)
    assert r.returncode == 0, f"apply failed:\n{r.stdout[-800:]}\n{r.stderr[-800:]}"
    assert "applied:   script exit 0" in r.stdout
    for check in ("wg0 interface persisted", "peer endpoint persisted"):
        assert check in r.stdout, f"verification missing {check!r}: {r.stdout[-500:]}"

    # Wait for wg0 to come up (netifd brings the proto interface up async).
    pub = ""
    for _ in range(30):
        probe = ssh_router("wg show wg0 public-key 2>/dev/null")
        pub = probe.stdout.strip()
        if pub:
            break
        time.sleep(1)
    assert pub, "wg0 never came up (no public key) — proto wireguard not active"

    wg_add_peer(pub, tunnel_ip)
    try:
        # First data packet establishes the flow through the double NAT
        # (VM→host→internet→endpoint); early replies can exceed -W. A
        # single answered echo plus a real handshake proves the tunnel.
        answered = False
        last_ping = ""
        for _ in range(3):
            ping = ssh_router(f"ping -c3 -W5 {WG_SUBNET}.1 2>&1")
            last_ping = ping.stdout
            m = re.search(r"(\d+) packets? received", ping.stdout)
            if m and int(m.group(1)) >= 1:
                answered = True
                break
            time.sleep(2)
        assert answered, (
            f"tunnel ping never answered — wg show:\n{ssh_router('wg show wg0').stdout}\n{last_ping}"
        )
        handshake = ssh_router("wg show wg0 latest-handshakes")
        assert re.search(r"\d{8,}", handshake.stdout), f"no real handshake: {handshake.stdout}"
    finally:
        _wg_teardown(pub)


@pytest.mark.timeout(420)
@pytest.mark.slow
def test_wireguard_client_tunnel_e2e(conwrt, venue, venue_model_id, wg_endpoint):
    """wireguard-client: apply → wg0 up → peer on wgtest → ping through tunnel."""
    _apply_and_tunnel(
        conwrt, venue, venue_model_id, wg_endpoint,
        profile="wireguard-client",
        tunnel_ip=f"{WG_SUBNET}.2",
        extra_args=["--address", f"{WG_SUBNET}.2/32", "--allowed-ips", f"{WG_SUBNET}.0/24"],
    )


@pytest.mark.timeout(420)
@pytest.mark.slow
def test_cashu_vpn_client_tunnel_e2e(conwrt, venue, venue_model_id, wg_endpoint):
    """cashu-vpn-client (conwrt PR #524): same tunnel proof through the new profile."""
    _apply_and_tunnel(
        conwrt, venue, venue_model_id, wg_endpoint,
        profile="cashu-vpn-client",
        tunnel_ip=f"{WG_SUBNET}.3",
        extra_args=["--peer-ip", f"{WG_SUBNET}.3/32", "--allowed-ips", f"{WG_SUBNET}.0/24"],
    )


def test_venue_left_clean_after_tunnel_tests(venue):
    """Teardown verification: no conwrt WG sections linger on the venue."""
    r = ssh_router("uci -q show network.wg0 2>/dev/null; uci -q show firewall.wg_client_vpn 2>/dev/null; "
                   "uci -q show firewall.cashu_vpn_zone 2>/dev/null; echo CLEAN_CHECK_DONE")
    leftover = [l for l in r.stdout.splitlines() if "=" in l]
    assert not leftover, f"WG sections leaked on venue: {leftover}"
