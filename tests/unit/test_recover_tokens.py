"""Unit tests for scripts/recover_tokens.py — Cashu token recovery tool.

Tests cover:
  * Token file parsing (pipe-delimited records)
  * cashuA / cashuB token decoding (JSON and CBOR variants)
  * Proof extraction and Y-value computation for NUT-07 checkstate
  * Checkstate request building
  * Router token submission (mocked HTTP)
  * Summary reporting logic

No live network calls — all HTTP interactions are mocked.
"""
from __future__ import annotations

import base64
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import cbor2
import pytest

from scripts.recover_tokens import (
    ACTION_SUBMIT_FAILED,
    TOKEN_PREFIX_A,
    TOKEN_PREFIX_B,
    TokenRecord,
    build_checkstate_request,
    compute_proof_y,
    decode_cashu_token,
    extract_proofs,
    parse_token_file,
    process_tokens,
    submit_token_to_router,
    summarize_results,
)

# --------------------------------------------------------------------------- #
# Fixtures — sample token file lines and synthetic tokens
# --------------------------------------------------------------------------- #

SAMPLE_LINE_FEE = (
    "2026-06-18T16:14:05Z | https://nofee.testnut.cashu.space | "
    "cashuBo2F0gaJhaUgAtM0n2IYaRGFwplaceholder | "
    "payment rejected: failed to open gate: exit status 1"
)
SAMPLE_LINE_EXCHANGE = (
    "2026-07-01T09:30:00Z | https://testnut.cashu.exchange | "
    "cashuBanotherfakebase64payload | no error column"
)

# A real-ish proof dict matching Cashu V3 structure
_PROOF = {
    "amount": 4,
    "id": "00abc123",
    "secret": "deadbeef" * 8,  # 64 hex chars
    "C": "02" + "ab" * 32,     # 33-byte compressed pubkey
}

_TOKEN_OBJ = {
    "token": [{"mint": "https://nofee.testnut.cashu.space", "proofs": [_PROOF]}],
    "unit": "sat",
}


def _make_cashuA(token_obj: dict | None = None) -> str:
    """Create a cashuA (base64url-JSON) token for testing."""
    obj = token_obj or _TOKEN_OBJ
    raw = json.dumps(obj, separators=(",", ":"))
    return TOKEN_PREFIX_A + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _make_cashuB(token_obj: dict | None = None) -> str:
    """Create a cashuB (base64url-CBOR) token for testing.

    Uses the Cashu V3 CBOR key abbreviations:
      t = token list, u = unit, i = mint, p = proofs,
      a = amount, id = keyset id, s = secret, c = C
    """
    obj = token_obj or _TOKEN_OBJ
    cbor_obj = {
        "t": [
            {
                "i": entry["mint"],
                "p": [
                    {
                        "a": p["amount"],
                        "id": p["id"],
                        "s": p["secret"],
                        "c": p["C"],
                    }
                    for p in entry["proofs"]
                ],
            }
            for entry in obj["token"]
        ],
        "u": obj.get("unit", "sat"),
    }
    return TOKEN_PREFIX_B + base64.urlsafe_b64encode(
        cbor2.dumps(cbor_obj)
    ).decode().rstrip("=")


CASHU_A_TOKEN = _make_cashuA()
CASHU_B_TOKEN = _make_cashuB()


# --------------------------------------------------------------------------- #
# parse_token_file
# --------------------------------------------------------------------------- #


def test_parse_single_line():
    content = SAMPLE_LINE_FEE + "\n"
    path = Path("/tmp/test_tokens_recover_parse.txt")
    path.write_text(content)
    try:
        records = parse_token_file(str(path))
        assert len(records) == 1
        r = records[0]
        assert r.timestamp == "2026-06-18T16:14:05Z"
        assert r.mint_url == "https://nofee.testnut.cashu.space"
        assert r.token.startswith("cashuB")
        assert "exit status 1" in r.error
    finally:
        path.unlink(missing_ok=True)


def test_parse_multiple_lines():
    content = f"{SAMPLE_LINE_FEE}\n{SAMPLE_LINE_EXCHANGE}\n"
    path = Path("/tmp/test_tokens_recover_parse2.txt")
    path.write_text(content)
    try:
        records = parse_token_file(str(path))
        assert len(records) == 2
        assert records[0].mint_url == "https://nofee.testnut.cashu.space"
        assert records[1].mint_url == "https://testnut.cashu.exchange"
    finally:
        path.unlink(missing_ok=True)


def test_parse_skips_blank_lines():
    content = f"\n{SAMPLE_LINE_FEE}\n\n\n"
    path = Path("/tmp/test_tokens_recover_parse3.txt")
    path.write_text(content)
    try:
        records = parse_token_file(str(path))
        assert len(records) == 1
    finally:
        path.unlink(missing_ok=True)


def test_parse_line_without_error_column():
    """Lines with only 3 fields (no rejection error) still parse."""
    line = "2026-06-18T16:14:05Z | https://nofee.testnut.cashu.space | cashuBabc"
    path = Path("/tmp/test_tokens_recover_parse4.txt")
    path.write_text(line + "\n")
    try:
        records = parse_token_file(str(path))
        assert len(records) == 1
        assert records[0].error == ""
    finally:
        path.unlink(missing_ok=True)


def test_parse_skips_malformed_lines():
    """Lines with fewer than 3 pipe-separated fields are skipped."""
    content = "not a token line\n" + SAMPLE_LINE_FEE + "\n"
    path = Path("/tmp/test_tokens_recover_parse5.txt")
    path.write_text(content)
    try:
        records = parse_token_file(str(path))
        assert len(records) == 1  # only the valid line
    finally:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# decode_cashu_token
# --------------------------------------------------------------------------- #


def test_decode_cashuA():
    decoded = decode_cashu_token(CASHU_A_TOKEN)
    assert "token" in decoded
    proofs = decoded["token"][0]["proofs"]
    assert len(proofs) == 1
    assert proofs[0]["amount"] == 4


def test_decode_cashuB():
    decoded = decode_cashu_token(CASHU_B_TOKEN)
    assert "t" in decoded
    entries = decoded["t"]
    assert len(entries) == 1
    proofs = entries[0]["p"]
    assert len(proofs) == 1
    assert proofs[0]["a"] == 4


def test_decode_invalid_prefix():
    with pytest.raises(ValueError, match="unknown token prefix"):
        decode_cashu_token("wtfBBQabc123")


def test_decode_garbage_base64():
    with pytest.raises(Exception):
        decode_cashu_token("cashuB!!!notvalidbase64!!!")


# --------------------------------------------------------------------------- #
# extract_proofs
# --------------------------------------------------------------------------- #


def test_extract_proofs_from_cashuA():
    decoded = decode_cashu_token(CASHU_A_TOKEN)
    proofs = extract_proofs(decoded)
    assert len(proofs) == 1
    assert proofs[0]["amount"] == 4
    assert "secret" in proofs[0]
    assert "C" in proofs[0]


def test_extract_proofs_from_cashuB():
    decoded = decode_cashu_token(CASHU_B_TOKEN)
    proofs = extract_proofs(decoded)
    assert len(proofs) == 1
    assert proofs[0]["amount"] == 4
    assert "secret" in proofs[0]
    assert "C" in proofs[0]


def test_extract_proofs_multiple():
    """Token with multiple proofs in multiple mints."""
    obj = {
        "token": [
            {"mint": "https://mint1.example.com", "proofs": [
                {"amount": 1, "id": "ks1", "secret": "a" * 64, "C": "02" + "a" * 64},
                {"amount": 2, "id": "ks1", "secret": "b" * 64, "C": "03" + "b" * 64},
            ]},
            {"mint": "https://mint2.example.com", "proofs": [
                {"amount": 4, "id": "ks2", "secret": "c" * 64, "C": "02" + "c" * 64},
            ]},
        ],
        "unit": "sat",
    }
    token = _make_cashuA(obj)
    decoded = decode_cashu_token(token)
    proofs = extract_proofs(decoded)
    assert len(proofs) == 3
    amounts = [p["amount"] for p in proofs]
    assert sorted(amounts) == [1, 2, 4]


def test_extract_proofs_empty():
    """Token with no proofs returns empty list."""
    obj = {"token": [{"mint": "https://m.example", "proofs": []}], "unit": "sat"}
    token = _make_cashuA(obj)
    decoded = decode_cashu_token(token)
    proofs = extract_proofs(decoded)
    assert proofs == []


# --------------------------------------------------------------------------- #
# compute_proof_y
# --------------------------------------------------------------------------- #

# Canonical cross-implementation vectors (cashu-cross-vectors.json, from the
# Amperstrand review). Y = hash_to_curve(secret) is what NUT-07 checkstate
# keys on — these pin the exact values the gonuts mint (btcec),
# cashu-core-lite (k256), and coincurve produce. Any divergence in the
# derivation fails here instead of silently mis-reporting spend state.
CROSS_VECTORS = [
    ("test-secret-01", "0279110ffdbbaccf1f96e0641dd8794fb206e8f95eb52c0fa001487b070cb5f7b1"),
    ("a", "029794c59a5d9b910a18e50e10623c864b77c7edf4552f8652b0c85d30ac0498f0"),
    # Hex-looking-secret trap: a valid 64-char hex string that MUST be
    # hashed as its 64 ASCII characters, never hex-decoded. Hashing the
    # decoded 32 bytes produces a plausible-but-wrong Y — this exact
    # divergence shipped in the original code AND in the first patch
    # draft in the review thread.
    ("deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
     "0244d4bdec44e84725e2b6d9d7a2896df8bc27b482e84e0cb2144272d318375bc3"),
]


@pytest.mark.parametrize("secret,expected_y", CROSS_VECTORS)
def test_compute_proof_y_cross_vectors(secret, expected_y):
    # C deliberately present: pins that the blind signature is IGNORED
    # in favor of hash_to_curve(secret).
    proof = {"secret": secret, "C": "02" + "ff" * 32}
    assert compute_proof_y(proof) == expected_y


def test_compute_proof_y_ignores_c():
    """A proof carrying C must NOT send C as its Y (the NUT-07 bug).

    C is the blind signature, not the secret-derived Y the mint keys
    checkstate on.
    """
    with_c = dict(_PROOF)  # has "C"
    without_c = {"secret": _PROOF["secret"]}
    assert compute_proof_y(with_c) == compute_proof_y(without_c)
    assert compute_proof_y(with_c) != _PROOF["C"]


def test_compute_proof_y_ignores_untrusted_y_field():
    """A Y field on an untrusted token must not be trusted to identify
    its own spend state — always re-derive from the secret."""
    fake_y = "02" + "ee" * 32
    assert compute_proof_y({"secret": "a", "Y": fake_y}) == compute_proof_y({"secret": "a"})
    assert compute_proof_y({"secret": "a", "Y": fake_y}) != fake_y


def test_compute_proof_y_ignores_cbor_short_c_key():
    """CBOR short-key 'c' (normalized C) must be ignored too."""
    with_c = {"secret": "a", "c": "02" + "ab" * 32}
    assert compute_proof_y(with_c) == compute_proof_y({"secret": "a"})


def test_compute_proof_y_hex_looking_secret_not_decoded():
    """Hex-looking secrets are hashed as their ASCII bytes verbatim."""
    secret = "deadbeef" * 8
    expected = "0244d4bdec44e84725e2b6d9d7a2896df8bc27b482e84e0cb2144272d318375bc3"
    assert compute_proof_y({"secret": secret}) == expected


def test_compute_proof_y_bytes_secret():
    """CBOR byte-string secrets hash identically to their str form."""
    expected = compute_proof_y({"secret": "test-secret-01"})
    assert compute_proof_y({"secret": b"test-secret-01"}) == expected


def test_compute_proof_y_cbor_short_s_key():
    """CBOR short key 's' is accepted for secret extraction."""
    assert compute_proof_y({"s": "a"}) == compute_proof_y({"secret": "a"})


def test_compute_proof_y_missing_secret():
    """No secret → empty string (filtered out of the Ys list)."""
    assert compute_proof_y({"amount": 4}) == ""


def test_compute_proof_y_returns_hex_string():
    """Y value should be a 66-char hex string (33-byte compressed pubkey)."""
    proof = {"secret": _PROOF["secret"]}
    y_hex = compute_proof_y(proof)
    assert isinstance(y_hex, str)
    assert len(y_hex) == 66  # 33 bytes = 66 hex chars
    assert y_hex[:2] in ("02", "03")  # compressed pubkey prefix


def test_compute_proof_y_deterministic():
    """Same secret always produces the same Y."""
    proof = {"secret": _PROOF["secret"]}
    y1 = compute_proof_y(proof)
    y2 = compute_proof_y(proof)
    assert y1 == y2


def test_compute_proof_y_different_secrets():
    """Different secrets produce different Y values."""
    y1 = compute_proof_y({"secret": "a" * 64})
    y2 = compute_proof_y({"secret": "b" * 64})
    assert y1 != y2


# --------------------------------------------------------------------------- #
# build_checkstate_request
# --------------------------------------------------------------------------- #


def test_build_checkstate_request_basic():
    proofs = [{"amount": 4, "id": "ks1", "secret": _PROOF["secret"]}]
    payload = build_checkstate_request(proofs)
    assert "Ys" in payload
    assert len(payload["Ys"]) == 1
    assert len(payload["Ys"][0]) == 66  # 33-byte hex


def test_build_checkstate_request_multiple_proofs():
    proofs = [
        {"amount": 1, "id": "ks1", "secret": "a" * 64},
        {"amount": 2, "id": "ks1", "secret": "b" * 64},
        {"amount": 4, "id": "ks1", "secret": "c" * 64},
    ]
    payload = build_checkstate_request(proofs)
    assert len(payload["Ys"]) == 3
    assert len(set(payload["Ys"])) == 3  # all different


# --------------------------------------------------------------------------- #
# submit_token_to_router (mocked HTTP)
# --------------------------------------------------------------------------- #


@patch("scripts.recover_tokens.requests.post")
def test_submit_token_success(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"ok": true}'
    mock_post.return_value = mock_resp

    status, body = submit_token_to_router("192.168.8.1", CASHU_B_TOKEN)
    assert status == 200
    assert '"ok": true' in body

    # Verify the raw token was POSTed as text/plain
    call_args = mock_post.call_args
    assert call_args.kwargs["data"] == CASHU_B_TOKEN
    assert call_args.kwargs["headers"]["Content-Type"] == "text/plain"
    assert "192.168.8.1:2121" in call_args.args[0]


@patch("scripts.recover_tokens.requests.post")
def test_submit_token_failure(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 402
    mock_resp.text = '{"error": "token already spent"}'
    mock_post.return_value = mock_resp

    status, body = submit_token_to_router("10.0.0.1", "cashuBtest")
    assert status == 402
    assert "already spent" in body


# --------------------------------------------------------------------------- #
# process_tokens (integration of parse + check + submit)
# --------------------------------------------------------------------------- #


def _make_token_file(records_data: list[dict]) -> Path:
    """Write sample records to a temp file."""
    lines = []
    for d in records_data:
        parts = [d["timestamp"], d["mint_url"], d["token"]]
        if d.get("error"):
            parts.append(d["error"])
        lines.append(" | ".join(parts))
    path = Path("/tmp/test_tokens_recover_process.txt")
    path.write_text("\n".join(lines) + "\n")
    return path


@patch("scripts.recover_tokens.check_token_state")
@patch("scripts.recover_tokens.requests.post")
def test_process_tokens_check_mode_all_unspent(mock_post, mock_check):
    """--check mode: reports state without submitting."""
    # Real return shape of check_token_state: processed counts, not raw states.
    mock_check.return_value = {"unspent_count": 1, "spent_count": 0, "total_proofs": 1}
    path = _make_token_file([
        {"timestamp": "2026-06-18T16:14:05Z",
         "mint_url": "https://nofee.testnut.cashu.space",
         "token": CASHU_B_TOKEN,
         "error": "rejected"},
    ])
    try:
        results = process_tokens(
            str(path), mode="check", router_ip="192.168.8.1",
        )
        assert len(results) == 1
        assert results[0].state == "UNSPENT"
        assert results[0].action == "CHECK_ONLY"
        mock_post.assert_not_called()  # no submission in check mode
    finally:
        path.unlink(missing_ok=True)


@patch("scripts.recover_tokens.check_token_state")
@patch("scripts.recover_tokens.requests.post")
def test_process_tokens_recover_mode_unspent(mock_post, mock_check):
    """--recover mode: submits unspent tokens."""
    mock_check.return_value = {"unspent_count": 1, "spent_count": 0, "total_proofs": 1}
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"ok": true}'
    mock_post.return_value = mock_resp

    path = _make_token_file([
        {"timestamp": "2026-06-18T16:14:05Z",
         "mint_url": "https://nofee.testnut.cashu.space",
         "token": CASHU_B_TOKEN,
         "error": "rejected"},
    ])
    try:
        results = process_tokens(
            str(path), mode="recover", router_ip="192.168.8.1",
        )
        assert len(results) == 1
        assert results[0].state == "UNSPENT"
        assert results[0].action == "SUBMITTED"
        assert results[0].submit_status == 200
        mock_post.assert_called_once()
    finally:
        path.unlink(missing_ok=True)


@patch("scripts.recover_tokens.check_token_state")
@patch("scripts.recover_tokens.requests.post")
def test_process_tokens_recover_mode_spent_skipped(mock_post, mock_check):
    """--recover mode: skips already-spent tokens."""
    mock_check.return_value = {"unspent_count": 0, "spent_count": 1, "total_proofs": 1}
    path = _make_token_file([
        {"timestamp": "2026-06-18T16:14:05Z",
         "mint_url": "https://nofee.testnut.cashu.space",
         "token": CASHU_B_TOKEN,
         "error": "rejected"},
    ])
    try:
        results = process_tokens(
            str(path), mode="recover", router_ip="192.168.8.1",
        )
        assert results[0].state == "SPENT"
        assert results[0].action == "SKIPPED_SPENT"
        mock_post.assert_not_called()
    finally:
        path.unlink(missing_ok=True)


@patch("scripts.recover_tokens.check_token_state")
def test_process_tokens_checkstate_error(mock_check):
    """Network error during checkstate is reported, not fatal."""
    mock_check.side_effect = ConnectionError("mint unreachable")
    path = _make_token_file([
        {"timestamp": "2026-06-18T16:14:05Z",
         "mint_url": "https://nofee.testnut.cashu.space",
         "token": CASHU_B_TOKEN,
         "error": "rejected"},
    ])
    try:
        results = process_tokens(
            str(path), mode="check", router_ip="192.168.8.1",
        )
        assert results[0].state == "ERROR"
        assert results[0].action == "CHECK_FAILED"
    finally:
        path.unlink(missing_ok=True)


@patch("scripts.recover_tokens.check_token_state")
@patch("scripts.recover_tokens.requests.post")
def test_process_tokens_dry_run_mode(mock_post, mock_check):
    """--dry-run mode: checks state but never submits."""
    mock_check.return_value = {"unspent_count": 1, "spent_count": 0, "total_proofs": 1}
    path = _make_token_file([
        {"timestamp": "2026-06-18T16:14:05Z",
         "mint_url": "https://nofee.testnut.cashu.space",
         "token": CASHU_B_TOKEN,
         "error": "rejected"},
    ])
    try:
        results = process_tokens(
            str(path), mode="dry-run", router_ip="192.168.8.1",
        )
        assert results[0].state == "UNSPENT"
        assert results[0].action == "DRY_RUN"
        mock_post.assert_not_called()
    finally:
        path.unlink(missing_ok=True)


@patch("scripts.recover_tokens.check_token_state")
@patch("scripts.recover_tokens.requests.post")
def test_process_tokens_recover_mode_submit_failed(mock_post, mock_check):
    """--recover mode: a submit exception gets its own SUBMIT_FAILED action,
    distinct from a successful submission."""
    mock_check.return_value = {"unspent_count": 1, "spent_count": 0, "total_proofs": 1}
    mock_post.side_effect = ConnectionError("router unreachable")
    path = _make_token_file([
        {"timestamp": "2026-06-18T16:14:05Z",
         "mint_url": "https://nofee.testnut.cashu.space",
         "token": CASHU_B_TOKEN,
         "error": "rejected"},
    ])
    try:
        results = process_tokens(
            str(path), mode="recover", router_ip="192.168.8.1",
        )
        assert results[0].state == "UNSPENT"
        assert results[0].action == ACTION_SUBMIT_FAILED
        assert results[0].submit_status is None
        assert "router unreachable" in results[0].error
    finally:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# summarize_results
# --------------------------------------------------------------------------- #


def test_summarize_results_counts():
    from scripts.recover_tokens import ResultRecord
    results = [
        ResultRecord(timestamp="t1", mint_url="m1", amount=4,
                     state="UNSPENT", action="SUBMITTED", submit_status=200),
        ResultRecord(timestamp="t2", mint_url="m2", amount=2,
                     state="SPENT", action="SKIPPED_SPENT"),
        ResultRecord(timestamp="t3", mint_url="m1", amount=4,
                     state="ERROR", action="CHECK_FAILED"),
    ]
    summary = summarize_results(results)
    assert summary["total"] == 3
    assert summary["unspent"] == 1
    assert summary["spent"] == 1
    assert summary["errors"] == 1
    assert summary["submitted"] == 1
