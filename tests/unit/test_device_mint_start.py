"""Device-mint startup contract (lib/router.py, #297).

Pins, with a scripted Router double and no hardware:
  * kill BEFORE copy — scp'ing over a running binary hits ETXTBSY and
    aborted the restart;
  * the one-time init guard recognizes CDK 0.18's cdk-mintd.sqlite (the
    legacy cdk-mintd.db check re-ran init and silently ignored new
    settings/mnemonics);
  * readiness is a 1-sat quote settling to PAID, not /v1/info answering
    200 (a wedged FakeWallet passes the old gate and hangs every payment
    test);
  * device_mint_url is CALLER-visible: env override, else the router's
    address — never the advertised 127.0.0.1 that resolved to the
    caller's own loopback.
"""
import pytest

import lib.router
from lib.router import Router

pytestmark = [pytest.mark.api]


class FakeRouter:
    """Records ssh/scp calls; serves scripted responses by substring."""

    def __init__(self, responses):
        self.host = "10.20.30.40"
        self.calls = []
        self.responses = list(responses)

    def ssh(self, cmd, timeout=30):
        self.calls.append(("ssh", cmd))
        for i, (needle, out) in enumerate(self.responses):
            if needle in cmd:
                self.responses.pop(i)
                return out
        return ""

    def scp_to(self, local, remote, timeout=120):
        self.calls.append(("scp", local, remote))
        return ""

    def ssh_cmds(self):
        return [call[1] for call in self.calls if call[0] == "ssh"]


def _router(responses):
    r = FakeRouter(responses)
    real = object.__new__(Router)
    real.host = r.host
    real.ssh = r.ssh
    real.scp_to = r.scp_to
    real._fake = r
    return real


HAPPY = [
    ("/v1/info", "200"),
    ("/v1/mint/quote/bolt11", '{"quote":"qid-1","state":"UNPAID"}'),
    ('/v1/mint/quote/bolt11/qid-1', '{"quote":"qid-1","state":"PAID"}'),
]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr("lib.router.time.sleep", lambda *_: None)
    monkeypatch.setenv("TOLLGATE_DEVICE_MINT_MNEMONIC", "test mnemonic words")


class TestStartDeviceMint:
    def test_happy_path_returns_true_and_probes_quote_to_paid(self):
        r = _router(HAPPY)
        assert r.start_device_mint("/lab/cdk-mintd", "/lab/mint.toml") is True
        cmds = r._fake.ssh_cmds()
        # the quote->PAID probe actually ran (POST + state GET)
        assert any("POST" in c and "quote/bolt11" in c and '"amount":1' in c for c in cmds)
        assert any("quote/bolt11/qid-1" in c for c in cmds)

    def test_kill_before_copy(self):
        r = _router(HAPPY)
        r.start_device_mint("/lab/cdk-mintd", "/lab/mint.toml")
        calls = r._fake.calls
        kill_idx = next(
            i for i, call in enumerate(calls)
            if call[0] == "ssh" and "killall cdk-mintd" in call[1]
        )
        scp_idx = next(
            i for i, call in enumerate(calls)
            if call[0] == "scp" and call[2] == "/tmp/cdk-mintd"
        )
        assert kill_idx < scp_idx, "the binary must be killed BEFORE it is copied (ETXTBSY)"

    def test_init_guard_checks_both_db_filenames(self):
        r = _router(HAPPY)
        r.start_device_mint("/lab/cdk-mintd", "/lab/mint.toml")
        init = [c for c in r._fake.ssh_cmds() if "config init" in c]
        assert init, "init command present"
        assert "cdk-mintd.db" in init[0] and "cdk-mintd.sqlite" in init[0]

    def test_wedged_fakewallet_fails_readiness(self):
        # /v1/info answers 200 but the quote never settles: the OLD gate
        # returned True here — the exact hazard the probe exists for.
        r = _router([
            ("/v1/info", "200"),
            ("/v1/mint/quote/bolt11", '{"quote":"qid-1","state":"UNPAID"}'),
        ])
        assert r.start_device_mint("/lab/cdk-mintd", "/lab/mint.toml") is False

    def test_no_mnemonic_short_circuits(self, monkeypatch):
        monkeypatch.delenv("TOLLGATE_DEVICE_MINT_MNEMONIC", raising=False)
        r = _router(HAPPY)
        assert r.start_device_mint("/lab/cdk-mintd", "/lab/mint.toml") is False
        assert r._fake.calls == []

    def test_info_never_answering_fails(self):
        r = _router([])
        assert r.start_device_mint("/lab/cdk-mintd", "/lab/mint.toml") is False


class TestDeviceMintUrl:
    def test_default_is_the_router_address_not_loopback(self, monkeypatch):
        monkeypatch.delenv("TOLLGATE_DEVICE_MINT_URL", raising=False)
        r = _router([])
        assert r.device_mint_url == "http://10.20.30.40:8487"

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("TOLLGATE_DEVICE_MINT_URL", "http://127.0.0.1:18487")
        r = _router([])
        assert r.device_mint_url == "http://127.0.0.1:18487"

    def test_loopback_constant_survives_for_on_box_probes(self):
        assert Router.DEVICE_MINT_LOOPBACK_URL == "http://127.0.0.1:8487"
