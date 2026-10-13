"""Tests for netns_probe guard + guaranteed cleanup (2026-10-13).

Regression origin: cobrador's built-in NIC was left stranded in a `probe_router`
netns by a host-mode router probe, silently removing the host's ethernet.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import lib.netns_probe as npr  # noqa: E402


class FakeResult:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.returncode = 0


class FakeRunner:
    """Records every argv and serves canned outputs by predicate."""

    def __init__(self, defaults=(), addrs=None):
        self.defaults = defaults
        self.addrs = addrs or {}
        self.calls = []

    def __call__(self, argv, capture_output=True, text=True, check=False):
        self.calls.append(list(argv))
        j = " ".join(argv)
        if "route" in argv and "default" in j:
            return FakeResult("\n".join(f"default via 1.1.1.1 dev {d} proto dhcp" for d in self.defaults))
        if argv[:2] == ["ip", "netns"] and "addr" in argv and "show" in argv:
            dev = argv[-1]
            return FakeResult("\n".join(f"2: {dev} inet {a} scope global" for a in self.addrs.get(dev, [])))
        for a in argv:
            if a.startswith("dev ") or a in self.addrs:
                pass
        # addr show dev X form: ["ip","-o","addr","show","dev","X"]
        if "-o" in argv and "addr" in argv and "dev" in argv:
            dev = argv[argv.index("dev") + 1]
            return FakeResult("\n".join(f"inet {a} scope global {dev}" for a in self.addrs.get(dev, [])))
        return FakeResult("")


def test_unsafe_reason_default_route():
    r = FakeRunner(defaults=("enp0s31f6",))
    assert "default route" in npr.unsafe_reason("enp0s31f6", protected={"enp0s31f6"},
                                                managed_cidrs=(), runner=r)


def test_unsafe_reason_managed_cidr():
    r = FakeRunner(defaults=(), addrs={"enp0s31f6": ["192.168.2.43/24"]})
    reason = npr.unsafe_reason("enp0s31f6", protected=set(),
                               managed_cidrs=("192.168.2.0/24",), runner=r)
    assert reason and "managed address" in reason


def test_safe_iface_returns_none():
    r = FakeRunner(defaults=("enp0s31f6",), addrs={"enx00e04c68": []})
    assert npr.unsafe_reason("enx00e04c68", protected={"enp0s31f6"},
                             managed_cidrs=("192.168.2.0/24",), runner=r) is None


def test_enter_refuses_protected_iface():
    r = FakeRunner(defaults=("enp0s31f6",))
    p = npr.NetnsProbe("enp0s31f6", runner=r)
    try:
        p.__enter__()
        assert False, "should have refused"
    except npr.UnsafeProbeInterface:
        pass
    # never moved anything
    assert not any("netns" in " ".join(c) and "add" in c for c in r.calls)


def test_cleanup_always_returns_iface(monkeypatch):
    r = FakeRunner(defaults=("enp0s31f6",), addrs={"enx00e04c68": []})
    monkeypatch.setattr(npr.shutil, "which", lambda x: "/usr/sbin/ip")
    with npr.NetnsProbe("enx00e04c68", runner=r):
        pass
    joined = [" ".join(c) for c in r.calls]
    assert any("netns add prta-probe" in j for j in joined)
    assert any("link set enx00e04c68 netns prta-probe" in j for j in joined)
    # returned + deleted
    assert any("link set enx00e04c68 netns 1" in j for j in joined)
    assert any("netns del prta-probe" in j for j in joined)


def test_cleanup_on_exception(monkeypatch):
    r = FakeRunner(defaults=("enp0s31f6",), addrs={"enx00e04c68": []})
    monkeypatch.setattr(npr.shutil, "which", lambda x: "/usr/sbin/ip")
    try:
        with npr.NetnsProbe("enx00e04c68", runner=r):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    joined = [" ".join(c) for c in r.calls]
    assert any("link set enx00e04c68 netns 1" in j for j in joined)
    assert any("netns del prta-probe" in j for j in joined)
