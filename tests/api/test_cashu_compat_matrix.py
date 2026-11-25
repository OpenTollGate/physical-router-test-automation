"""Cashu token/keyset compatibility matrix — the source of truth for what works.

Cross-references: issue #35, #26, AGENTS.md "Cashu Token Version Compatibility"
and "Keyset ID compatibility" sections.

The matrix documents expected behavior for every combination of:
  - Token format: V1 (legacy bare-list), V3 (cashuA JSON), V4 (cashuB CBOR)
  - Keyset version: V1 (00-prefix, 16 hex), V2 (01-prefix, 66 hex)
  - Backend: Go (gonuts), Rust (cdk-rs)

Existing tests cover individual cells:
  - test_token_formats.py: V3 structure, V4 acceptance + V4/V2 regression
  - test_keyset_id_versions.py: V1/V2 format, derivation, discovery
  - test_edge_tokens.py: garbage/empty/duplicate tokens
  - test_wrong_mint.py: wrong-mint token rejection

This file adds the CROSS-PRODUCT view. Reconciled 2026-11 (#294): the
Go column previously recorded the GONUTS-ERA failure modes (V4 → reject,
V2 keyset → fatal startup crash) long after they were fixed — V2-keyset
support via tollgate-module-basic-go PR #167 (the 'Got invalid keyset'
crash was a multi-mint wallet registration bug, not a V2-keyset issue)
and V4 support via gonuts-tollgate v0.8.0's resolveShortKeysetIds (PR
#286). The current Go backend on main (gonuts-tollgate v0.11.2) accepts
every token×keyset combination — clean-room verified 2026-09-19 ("V4+V2
pays fine everywhere"; see AGENTS.md "Lessons Learned" and the live
verdicts in test_token_formats.py). Field note: pre-v0.8.0 builds (the
v0.5.0 field fleet) still fail V4+V2 only.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re

import pytest

from lib.backend import BackendConfig
from lib.helpers import require_client_identity

log = logging.getLogger("tollgate.api.cashu_matrix")
pytestmark = [
    pytest.mark.story("R2"),
    pytest.mark.tip("TIP-02"),pytest.mark.api, pytest.mark.extended]

MINT_URL = os.environ.get("TOLLGATE_TEST_MINT_URL", "https://testnut.cashu.exchange")

V1_PATTERN = re.compile(r"^00[0-9a-f]{14}$")
V2_PATTERN = re.compile(r"^01[0-9a-f]{64}$")


# --------------------------------------------------------------------------- #
# The compatibility matrix (source of truth — update when backend changes)
# --------------------------------------------------------------------------- #

#: Each cell: (token_format, keyset_version, backend) → expected_behavior
#:
#: expected_behavior values:
#:   "accept"    — backend processes the token successfully
#:   "reject"    — backend returns an error but stays running
#:   "fatal"     — backend crashes on startup or during processing
#:   "unknown"   — not tested or behavior varies
#:
#: References: AGENTS.md, issue #26, issue #35
MATRIX = {
    # ── Go backend (gonuts) ──────────────────────────────────────────
    # Reconciled 2026-11 (#294) with the observed current behavior:
    #   * V2 keysets: full support since tollgate-module-basic-go PR #167
    #     (the historical 'fatal' startup crash — 'Got invalid keyset.
    #     Derived id: ... but got ...' — was a multi-mint wallet
    #     registration bug, NOT a V2-keyset issue; AGENTS.md "Keyset ID
    #     compatibility" table: V1 and V2 both 'Full support', verified
    #     end-to-end kind=1022).
    #   * V4 tokens: accepted since gonuts-tollgate v0.8.0
    #     (resolveShortKeysetIds, PR #286); main pins v0.11.2. V4+V1
    #     verified e2e (kind=1022, allotment=176160768); V4+V2 pays after
    #     the 2026-09-19 clean-room retest ("V4+V2 pays fine everywhere").
    #   * Field caveat: pre-v0.8.0 field builds (v0.5.0 fleet) still fail
    #     V4+V2 only — V4+V1 needs no short-ID resolution and pays even
    #     there.
    ("V1_token", "V1_keyset", "go"): "accept",
    ("V3_token", "V1_keyset", "go"): "accept",
    ("V4_token", "V1_keyset", "go"): "accept",
    ("V1_token", "V2_keyset", "go"): "accept",
    ("V3_token", "V2_keyset", "go"): "accept",
    ("V4_token", "V2_keyset", "go"): "accept",

    # ── Rust backend (cdk-rs) ────────────────────────────────────────
    ("V1_token", "V1_keyset", "rust"): "accept",
    ("V3_token", "V1_keyset", "rust"): "accept",
    ("V4_token", "V1_keyset", "rust"): "unknown",
    ("V1_token", "V2_keyset", "rust"): "accept",
    ("V3_token", "V2_keyset", "rust"): "accept",
    ("V4_token", "V2_keyset", "rust"): "unknown",

    # ── Go backend with cdk-go wallet (go-cdk) ─────────────────────
    # All "unknown" until CdkWallet adapter is implemented and tested
    # on physical hardware. Expected: all "accept" because CDK handles
    # V2 keysets and V4 tokens natively (the bugs gonuts has).
    ("V1_token", "V1_keyset", "go-cdk"): "unknown",
    ("V3_token", "V1_keyset", "go-cdk"): "unknown",
    ("V4_token", "V1_keyset", "go-cdk"): "unknown",
    ("V1_token", "V2_keyset", "go-cdk"): "unknown",
    ("V3_token", "V2_keyset", "go-cdk"): "unknown",
    ("V4_token", "V2_keyset", "go-cdk"): "unknown",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _decode_cashuA(token: str) -> dict | list:
    assert token.startswith("cashuA"), f"Expected cashuA prefix, got: {token[:20]}"
    payload = token[len("cashuA"):]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _get_proofs(decoded: dict | list) -> list[dict]:
    if isinstance(decoded, list):
        proofs = []
        for entry in decoded:
            proofs.extend(entry.get("proofs", []))
        return proofs
    token_list = decoded.get("token", [])
    proofs = []
    for entry in token_list:
        proofs.extend(entry.get("proofs", []))
    return proofs


def _classify_keyset_id(keyset_id: str) -> str:
    if V1_PATTERN.match(keyset_id):
        return "V1"
    if V2_PATTERN.match(keyset_id):
        return "V2"
    return "unknown"


def _get_mint_keysets():
    from urllib.request import Request, urlopen
    from urllib.error import URLError

    endpoint = f"{MINT_URL.rstrip('/')}/v1/keys"
    req = Request(endpoint, headers={"User-Agent": "tollgate-test/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, Exception) as exc:
        pytest.skip(f"Cannot reach mint at {endpoint}: {exc}")
    return data.get("keysets", [])


def _classify_token_version(token: str) -> str:
    if token.startswith("cashuA"):
        decoded = _decode_cashuA(token)
        if isinstance(decoded, list):
            return "V1"
        if "token" in decoded:
            return "V3"
        return "V3"
    if token.startswith("cashuB"):
        return "V4"
    return "unknown"


# --------------------------------------------------------------------------- #
# Matrix documentation tests (always runnable — no router needed)
# --------------------------------------------------------------------------- #


class TestMatrixDocumentation:
    """Verify the matrix data structure is complete and consistent."""

    TOKEN_FORMATS = ["V1_token", "V3_token", "V4_token"]
    KEYSET_VERSIONS = ["V1_keyset", "V2_keyset"]
    BACKENDS = ["go", "rust"]

    def test_matrix_covers_all_combinations(self):
        """Every token × keyset × backend combination has an entry."""
        missing = []
        for tok in self.TOKEN_FORMATS:
            for ks in self.KEYSET_VERSIONS:
                for be in self.BACKENDS:
                    key = (tok, ks, be)
                    if key not in MATRIX:
                        missing.append(key)
        assert not missing, f"Matrix missing entries: {missing}"

    def test_matrix_values_are_valid(self):
        """Every matrix value is one of the known behaviors."""
        valid = {"accept", "reject", "fatal", "unknown"}
        for key, behavior in MATRIX.items():
            assert behavior in valid, f"Invalid behavior '{behavior}' for {key}"

    def test_go_backend_accepts_all_combinations_on_main(self):
        """Go on main (gonuts v0.11.2) accepts every token×keyset cell (#294).

        The historical failures are fixed and recorded: V4 acceptance
        since gonuts-tollgate v0.8.0 (resolveShortKeysetIds, PR #286) and
        V2-keyset full support since tollgate-module-basic-go PR #167
        (the 'fatal' startup crash was a multi-mint registration bug).
        Pre-v0.8.0 field builds remain V4+V2-only failures.
        """
        for tok in self.TOKEN_FORMATS:
            for ks in self.KEYSET_VERSIONS:
                behavior = MATRIX[(tok, ks, "go")]
                assert behavior == "accept", \
                    f"Go+{tok}+{ks} on main should accept (reconciled 2026-11, #294), got {behavior}"

    def test_v2_keyset_fatal_cells_are_gone(self):
        """No 'fatal' cell may return without a live crash record (#294).

        A 'fatal' claim must be backed by a reproduced startup crash; the
        V2-keyset crash was root-caused to a multi-mint wallet
        registration bug (tbmg #167) and does not reproduce on main.
        """
        fatal = [key for key, behavior in MATRIX.items() if behavior == "fatal"]
        assert not fatal, \
            f"'fatal' cells reappeared without a live crash record: {fatal}"

    def test_rust_backend_accepts_v1_and_v3(self):
        """Rust backend accepts V1/V3 tokens with V1 keysets."""
        for tok in ["V1_token", "V3_token"]:
            behavior = MATRIX[(tok, "V1_keyset", "rust")]
            assert behavior == "accept", \
                f"Rust+{tok}+V1_keyset should accept, got {behavior}"


# --------------------------------------------------------------------------- #
# Live matrix verification (requires router + mint)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def backend_type():
    return BackendConfig()


@pytest.fixture(scope="module")
def mint_keysets():
    return _get_mint_keysets()


class TestLiveMatrix:
    """Verify the live backend behavior matches the documented matrix."""

    def test_minted_token_format_matches_matrix(self, cashu, backend_type):
        """The minted token format must match what the matrix expects for this backend."""
        token = cashu.mint(amount=4)
        token_version = _classify_token_version(token)
        assert token_version in ("V1", "V3", "V4"), \
            f"Minter produced {token_version} token — matrix expects V1/V3/V4 (V4 accepted since gonuts v0.8.0, #294)"

    def test_minted_token_keyset_matches_matrix(self, cashu, backend_type, mint_keysets):
        """The keyset version in the minted token must be compatible with the backend."""
        token = cashu.mint(amount=4)
        decoded = _decode_cashuA(token)
        proofs = _get_proofs(decoded)
        assert proofs, "No proofs in minted token"

        keyset_id = proofs[0].get("id", "")
        keyset_version = _classify_keyset_id(keyset_id)

        if backend_type.is_go:
            # Regression guard (#294): a 'fatal' cell here would mean the
            # multi-mint registration crash class returned — V2 keysets
            # are fully supported on main (tbmg #167).
            behavior = MATRIX.get(("V3_token", f"{keyset_version}_keyset", "go"), "unknown")
            assert behavior != "fatal", \
                f"Matrix records a FATAL Go cell for {keyset_version} keysets ({keyset_id}) — " \
                f"a live crash record must back any 'fatal' claim. " \
                f"See AGENTS.md 'Keyset ID compatibility'."
        log.info("Backend=%s, keyset=%s (%s) — compatible", backend_type.type, keyset_id, keyset_version)

    def test_v3_token_accepted_by_current_backend(self, router, cashu, backend_type):
        """V3 token with current mint's keyset is accepted by the running backend."""
        require_client_identity(router)

        token = cashu.mint(amount=4)
        token_version = _classify_token_version(token)
        resp = router.pay_direct(token)

        decoded = _decode_cashuA(token)
        proofs = _get_proofs(decoded)
        keyset_id = proofs[0].get("id", "") if proofs else ""
        keyset_version = _classify_keyset_id(keyset_id)

        accepted = (
            resp.get("kind") in (1022, 21000, 10021)
            or resp.get("success") is True
        )

        expected = MATRIX.get((f"{token_version}_token", f"{keyset_version}_keyset", backend_type.type), "unknown")
        log.info("Live: %s token + %s keyset + %s backend → expected=%s, accepted=%s",
                 token_version, keyset_version, backend_type.type, expected, accepted)

        if expected == "accept":
            assert accepted, \
                f"Matrix says accept but backend rejected: {token_version}+{keyset_version}+{backend_type.type}"
        elif expected == "reject":
            assert not accepted or resp.get("kind") != 1022, \
                f"Matrix says reject but backend accepted: {resp}"

    def test_v4_token_rejected(self, router, backend_type):
        """A SYNTHETIC cashuB blob is rejected — garbage CBOR, not a minted V4.

        #294 note: real V4 tokens (cdk-cli minted) are ACCEPTED by the Go
        backend since gonuts v0.8.0 — this rail pins the garbage-rejection
        path (the backend must not 500 or accept nonsense), not V4 policy.
        """
        require_client_identity(router)

        synthetic_b64 = base64.urlsafe_b64encode(b"\x82\x01\x02").decode()
        cashuB_token = "cashuB" + synthetic_b64

        resp = router.pay_direct(cashuB_token)

        accepted = (
            resp.get("kind") == 1022
            or resp.get("success") is True
        )
        assert not accepted, \
            f"Synthetic garbage cashuB token was ACCEPTED: {resp}"

    def test_wrong_mint_token_rejected(self, router, cashu):
        """Token from unconfigured mint is rejected."""
        require_client_identity(router)

        wrong_token = cashu.synthetic_wrong_mint_token()
        resp = router.pay_direct(wrong_token)

        accepted = (
            resp.get("kind") == 1022
            or resp.get("success") is True
        )
        assert not accepted, \
            f"Wrong-mint token was ACCEPTED: {resp}"

    def test_empty_and_garbage_tokens_rejected(self, router):
        """Empty and garbage tokens don't crash the backend."""
        require_client_identity(router)

        for bad_token in ["", "not-a-token", "cashuA!!!garbage!!!"]:
            resp = router.pay_direct(bad_token)
            accepted = (
                resp.get("kind") == 1022
                or resp.get("success") is True
            )
            assert not accepted, \
                f"Invalid token '{bad_token[:30]}' was accepted: {resp}"

    def test_matrix_summary_logged(self, router, cashu, backend_type, mint_keysets):
        """Log the full compatibility picture for this test run.

        This test always passes — it's a diagnostic that records what was
        tested, what keysets the mint offers, and what the matrix predicts.
        """
        token = cashu.mint(amount=4)
        token_version = _classify_token_version(token)

        decoded = _decode_cashuA(token)
        proofs = _get_proofs(decoded)
        keyset_id = proofs[0].get("id", "") if proofs else ""
        keyset_version = _classify_keyset_id(keyset_id)

        mint_keyset_versions = {}
        for ks in mint_keysets:
            v = _classify_keyset_id(ks["id"])
            mint_keyset_versions.setdefault(v, []).append(ks["id"])

        log.info("=== Cashu Compatibility Matrix Summary ===")
        log.info("Backend: %s", backend_type.type)
        log.info("Mint: %s", MINT_URL)
        log.info("Mint keysets: %s", {v: len(ids) for v, ids in mint_keyset_versions.items()})
        log.info("Minted token: %s + %s keyset (%s)", token_version, keyset_version, keyset_id)
        log.info("")
        log.info("Matrix predictions for this backend (%s):", backend_type.type)
        for (tok, ks, be), behavior in sorted(MATRIX.items()):
            if be != backend_type.type:
                continue
            marker = " ← CURRENT" if (tok == f"{token_version}_token" and ks == f"{keyset_version}_keyset") else ""
            log.info("  %s × %s → %s%s", tok, ks, behavior, marker)
