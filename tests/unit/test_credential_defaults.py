"""Hermetic controls for lib/credential_defaults.py — no router, no network.

The live scenario (``tests/scenarios/test_feed_package.py``) proves the rc4
fixed-default credential on the bench. These tests prove the HELPER it relies on
cannot pass vacuously: ``hash_matches`` accepts exactly the right password and
rejects EVERYTHING for an empty/unset hash, so the scenario's "the hash is
exactly ``TollGate``" assertion cannot be satisfied by a credential-less router.
"""

import shutil
import subprocess

import pytest

from lib import credential_defaults as cred

pytestmark = [
    pytest.mark.story("O5"),
    pytest.mark.skipif(
        shutil.which("openssl") is None, reason="openssl not available on this host"
    ),
]


class _StubRouter:
    """A Router stand-in whose ``ssh`` returns canned stdout by substring match."""

    def __init__(self, responses):
        self._r = responses

    def ssh(self, cmd, timeout=30):
        for needle, value in self._r.items():
            if needle in cmd:
                return value
        return ""


def _sha256crypt(password: str, salt: str) -> str:
    return subprocess.run(
        ["openssl", "passwd", "-5", "-salt", salt, password],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


SALT = "abcdefghijklmnop"
HASH = _sha256crypt(cred.DEFAULT_CREDENTIAL, SALT)


def test_hash_matches_accepts_exactly_the_default():
    assert cred.hash_matches(cred.DEFAULT_CREDENTIAL, HASH)


def test_hash_matches_rejects_a_wrong_password():
    assert not cred.hash_matches("definitely-not-the-password", HASH)


def test_hash_matches_honours_the_hash_algorithm_prefix():
    # $6$ (sha512crypt) is handled too — the comparison picks the algorithm from
    # the router's own hash field.
    h6 = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", SALT, cred.DEFAULT_CREDENTIAL],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    assert h6.startswith("$6$")
    assert cred.hash_matches(cred.DEFAULT_CREDENTIAL, h6)
    assert not cred.hash_matches("not-the-default", h6)


def test_hash_matches_is_not_vacuous_on_an_empty_hash():
    # The control the whole card rests on: on a credential-less router (empty /
    # unset hash) the "exactly TollGate" check must be FALSE, or the live scenario
    # would pass on a router that installed no credential at all.
    assert not cred.hash_matches(cred.DEFAULT_CREDENTIAL, "")
    assert not cred.hash_matches("", "")


def test_root_hash_state_reads_the_gate_states():
    cases = {
        "": "empty",
        "!": "locked",
        "*": "locked",
        "$5$salt$body": "set",
        "$6$salt$body": "set",
        "garbage": "unknown",
    }
    for field, want in cases.items():
        r = _StubRouter({"/etc/shadow": field})
        assert cred.root_hash_state(r) == want, (field, want)


def test_root_hash_reads_the_root_field():
    r = _StubRouter({"/etc/shadow": "$5$salt$body\n"})
    assert cred.root_hash(r) == "$5$salt$body"
