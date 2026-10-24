"""Unit tests for lib/cashu.py — the mock-mode token fabricator and quote-output parser.

These run without a mint and without a router: MockCashuMinter is what
TOLLGATE_MOCK=1 payment-tier api tests spend, so a silent shape drift in its
tokens (keyset id form, proof fields, secret freshness, wrong-mint fixture)
turns real regressions into false greens mock-side. _parse_invoices is the
bridge between the cashu CLI's human-oriented invoice listing and the quote
claim loop, so its parsing contract is pinned here too.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re

import pytest

from lib.cashu import CashuMint, MockCashuMinter

MINT_A = "https://mint-a.example.com"
MINT_B = "https://mint-b.example.com"


def _decode_v3(token: str) -> dict:
    assert token.startswith("cashuA"), "V3 tokens must carry the cashuA prefix"
    return json.loads(base64.b64decode(token[len("cashuA"):]))


# --------------------------------------------------------------------------- #
# Keyset derivation
# --------------------------------------------------------------------------- #


def test_keyset_id_is_deterministic_v1_shaped() -> None:
    m = MockCashuMinter(MINT_A)
    assert re.fullmatch(r"00[0-9a-f]{14}", m.keyset_id)
    assert m.keyset_id == "00" + hashlib.sha256(MINT_A.encode()).hexdigest()[:14]
    assert m.keyset_id == MockCashuMinter(MINT_A).keyset_id


def test_keyset_id_differs_per_mint() -> None:
    assert MockCashuMinter(MINT_A).keyset_id != MockCashuMinter(MINT_B).keyset_id


def test_keyset_ids_publishes_exactly_the_deterministic_keyset() -> None:
    m = MockCashuMinter(MINT_A)
    assert m.keyset_ids() == {m.keyset_id}


def test_keyset_keys_cover_power_of_two_amounts_with_02_prefixed_hex() -> None:
    keys = MockCashuMinter(MINT_A).keyset_keys()
    assert set(keys) == {str(1 << i) for i in range(11)}  # 1 .. 1024
    for amount, pubkey in keys.items():
        assert re.fullmatch(r"02[0-9a-f]{64}", pubkey), amount
        assert pubkey == "02" + hashlib.sha256(f"{MINT_A}:{amount}".encode()).hexdigest()


# --------------------------------------------------------------------------- #
# Token fabrication
# --------------------------------------------------------------------------- #


def test_fabricated_token_carries_mint_keyset_and_amount() -> None:
    m = MockCashuMinter(MINT_A)
    payload = _decode_v3(m.mint(amount=7))
    assert payload["unit"] == "sat"
    assert payload["memo"] is None
    (entry,) = payload["token"]
    assert entry["mint"] == MINT_A
    (proof,) = entry["proofs"]
    assert proof["amount"] == 7
    assert proof["id"] == m.keyset_id
    assert re.fullmatch(r"[0-9a-f]{32}", proof["secret"])
    assert re.fullmatch(r"02[0-9a-f]{64}", proof["C"])


def test_fabricated_proofs_are_never_reused() -> None:
    # The mock backend's double-spend guard keys on the secret: two mints of
    # the same amount must never share proof material.
    m = MockCashuMinter(MINT_A)
    p1 = _decode_v3(m.mint(amount=4))["token"][0]["proofs"][0]
    p2 = _decode_v3(m.mint(amount=4))["token"][0]["proofs"][0]
    assert p1["secret"] != p2["secret"]
    assert p1["C"] != p2["C"]


def test_wrong_mint_token_is_pinned_obviously_invalid() -> None:
    token = MockCashuMinter(MINT_A).mint_from_wrong_mint()
    static = MockCashuMinter.synthetic_wrong_mint_token()
    assert token == static
    payload = json.loads(base64.b64decode(token[len("cashuA"):]))
    (entry,) = payload
    assert entry["mint"] == "https://wrong-mint.example.com"
    (proof,) = entry["proofs"]
    assert proof == {"amount": 4, "secret": "fake", "C": "fake"}


def test_availability_surface_is_always_ready() -> None:
    m = MockCashuMinter(MINT_A)
    assert m.is_available() is True
    assert m.ensure_mint_available(timeout=1) is None
    assert m.warmup(timeout=1) is None


# --------------------------------------------------------------------------- #
# Invoice output parsing
# --------------------------------------------------------------------------- #


def test_parse_invoices_splits_on_comment_rules_and_lowercases_keys() -> None:
    # Quirk the claim loop tolerates and this test pins: ANY line containing a
    # colon becomes an entry, so header-ish lines ("Mint quote 1:") yield
    # entries with an empty value. _find_latest_quote_id reads
    # entries[-1].get("id", "") — only the LAST entry's id matters, so stray
    # early entries are harmless; a trailing header-only section means the
    # claim loop sees "" and retries.
    out = "\n".join([
        "Mint quote 1:",
        "#",
        "Id: q-123",
        "Request: lnbc100n:extra:colons",
        "#",
        "Id: q-456",
        "Request: lnbc200n",
    ])
    entries = CashuMint._parse_invoices(out)
    assert entries == [
        {"mint quote 1": ""},
        {"id": "q-123", "request": "lnbc100n:extra:colons"},
        {"id": "q-456", "request": "lnbc200n"},
    ]
    # The consumer contract: the LAST entry's "id" is what gets claimed.
    assert entries[-1].get("id", "").strip() == "q-456"


def test_parse_invoices_handles_empty_and_colonless_noise() -> None:
    assert CashuMint._parse_invoices("") == []
    assert CashuMint._parse_invoices("no colons here\nstill none\n") == []
    entries = CashuMint._parse_invoices("Amount: 4 sat\n stray line \nState: PAID")
    assert entries == [{"amount": "4 sat", "state": "PAID"}]


@pytest.mark.parametrize("value", ["UNPAID", "PAID", " EXPIRED "])
def test_parse_invoices_preserves_value_casing_and_strips_whitespace(value: str) -> None:
    (entry,) = CashuMint._parse_invoices(f"State:{value}")
    assert entry["state"] == value.strip()
