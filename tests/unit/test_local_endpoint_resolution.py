"""Locality must be decided by address, not by spelling.

``is_local_endpoint`` guards one of the most consequential refusals in this
module: it decides whether reading ``/proc`` answers for THIS machine. Getting it
wrong in the permissive direction would attribute one host's memory and CPU to
another authority's state; getting it wrong in the restrictive direction (the bug
here) reports the local host as "not this host" and refuses to measure it at all.

The measured failure: ``x280.fips`` IS this machine, but it resolves **IPv6-only**
(``fd36:3c0f:...``) while ``gethostname()`` resolves to ``127.0.1.1``. A string
comparison misses it, and an IPv4-only ``gethostbyname`` comparison misses it too
— ``gethostbyname('x280.fips')`` raises ``No address associated with hostname``.

So the rule encoded here: compare the full address sets from ``getaddrinfo``,
over both families. A name is local when its address set intersects this host's.
Nothing else may make a remote host look local.
"""

from __future__ import annotations

import socket

import pytest

from lib.fleet_observe import is_local_endpoint


def _addrs(name: str) -> set[str]:
    """Every address a name resolves to, any family. Empty on failure."""
    try:
        infos = socket.getaddrinfo(name, None)
    except OSError:
        return set()
    return {str(info[4][0]) for info in infos}


def test_the_loopback_names_are_local():
    assert is_local_endpoint("127.0.0.1:8099")
    assert is_local_endpoint("localhost:8099")


def test_this_hosts_own_names_are_local():
    assert is_local_endpoint(f"{socket.gethostname()}:8099")


def test_an_ipv6_only_alias_of_this_host_is_local():
    """The measured bug: an alias with no IPv4 record must still be recognised.

    If this host has no such alias the test is not meaningful, so it is skipped
    rather than faked — but it must never be silently *passed*.
    """
    candidate = "x280.fips"
    if not (_addrs(candidate) & _addrs(socket.gethostname())):
        pytest.skip(f"{candidate} does not alias this host in this environment")
    assert is_local_endpoint(f"{candidate}:8099"), (
        "an alias resolving to this host's own addresses must be local"
    )


def test_a_genuinely_remote_host_is_not_local():
    for remote in ("example.com:8099", "dq05:8099", "192.0.2.1:8099"):
        assert not is_local_endpoint(remote), remote


def test_an_unresolvable_name_is_refused_rather_than_assumed_local():
    """Fail closed: not resolving is not evidence of locality."""
    assert not is_local_endpoint("definitely-not-a-host.invalid:8099")


def test_an_empty_endpoint_is_not_local():
    assert not is_local_endpoint("")
    assert not is_local_endpoint(":8099")


def test_candidate_resolution_failure_is_refused_rather_than_assumed_local(monkeypatch):
    """Fail closed: if the candidate cannot be resolved, do not claim locality.

    Locality is decided by intersecting the candidate's addresses with this
    machine's assigned addresses. If the candidate yields no addresses there is
    no intersection to find, and guessing in the permissive direction would
    attribute this host's /proc to another authority.
    """
    real = socket.getaddrinfo

    def fake(name, *args, **kwargs):
        if name == "x280.fips":
            raise socket.gaierror("boom")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert not is_local_endpoint("x280.fips:8099")


def test_interface_enumeration_failure_does_not_grant_locality(monkeypatch):
    """If we cannot enumerate our own addresses, nothing can be proven local."""
    import lib.fleet_observe as fo

    monkeypatch.setattr(fo, "_local_interface_addresses", lambda: set())
    assert not is_local_endpoint("x280.fips:8099")
    # ...but an explicit local name is still local without enumeration.
    assert is_local_endpoint("127.0.0.1:8099")
