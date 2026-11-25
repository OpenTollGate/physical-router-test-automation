"""Scoped mint-REJECT sweep contract (lib/helpers.py, #298).

Pins the canonical cleanup helper the degraded-mode tests route through:
  * specs use the URL's REAL port (the block_mints insert spec) — the old
    hardcoded --dport 443 missed plain-http/custom-port mints and left
    duplicate rules / a degraded router;
  * loop-deletion (duplicates on retry), never a blind line-number sweep
    that would eat an operator's unrelated REJECT policy;
  * an empty map sweeps NOTHING and warns — blind deletion is the hazard.
"""
import logging

import pytest

from lib.helpers import sweep_mint_rejects

pytestmark = [pytest.mark.api]


class FakeRouter:
    def __init__(self):
        self.ssh_calls = []

    def ssh(self, cmd, timeout=30):
        self.ssh_calls.append(cmd)
        return ""


MAP = {
    "https://mint.example": "203.0.113.9",      # default port 443
    "http://10.99.99.2:8383": "10.99.99.2",     # explicit custom port
    "http://mint.local": "198.51.100.7",        # http default 80
}


def test_specs_use_each_urls_real_port():
    r = FakeRouter()
    sweep_mint_rejects(r, MAP)
    joined = "\n".join(r.ssh_calls)
    assert "-d 203.0.113.9 -p tcp --dport 443 -j REJECT" in joined
    assert "-d 10.99.99.2 -p tcp --dport 8383 -j REJECT" in joined
    assert "-d 198.51.100.7 -p tcp --dport 80 -j REJECT" in joined


def test_loop_deletes_duplicates_not_line_numbers():
    r = FakeRouter()
    sweep_mint_rejects(r, MAP)
    for cmd in r.ssh_calls:
        assert "while iptables -C OUTPUT" in cmd, "spec loop-deletion, not -D by line number"
        assert "--line-numbers" not in cmd


def test_empty_map_sweeps_nothing_and_warns(caplog):
    r = FakeRouter()
    with caplog.at_level(logging.WARNING):
        sweep_mint_rejects(r, {})
    assert r.ssh_calls == []
    assert "no mint map" in caplog.text


def test_single_rule_covers_all_mints_in_map():
    r = FakeRouter()
    sweep_mint_rejects(r, MAP)
    assert len(r.ssh_calls) == len(MAP)
