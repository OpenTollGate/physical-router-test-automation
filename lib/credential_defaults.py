"""Credential facts for the rc4 fixed-default design.

The router packages install a **FIXED** default credential (``TollGate``) for the
admin board AND the private (management) WiFi when the operator supplies none,
mark each **PROVISIONAL**, and let the admin/config UI force a change at first
login. A fixed default — never a random one — is the point: a value the operator
cannot know is the lockout the design exists to prevent.

These helpers read the facts the live credential scenario asserts on — the root
shadow hash (and whether it is **exactly** ``TollGate``), the provisional
markers, and the private-WiFi key — plus a fresh password-login probe so a test
can prove the router *accepts* the default and *rejects* a wrong one.
"""

from __future__ import annotations

import re
import shlex
import subprocess

#: The fixed default the package installs (module TOLLGATE_DEFAULT_CREDENTIAL).
DEFAULT_CREDENTIAL = "TollGate"

#: The install-time markers (module's 99-tollgate-setup).
ADMIN_MARKER = "/etc/tollgate/admin-credential-provisional"
PRIVATE_WIFI_MARKER = "/etc/tollgate/private-wifi-credential-provisional"

_ROOT_HASH_CMD = "awk -F: '$1==\"root\"{print $2; exit}' /etc/shadow 2>/dev/null"


def root_hash(router) -> str:
    """The root account's /etc/shadow hash field ('' when unset)."""
    return router.ssh(_ROOT_HASH_CMD, timeout=10).strip()


def root_hash_state(router) -> str:
    """One of ``set`` | ``empty`` | ``locked`` | ``unknown`` (the gate's states)."""
    h = root_hash(router)
    if h == "":
        return "empty"
    if h.startswith("!") or h.startswith("*"):
        return "locked"
    if h.startswith("$"):
        return "set"
    return "unknown"


def marker_present(router, path: str) -> bool:
    out = router.ssh(
        f"test -f {shlex.quote(path)} && echo yes || echo no", timeout=10
    ).strip()
    return out == "yes"


def private_wifi_key(router) -> str:
    return router.ssh(
        "uci -q get wireless.private_radio0.key 2>/dev/null", timeout=10
    ).strip()


def private_wifi_enabled(router) -> bool:
    out = router.ssh(
        "uci -q get wireless.private_radio0.disabled 2>/dev/null", timeout=10
    ).strip()
    return out == "0"


def _salt(hash_field: str) -> str | None:
    # $5$<salt>$<body> (sha256crypt) or $6$<salt>$<body> (sha512crypt).
    m = re.match(r"^\$[56]\$([^$]+)\$", hash_field or "")
    return m.group(1) if m else None


def hash_matches(password: str, hash_field: str) -> bool:
    """True when ``password`` crypts to ``hash_field``.

    Uses the HOST's ``openssl passwd`` ($5$/$6$), so it needs no crypt tool on the
    router — the router just hands over its own hash field. This is how the test
    proves the installed default is *exactly* ``TollGate`` and not merely "some
    non-empty hash".
    """
    salt = _salt(hash_field)
    if salt is None:
        return False
    algo = "-5" if hash_field.startswith("$5$") else "-6"
    out = subprocess.run(
        ["openssl", "passwd", algo, "-salt", salt, password],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    return out == hash_field


def login_works(host: str, password: str, *, user: str = "root", timeout: int = 15) -> bool:
    """A FRESH password SSH (no ControlMaster) — proves the router accepts the
    password itself, not a session cached before the credential changed."""
    r = subprocess.run(
        [
            "sshpass", "-p", password, "ssh",
            "-o", "ControlMaster=no",
            "-o", "ControlPath=none",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=8",
            "-o", "PreferredAuthentications=password",
            "-o", "PubkeyAuthentication=no",
            "-o", "NumberOfPasswordPrompts=1",
            f"{user}@{host}",
            "true",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode == 0
