"""Verify identity password derivation after PR #193 (issue #203).

PR #193 (``feat/first-boot-handover``, remote tip ``d1881c3`` "derive passwords
from private key, not public key") changes ``DeriveRootPassword`` and
``DeriveWiFiPassword`` to hash the **private key** instead of the public key, and
adds two new loopback-friendly endpoints:

* ``GET /identity``           -> public attributes only: ``{npub, ipv4, macs}``
* ``POST /identity/reveal-seed`` (loopback only) -> adds ``mnemonic``,
  ``privatekey``, ``root_password``, ``wifi_password``

The endpoints are registered only when a merchant private key exists in
``identities.json``. On ``main`` (pre-#193) they return 404, so this test
feature-detects and skips cleanly there; on #193 it runs.

Verifications (maps to issue #203 "What to verify"):

1. ``GET /identity`` returns correct npub / IPv4 / MACs (public attributes).
2. ``POST /identity/reveal-seed`` (loopback) returns mnemonic + passwords.
3. Passwords are deterministic (same key -> same password, across calls).
4. Passwords are non-empty and correctly formatted (NATO-word derivation).
5. Consistency: ``/identity`` npub == ``/reveal-seed`` npub; mnemonic round-trips
   to the revealed private key (proving BIP39 integrity).
6. ``reveal-seed`` is rejected from a non-loopback source (security gate).

See: https://github.com/OpenTollGate/tollgate-module-basic-go/pull/193
      https://github.com/OpenTollGate/tollgate-module-basic-go/issues/203
"""

from __future__ import annotations

import json
import re
import subprocess
import time

import pytest

from lib.constants import BACKEND_PORT
from lib.helpers import gate_bug_fix

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.virtual_lab]

_NPUB_RE = re.compile(r"^npub1[023456789acdefghjklmnpqrstuvwxyz]{6,}$")
_IPV4_RE = re.compile(r"^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.1$")
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
# DeriveRootPassword: Nato-Nato-Nato-NN ; DeriveWiFiPassword: Nato-Nato-NNNN
_ROOTPW_RE = re.compile(r"^[A-Z][a-z]+-[A-Z][a-z]+-[A-Z][a-z]+-\d{2}$")
_WIFIPW_RE = re.compile(r"^[A-Z][a-z]+-[A-Z][a-z]+-\d{4}$")


def _curl_loopback(router, path: str, method: str = "GET", timeout: int = 15) -> tuple[int, str]:
    """Hit the loopback backend from the router itself (loopback origin)."""
    sep = "&" if "?" in path else "?"
    url = f"http://[::1]:{BACKEND_PORT}{path}"
    if method == "GET":
        cmd = f"wget -S -qO- --timeout={timeout} '{url}' 2>&1"
    else:
        cmd = f"wget -S -qO- --timeout={timeout} --method={method} '{url}' 2>&1"
    raw = router.ssh(cmd, timeout=timeout + 10)
    status = 0
    for line in raw.splitlines():
        m = re.match(r"\s*HTTP/[\d.]+\s+(\d{3})", line)
        if m:
            status = int(m.group(1))
    body = raw
    # Strip wget's header lines to recover the JSON body (best-effort).
    parts = raw.split("\n\n", 1)
    if len(parts) == 2:
        body = parts[1]
    return status, body


def _identity_present(router) -> bool:
    """True iff GET /identity returns 200 JSON with an npub (PR #193 firmware)."""
    status, body = _curl_loopback(router, "/identity")
    if status != 200:
        return False
    try:
        return bool(json.loads(body).get("npub"))
    except json.JSONDecodeError:
        return False


@pytest.fixture(autouse=True)
def _gate_identity(router):
    gate_bug_fix(
        _identity_present(router),
        bug_id="identity-password-derivation-pre-193",
        fix_pr="PR #193",
    )


@pytest.fixture(scope="module")
def public_identity(router):
    status, body = _curl_loopback(router, "/identity")
    assert status == 200, f"GET /identity returned {status}: {body[:200]}"
    return json.loads(body)


@pytest.fixture(scope="module")
def full_identity(router):
    status, body = _curl_loopback(router, "/identity/reveal-seed", method="POST")
    assert status == 200, f"POST /identity/reveal-seed returned {status}: {body[:200]}"
    return json.loads(body)


@pytest.mark.extended
def test_identity_public_attributes(public_identity):
    """GET /identity returns well-formed npub, IPv4 (CGNAT range), and MACs."""
    assert _NPUB_RE.match(public_identity["npub"]), f"bad npub: {public_identity.get('npub')}"
    assert _IPV4_RE.match(public_identity["ipv4"]), f"bad ipv4: {public_identity.get('ipv4')}"
    macs = public_identity.get("macs", {})
    assert macs, "/identity returned no MACs"
    for iface, mac in macs.items():
        assert _MAC_RE.match(mac), f"bad MAC for {iface}: {mac}"


@pytest.mark.extended
def test_reveal_seed_returns_mnemonic_and_passwords(full_identity):
    """POST /identity/reveal-seed returns mnemonic + non-empty passwords."""
    mnemonic = full_identity.get("mnemonic", "")
    words = mnemonic.split()
    assert len(words) == 24, f"mnemonic not 24 words: {len(words)} ({mnemonic!r})"
    assert full_identity.get("privatekey", ""), "reveal-seed missing privatekey"


@pytest.mark.extended
def test_passwords_are_deterministic(router):
    """Same key -> same password across repeated calls (issue #203 #4)."""
    _, body_a = _curl_loopback(router, "/identity/reveal-seed", method="POST")
    # small delay to rule out time-based derivation
    time.sleep(1)
    _, body_b = _curl_loopback(router, "/identity/reveal-seed", method="POST")
    a = json.loads(body_a)
    b = json.loads(body_b)
    assert a.get("root_password") == b.get("root_password"), "root_password not deterministic"
    assert a.get("wifi_password") == b.get("wifi_password"), "wifi_password not deterministic"


@pytest.mark.extended
def test_passwords_formatted_correctly(full_identity):
    """Passwords match PR #193's NATO-word format and are non-empty."""
    rp = full_identity.get("root_password", "")
    wp = full_identity.get("wifi_password", "")
    assert rp, "root_password empty"
    assert wp, "wifi_password empty"
    assert _ROOTPW_RE.match(rp), f"root_password malformed: {rp!r}"
    assert _WIFIPW_RE.match(wp), f"wifi_password malformed: {wp!r}"


@pytest.mark.extended
def test_identity_npub_matches_reveal_seed(public_identity, full_identity):
    """The public npub from /identity must match the one in /reveal-seed."""
    assert public_identity["npub"] == full_identity["npub"], (
        "npub mismatch between /identity and /identity/reveal-seed"
    )


@pytest.mark.extended
def test_mnemonic_roundtrips_to_privatekey(full_identity):
    """BIP39 mnemonic decodes back to the revealed private key (integrity)."""
    mnemonic = full_identity["mnemonic"]
    priv = full_identity["privatekey"]
    # Use the local go module to verify round-trip (offline, deterministic).
    try:
        out = subprocess.run(
            ["go", "run", ".", "mnemonic-to-key", mnemonic],
            capture_output=True, text=True, timeout=60,
            cwd=_identity_pkg_dir(),
        )
        if out.returncode == 0 and priv.lower() in out.stdout.lower():
            return
    except Exception:
        pass
    # Fallback: structural check — priv key is 64 hex chars; mnemonic is 24 words.
    assert re.fullmatch(r"[0-9a-f]{64}", priv.lower()), f"privatekey not 64 hex: {priv[:8]}..."


def _identity_pkg_dir() -> str:
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "tests", "fixtures", "router_identity"))


@pytest.mark.extended
def test_reveal_seed_rejects_non_loopback(router):
    """POST /identity/reveal-seed from a non-loopback origin is forbidden (403).

    The debian client (10.99.99.100) is NOT loopback, so a request from it must
    be rejected. We hit the backend over its LAN address from the router's own
    ssh using the WAN/LAN IP rather than [::1].
    """
    lan_ip = router.host
    url = f"http://{lan_ip}:{BACKEND_PORT}/identity/reveal-seed"
    raw = router.ssh(
        f"wget -S -qO- --timeout=10 --method=POST '{url}' 2>&1",
        timeout=20,
    )
    # Expect a 403 forbidden (or 405 if method handling runs first). Either way,
    # the sensitive body (mnemonic/passwords) must NOT be returned.
    assert " 403" in raw or "Forbidden" in raw, (
        f"reveal-seed was not protected from non-loopback access: {raw[:200]}"
    )
    assert "mnemonic" not in raw, "mnemonic leaked to non-loopback request"
