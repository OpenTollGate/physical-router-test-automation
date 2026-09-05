"""Unit tests for Router.fix_nodogsplash_auth_marks (NDS 5.0.2 mark bug).

See AGENTS.md "Nodogsplash 5.0.2 auth-mark bug": ndsctl auth inserts a
0x30000-marked rule in mangle ndsOUT, but filter ndsNET accepts only
0x20000/0x30000 — authenticated clients cannot open new connections.
The helper rewrites each 0x30000 rule to 0x20000.
"""

import pytest

from lib.router import Router

BUGGY = (
    "-A ndsOUT -s 10.99.99.186/32 -m mac --mac-source aa:bb:cc:dd:ee:ff "
    "-j MARK --or-mark 0x30000"
)
BUGGY2 = (
    "-A ndsOUT -s 10.99.99.42/32 -m mac --mac-source 11:22:33:44:55:66 "
    "-j MARK --or-mark 0x30000"
)
CHAIN_DEF = "-P ndsOUT ACCEPT"


def make_router(ssh_output, calls):
    router = Router("10.99.99.1", "10.99.99.186", "aa:bb:cc:dd:ee:ff", "test.lan")

    def fake_ssh(cmd, timeout=None):
        calls.append(cmd)
        return ssh_output

    router.ssh = fake_ssh
    return router


def test_rewrites_buggy_rule_to_0x20000_at_position_1():
    calls = []
    router = make_router(f"{CHAIN_DEF}\n{BUGGY}", calls)

    router.fix_nodogsplash_auth_marks()
    assert len(calls) == 2
    delete_cmd, insert_cmd = calls[1].split("&&")
    assert "iptables -t mangle -D ndsOUT" in delete_cmd
    assert "-D ndsOUT -s 10.99.99.186/32" in delete_cmd
    assert "--or-mark 0x30000" in delete_cmd
    assert "iptables -t mangle -I ndsOUT 1" in insert_cmd
    assert "-I ndsOUT 1" in insert_cmd
    assert "--or-mark 0x20000" in insert_cmd
    assert "0x30000" not in insert_cmd


def test_rewrites_set_xmark_form():
    calls = []
    buggy = BUGGY.replace("--or-mark 0x30000", "--set-xmark 0x30000/0x30000")
    router = make_router(f"{CHAIN_DEF}\n{buggy}", calls)

    router.fix_nodogsplash_auth_marks()

    assert len(calls) == 2
    delete_cmd, insert_cmd = calls[1].split("&&")
    assert "iptables -t mangle -D ndsOUT" in delete_cmd
    assert "--set-xmark 0x30000/0x30000" in delete_cmd
    assert "iptables -t mangle -I ndsOUT 1" in insert_cmd
    assert "--set-xmark 0x20000/0x30000" in insert_cmd


def test_skips_unknown_mark_form():
    calls = []
    weird = BUGGY.replace("--or-mark 0x30000", "--mark 0x30000")
    router = make_router(f"{CHAIN_DEF}\n{weird}", calls)

    router.fix_nodogsplash_auth_marks()

    assert len(calls) == 1


def test_noop_when_no_buggy_rules():
    calls = []
    router = make_router(f"{CHAIN_DEF}\n" + BUGGY.replace("0x30000", "0x20000"), calls)

    router.fix_nodogsplash_auth_marks()

    assert calls == ["iptables -t mangle -S ndsOUT 2>/dev/null"]


def test_filters_by_ip_when_given():
    calls = []
    router = make_router(f"{CHAIN_DEF}\n{BUGGY}\n{BUGGY2}", calls)

    router.fix_nodogsplash_auth_marks(ip="10.99.99.42", mac="11:22:33:44:55:66")

    assert len(calls) == 2
    assert "-D ndsOUT -s 10.99.99.42/32" in calls[1]
    assert "10.99.99.186" not in calls[1]


def test_ssh_failure_is_swallowed():
    calls = []
    router = Router("10.99.99.1", "10.99.99.186", "aa:bb:cc:dd:ee:ff", "test.lan")

    def failing_ssh(cmd, timeout=None):
        calls.append(cmd)
        raise RuntimeError("connection lost")

    router.ssh = failing_ssh

    router.fix_nodogsplash_auth_marks()

    assert len(calls) == 1


if __name__ == "__main__":
    pytest.main([__file__])


def test_wait_for_auth_repairs_marks_once_authed(monkeypatch):
    router = Router("10.99.99.1", "10.99.99.186", "aa:bb:cc:dd:ee:ff", "test.lan")
    monkeypatch.setattr(router, "get_nds_state", lambda mac=None: "Authenticated")
    repaired = []
    monkeypatch.setattr(router, "fix_nodogsplash_auth_marks", lambda: repaired.append(True))
    assert router.wait_for_auth(timeout=1) is True
    assert repaired == [True]


def test_wait_for_auth_no_repair_when_not_authed(monkeypatch):
    router = Router("10.99.99.1", "10.99.99.186", "aa:bb:cc:dd:ee:ff", "test.lan")
    monkeypatch.setattr(router, "get_nds_state", lambda mac=None: "Preauthenticated")
    repaired = []
    monkeypatch.setattr(router, "fix_nodogsplash_auth_marks", lambda: repaired.append(True))
    assert router.wait_for_auth(timeout=1) is False
    assert repaired == []
