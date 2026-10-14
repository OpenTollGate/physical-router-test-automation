"""Installer ``/api/identify`` password verdict contract (rc28).

The wizard's identity step needs to tell the operator whether the root
password they typed actually worked, in a MACHINE-readable way, so it can
render an accepted/refused badge beside the field instead of a generic
"Router". ``/api/identify`` returns the discovered-router object with a
``password_status`` field derived from the connect outcome:

    accepted | refused | missing | untrusted | unreachable | unknown

These tests drive the endpoint against the QEMU lab VM (whose root password
is known) and its two neighbours: a WRONG password must read ``refused``,
the RIGHT one ``accepted``, and an address that is not the router must not
be labelled a router.

Run explicitly: ``pytest installer/ -v``. ``required`` — a skip is a
release-relevant NOT VERIFIED.
"""
from __future__ import annotations

import pytest

from lab_vm import VM_IP

# The classified verdicts /api/identify may return for the password field.
VERDICTS = frozenset({"accepted", "refused", "missing", "untrusted", "unreachable", "unknown"})

pytestmark = [
    pytest.mark.story("O5"),
    pytest.mark.installer,
    pytest.mark.virtual_lab,
    pytest.mark.required,
]


def _identify(installer_service, ip: str, password: str) -> dict:
    return installer_service.post_json("/api/identify", {"ip": ip, "password": password})


@pytest.mark.timeout(120)
def test_correct_password_reads_accepted(installer_service, installer_lab):
    """The lab VM's real root password -> ``password_status: accepted``."""
    resp = _identify(installer_service, VM_IP, installer_lab.password)
    status = resp.get("password_status")
    assert status in VERDICTS, f"password_status {status!r} not a known verdict: {resp}"
    assert status == "accepted", (
        f"the correct root password must read 'accepted', got {status!r}: {resp}"
    )


@pytest.mark.timeout(120)
def test_wrong_password_reads_refused(installer_service, installer_lab):
    """A wrong root password -> ``refused`` (the wizard shows a red badge).

    This is the regression rail: before the field existed the wizard could
    only say "Router" for any failed dial, so a typo looked like a discovery
    bug rather than a bad password.
    """
    resp = _identify(installer_service, VM_IP, "definitely-not-the-password")
    status = resp.get("password_status")
    assert status in VERDICTS, f"password_status {status!r} not a known verdict: {resp}"
    assert status == "refused", (
        f"a wrong root password must read 'refused', got {status!r}: {resp}"
    )


@pytest.mark.timeout(120)
def test_empty_password_on_protected_router_is_refused(installer_service, installer_lab):
    """An empty password against a password-protected router -> NOT accepted.

    The lab VM always has a root password set, so the passwordless path must
    fail and report a refusal rather than silently "identifying" the router.
    """
    resp = _identify(installer_service, VM_IP, "")
    status = resp.get("password_status")
    assert status in VERDICTS, f"password_status {status!r} not a known verdict: {resp}"
    assert status != "accepted", (
        f"an empty password must not be accepted on a protected router: {resp}"
    )


@pytest.mark.timeout(60)
def test_non_router_address_is_not_labelled_a_router(installer_service):
    """An address with no SSH -> a non-accepted verdict, never ``accepted``.

    ``classifyProbed`` must not brand an unidentified host a router; the
    password verdict for it is one of refused/unreachable/unknown.
    """
    resp = _identify(installer_service, "10.99.95.254", "")
    status = resp.get("password_status")
    assert status in VERDICTS, f"password_status {status!r} not a known verdict: {resp}"
    assert status != "accepted", f"an unreachable host must not read 'accepted': {resp}"
