"""Unit tests for scripts/bench/vantage-preflight.sh — the guest-vantage gate.

Offline by construction: the script's only router dependency is `sshpass ssh`,
so the tests put a stub on PATH and drive every branch. No router, no lock, no
network. This is the pattern the kit already uses for its shell harnesses.

Why these tests exist (measured 2026-10-21): a portal review recorded a redirect
chain from a host whose own wlan MAC was `state=Authenticated` — a bypassed
client — and the resulting claim had to be retracted. The script makes that
mistake impossible to make silently; these tests make sure the script itself
cannot silently pass.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "bench" / "vantage-preflight.sh"

TEST_MAC = "50:76:af:2f:33:ed"

# A minimal, honest client list in ndsctl's real shape.
CLIENTS_TMPL = """{n} clients
client_id=0
ip=192.168.1.158
mac={mac}
added=0
active=1792599562
duration=0
token=94b5933f
state={state}
downloaded=91
"""


def _make_stub_bin(tmp_path: Path, *, trustedmac: str = "", clients: str | None = None,
                   nodogsplash_present: bool = True) -> Path:
    """A PATH dir whose `sshpass` answers our two read commands.

    The script calls: `sshpass ... ssh ... <cmd>` where cmd is one of
      - `uci show nodogsplash ... | grep -i trustedmac`
      - `test -f /etc/config/nodogsplash`
      - `ndsctl clients`
    We match on the command text and emit the canned answer. A stub that answers
    nothing is NOT used: an unreadable router must be distinguishable from a
    clean one, and the tests cover that case explicitly.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)

    def q(s: str) -> str:
        return s.replace("'", "'\\''")

    # Build the ndsctl answer WITHOUT an f-string: a backslash inside an f-string
    # expression is illegal before Python 3.12 (PEP 701), and this repo supports
    # 3.11 locally even though CI pins 3.12.
    if clients:
        clients_block = 'cat <<"STUBEOF"\n' + clients + "\nSTUBEOF"
    else:
        clients_block = "exit 0"

    stub = f"""#!/bin/bash
# Concatenate all argv so the remote command text is greppable regardless of
# how it was split. Using "$*" avoids POSIX-sh array-slice portability traps
# ("${{@: -1}}" is not valid in dash).
cmd="$*"
case "$cmd" in
  *trustedmac*)
    printf '%s\\n' '{q(trustedmac)}'
    ;;
  *"/etc/config/nodogsplash"*)
    {'exit 0' if nodogsplash_present else 'exit 1'}
    ;;
  *"ndsctl clients"*)
    {clients_block}
    ;;
  *)
    exit 0
    ;;
esac
"""
    p = bin_dir / "sshpass"
    p.write_text(stub)
    p.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path, mac: str = TEST_MAC, *, json_mode: bool = False,
         trustedmac: str = "", clients: str | None = None,
         nodogsplash_present: bool = True, no_wlan: bool = False):
    bin_dir = _make_stub_bin(
        tmp_path, trustedmac=trustedmac, clients=clients,
        nodogsplash_present=nodogsplash_present,
    )
    args = ["bash", str(SCRIPT)]
    if json_mode:
        args.append("--json")
    args.append(mac)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["ROUTER_IP"] = "192.168.1.1"
    env["ROUTER_PASS"] = "testpass"
    if no_wlan:
        env["VANTAGE_NO_WLAN"] = "1"
    return subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)


@pytest.fixture(autouse=True)
def _require_script() -> None:
    if not SCRIPT.is_file():
        pytest.skip(f"missing {SCRIPT}")


# --- the happy path -----------------------------------------------------------

def test_preauthenticated_is_ok(tmp_path: Path) -> None:
    r = _run(tmp_path, clients=CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Preauthenticated"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VANTAGE OK" in r.stdout


def test_never_seen_mac_is_ok_for_first_contact(tmp_path: Path) -> None:
    other = CLIENTS_TMPL.format(n=1, mac="aa:bb:cc:dd:ee:ff", state="Preauthenticated")
    r = _run(tmp_path, clients=other)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not yet seen" in r.stdout


# --- the failure modes this gate exists for -----------------------------------

def test_authenticated_is_rejected(tmp_path: Path) -> None:
    """The exact mistake that invalidated a real review."""
    r = _run(tmp_path, clients=CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Authenticated"))
    assert r.returncode == 10, r.stdout + r.stderr
    assert "BYPASSED" in r.stdout


def test_trusted_state_is_rejected(tmp_path: Path) -> None:
    r = _run(tmp_path, clients=CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Trusted"))
    assert r.returncode == 11, r.stdout + r.stderr


def test_trustedmac_exemption_is_rejected(tmp_path: Path) -> None:
    r = _run(
        tmp_path,
        trustedmac=f"nodogsplash.@nodogsplash[0].trustedmac='{TEST_MAC}'",
        clients=CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Preauthenticated"),
    )
    assert r.returncode == 13, r.stdout + r.stderr
    assert "exempt by design" in r.stdout


# --- fail closed: an unreadable box is NOT a pass -----------------------------

def test_unreadable_client_list_fails_closed(tmp_path: Path) -> None:
    r = _run(tmp_path, clients=None)
    assert r.returncode == 12, r.stdout + r.stderr
    assert "NOT a pass" in r.stderr or "NOT a pass" in r.stdout


def test_unrecognised_state_fails_closed(tmp_path: Path) -> None:
    r = _run(tmp_path, clients=CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="WeirdState"))
    assert r.returncode == 12, r.stdout + r.stderr


# --- json mode is machine-checkable -------------------------------------------

def test_json_ok_shape(tmp_path: Path) -> None:
    r = _run(tmp_path, clients=CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Preauthenticated"),
             json_mode=True)
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip())
    assert payload["verdict"] == "ok"
    assert payload["state"] == "Preauthenticated"


def test_json_bad_shape(tmp_path: Path) -> None:
    r = _run(tmp_path, clients=CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Authenticated"),
             json_mode=True)
    assert r.returncode == 10
    payload = json.loads(r.stdout.strip())
    assert payload["verdict"] == "bad"
    assert payload["reason"] == "authenticated"


def test_json_is_parseable_on_every_exit_path(tmp_path: Path) -> None:
    """A caller scripting on this must never get an unparseable line."""
    cases = [
        (CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Preauthenticated"), "", 0),
        (CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Authenticated"), "", 10),
        (CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Trusted"), "", 11),
        (CLIENTS_TMPL.format(n=1, mac=TEST_MAC, state="Preauthenticated"),
         f"nodogsplash.@nodogsplash[0].trustedmac='{TEST_MAC}'", 13),
        (None, "", 12),
    ]
    for clients, trusted, expect_rc in cases:
        d = tmp_path / f"c{expect_rc}"
        d.mkdir()
        r = _run(d, clients=clients, trustedmac=trusted, json_mode=True)
        assert r.returncode == expect_rc, f"rc={r.returncode} want {expect_rc}\n{r.stdout}{r.stderr}"
        payload = json.loads(r.stdout.strip())  # must not raise
        assert payload["mac"] == TEST_MAC
        assert payload["verdict"] in {"ok", "bad", "unknown"}
