# TIP-02: Cashu Payments — Token Format Validation
#
# Validates Cashu token encoding (cashuA V3 JSON vs cashuB V4 CBOR),
# decoding, internal structure, and TollGate backend acceptance.
#
# Background:
#   NUT-00 defines two token serialisation formats:
#     V3 = cashuA + base64url(JSON) — the only format TollGate accepts.
#     V4 = cashuB + base64url(CBOR) — not supported by TollGate.
#
#   The `--v3` flag was added to `cdk-cli send` to force V3 output
#   because CDK 0.16.0+ defaults to V4 (CBOR). These tests confirm
#   that the minter produces cashuA tokens and that the backend
#   rejects cashuB tokens.

import base64
import json

import pytest

from lib.helpers import require_client_identity

pytestmark = [pytest.mark.api, pytest.mark.extended]


def _decode_cashuA(token: str) -> dict | list:
    """Strip the cashuA prefix, fix base64url padding, and decode to JSON."""
    assert token.startswith("cashuA"), f"Expected cashuA prefix, got: {token[:20]}"
    payload_b64 = token[len("cashuA"):]
    # NUT-00: clients MUST handle both padded and unpadded base64url
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    raw = base64.urlsafe_b64decode(padded)
    return json.loads(raw)


def _get_proofs(decoded: dict | list) -> list[dict]:
    """Extract proofs list from either {"token":[...]} wrapper or bare list."""
    if isinstance(decoded, list):
        # Legacy V1 format: bare list of mint entries
        proofs = []
        for entry in decoded:
            proofs.extend(entry.get("proofs", []))
        return proofs
    # V3 format: {"token": [{"mint": ..., "proofs": [...]}]}
    token_list = decoded.get("token", [])
    proofs = []
    for entry in token_list:
        proofs.extend(entry.get("proofs", []))
    return proofs


@pytest.mark.extended
def test_minted_token_is_cashuA_format(cashu):
    """Verify the mint produces cashuA (V3 JSON) tokens, not cashuB (V4 CBOR).

    The --v3 flag was added to cdk-cli send to prevent V4 output.
    If this test fails, the minter is producing CBOR tokens that
    TollGate cannot parse.
    """
    token = cashu.mint(amount=4)
    assert token.startswith("cashuA"), \
        f"Minter produced non-cashuA token (prefix: {token[:10]}). " \
        "The --v3 flag may not be in effect, producing V4 CBOR tokens."
    assert not token.startswith("cashuB"), \
        "Minter produced cashuB (V4 CBOR) token — TollGate only accepts cashuA"


@pytest.mark.extended
def test_decode_cashuA_token_structure(cashu):
    """Decode a minted token and verify the top-level JSON structure.

    Expected V3 structure (per NUT-00):
        {"token": [{"mint": "<url>", "proofs": [<proof objects>]}]}

    Some minters (legacy cashu CLI with --legacy) produce a bare list:
        [{"mint": "<url>", "proofs": [<proof objects>]}]

    Both formats must decode and contain at least one mint entry with proofs.
    """
    token = cashu.mint(amount=4)
    decoded = _decode_cashuA(token)

    if isinstance(decoded, list):
        # Legacy bare-list format
        assert len(decoded) > 0, "Token decoded to empty list"
        for entry in decoded:
            assert "mint" in entry, f"Missing 'mint' key in entry: {entry}"
            assert "proofs" in entry, f"Missing 'proofs' key in entry: {entry}"
            assert isinstance(entry["proofs"], list), \
                f"'proofs' is not a list: {type(entry['proofs'])}"
    else:
        # Standard V3 wrapped format
        assert "token" in decoded, \
            f"V3 token missing 'token' key. Keys: {list(decoded.keys())}"
        token_list = decoded["token"]
        assert isinstance(token_list, list), \
            f"'token' is not a list: {type(token_list)}"
        assert len(token_list) > 0, "Token list is empty"
        for entry in token_list:
            assert "mint" in entry, f"Missing 'mint' key in entry: {entry}"
            assert isinstance(entry["mint"], str), \
                f"'mint' is not a string: {type(entry['mint'])}"
            assert entry["mint"].startswith("http"), \
                f"Mint URL doesn't look like a URL: {entry['mint']}"
            assert "proofs" in entry, f"Missing 'proofs' key in entry: {entry}"
            assert isinstance(entry["proofs"], list), \
                f"'proofs' is not a list: {type(entry['proofs'])}"


@pytest.mark.extended
def test_cashuA_token_has_valid_proof_fields(cashu):
    """Check each proof in a decoded token has the required fields.

    Per NUT-00, each proof must contain:
      - amount (int): denomination value, must be > 0
      - id (str): keyset ID in hex
      - secret (str): secret for the proof
      - C (str): blinded signature point C in hex
    """
    token = cashu.mint(amount=4)
    decoded = _decode_cashuA(token)
    proofs = _get_proofs(decoded)

    assert len(proofs) > 0, "Token contains no proofs"

    for i, proof in enumerate(proofs):
        assert isinstance(proof, dict), \
            f"Proof {i} is not a dict: {type(proof)}"

        # amount: positive integer
        assert "amount" in proof, f"Proof {i} missing 'amount': {proof}"
        assert isinstance(proof["amount"], int), \
            f"Proof {i} 'amount' is not int: {type(proof['amount'])}"
        assert proof["amount"] > 0, \
            f"Proof {i} has non-positive amount: {proof['amount']}"

        # id: non-empty hex string (keyset ID)
        assert "id" in proof, f"Proof {i} missing 'id': {proof}"
        assert isinstance(proof["id"], str), \
            f"Proof {i} 'id' is not str: {type(proof['id'])}"
        assert len(proof["id"]) > 0, f"Proof {i} has empty 'id'"

        # secret: non-empty string
        assert "secret" in proof, f"Proof {i} missing 'secret': {proof}"
        assert isinstance(proof["secret"], str), \
            f"Proof {i} 'secret' is not str: {type(proof['secret'])}"
        assert len(proof["secret"]) > 0, f"Proof {i} has empty 'secret'"

        # C: non-empty hex string (blinded signature)
        assert "C" in proof, f"Proof {i} missing 'C': {proof}"
        assert isinstance(proof["C"], str), \
            f"Proof {i} 'C' is not str: {type(proof['C'])}"
        assert len(proof["C"]) > 0, f"Proof {i} has empty 'C'"


@pytest.mark.extended
def test_cashuA_base64_padding_handled():
    """Verify base64url decoding handles both padded and unpadded tokens.

    NUT-00 requires that clients handle base64url with or without
    '=' padding characters. This test constructs both variants of
    a synthetic token and verifies both decode to the same JSON.
    """
    payload = [{"mint": "https://test.example.com",
                "proofs": [{"amount": 2, "id": "00abcd1234ef5678",
                            "secret": "test_secret", "C": "02abcdef"}]}]
    payload_json = json.dumps(payload)

    # Encode with standard base64 (includes padding)
    encoded_padded = base64.b64encode(payload_json.encode()).decode()
    token_padded = "cashuA" + encoded_padded

    # Remove padding to simulate a mint that strips '='
    encoded_stripped = encoded_padded.rstrip("=")
    token_stripped = "cashuA" + encoded_stripped

    # Both must decode to the same JSON structure
    decoded_padded = _decode_cashuA(token_padded)
    decoded_stripped = _decode_cashuA(token_stripped)

    assert decoded_padded == decoded_stripped, \
        "Padded and unpadded base64 decoded to different structures"

    # Verify the round-trip content is correct
    proofs = _get_proofs(decoded_padded)
    assert len(proofs) == 1
    assert proofs[0]["amount"] == 2


@pytest.mark.extended
def test_cashuB_token_rejected_by_backend(router):
    """Verify TollGate rejects cashuB (V4 CBOR) tokens.

    TollGate only accepts cashuA (V3 JSON). If a V4 token were
    accepted, it would indicate a protocol handling bug. This test
    uses a synthetic cashuB token to document that rejection.

    See: https://github.com/cashubtc/nuts/pull/182 (V4 spec)
    """
    require_client_identity(router)

    # Construct a synthetic cashuB token with garbage CBOR payload
    synthetic_b64 = base64.urlsafe_b64encode(b"\x82\x01\x02").decode()
    cashuB_token = "cashuB" + synthetic_b64

    resp = router.pay_direct(cashuB_token)

    # Backend should not accept this — kind 1022 means session created (bad)
    assert resp.get("kind") != 1022, \
        f"cashuB token was ACCEPTED — backend should reject V4 format: {resp}"
    assert resp.get("success") is not True, \
        f"cashuB token resulted in success — unexpected: {resp}"


@pytest.mark.extended
def test_synthetic_wrong_mint_token_format(cashu):
    """Verify synthetic_wrong_mint_token() produces valid cashuA structure.

    The synthetic token is used by test_wrong_mint.py to verify the
    backend rejects tokens from unconfigured mints. This test ensures
    the synthetic token itself is well-formed: cashuA prefix, valid
    base64, decodable JSON, with a non-matching mint URL.
    """
    token = cashu.synthetic_wrong_mint_token()

    # Must be cashuA format
    assert token.startswith("cashuA"), \
        f"Synthetic wrong-mint token has wrong prefix: {token[:10]}"

    # Must decode cleanly
    decoded = _decode_cashuA(token)

    # Must contain at least one mint entry
    proofs = _get_proofs(decoded)
    assert len(proofs) > 0, "Synthetic wrong-mint token has no proofs"

    # Mint URL must NOT match any real mint
    if isinstance(decoded, list):
        mint_urls = [entry.get("mint", "") for entry in decoded]
    else:
        mint_urls = [entry.get("mint", "") for entry in decoded.get("token", [])]

    assert any("wrong-mint" in url for url in mint_urls), \
        f"Expected wrong-mint URL in synthetic token, got: {mint_urls}"


@pytest.mark.extended
def test_token_proof_amount_matches_request(cashu):
    """Verify the sum of proof amounts equals the requested mint amount.

    Cashu uses powers-of-2 denominations. For amount=8, the mint may
    split into multiple proofs (e.g., 4+4 or 2+2+2+2). The sum of
    all proof amounts must equal the originally requested amount.
    """
    requested_amount = 8
    token = cashu.mint(amount=requested_amount)
    decoded = _decode_cashuA(token)
    proofs = _get_proofs(decoded)

    total = sum(p["amount"] for p in proofs)
    assert total == requested_amount, \
        f"Proof amounts sum to {total}, expected {requested_amount}. " \
        f"Individual amounts: {[p['amount'] for p in proofs]}"


@pytest.mark.extended
def test_multi_proof_token_structure(cashu):
    """Verify tokens with multiple proofs have valid structure in each proof.

    Some mints return tokens with 2+ proofs (e.g., 4+4=8 split).
    Each proof must independently have valid amount, id, secret, and C.
    This catches partial corruption where some proofs are malformed.
    """
    # Use amount=8 to increase chance of multi-proof split
    token = cashu.mint(amount=8)
    decoded = _decode_cashuA(token)
    proofs = _get_proofs(decoded)

    assert len(proofs) >= 1, \
        f"Expected at least 1 proof for amount=8, got {len(proofs)}"

    for i, proof in enumerate(proofs):
        assert isinstance(proof.get("amount"), int), \
            f"Proof {i}: 'amount' missing or not int: {proof}"
        assert proof["amount"] > 0, \
            f"Proof {i}: non-positive amount {proof['amount']}"

        assert isinstance(proof.get("id"), str) and len(proof["id"]) > 0, \
            f"Proof {i}: 'id' missing or empty"

        assert isinstance(proof.get("secret"), str) and len(proof["secret"]) > 0, \
            f"Proof {i}: 'secret' missing or empty"

        assert isinstance(proof.get("C"), str) and len(proof["C"]) > 0, \
            f"Proof {i}: 'C' missing or empty"

    # Total must be correct regardless of split count
    total = sum(p["amount"] for p in proofs)
    assert total == 8, \
        f"Multi-proof total is {total} (expected 8). " \
        f"Split: {[p['amount'] for p in proofs]}"
