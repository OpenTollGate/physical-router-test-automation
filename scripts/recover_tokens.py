#!/usr/bin/env python3
"""Recover stuck Cashu tokens from tollgate-wrt tokens-to-recover.txt.

Usage:
    # Check which tokens are still unspent (no changes)
    python scripts/recover-tokens.py --file tokens-to-recover.txt --check

    # Check + re-submit unspent tokens to router
    python scripts/recover-tokens.py --file tokens-to-recover.txt --recover

    # Dry run (parse + show what would happen, no network calls)
    python scripts/recover-tokens.py --file tokens-to-recover.txt --dry-run

Token file format (pipe-delimited, one per line):
    2026-06-18T16:14:05Z | https://nofee.testnut.cashu.space | cashuBo2F0... | payment rejected: failed to open gate: exit status 1
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# NUT-07 checkstate endpoint
CHECKSTATE_PATH = "/v1/checkstate"

# Cashu token prefixes
TOKEN_PREFIX_A = "cashuA"
TOKEN_PREFIX_B = "cashuB"

# Actions reported in ResultRecord
ACTION_CHECK_ONLY = "CHECK_ONLY"
ACTION_SUBMITTED = "SUBMITTED"
ACTION_SKIPPED_SPENT = "SKIPPED_SPENT"
ACTION_CHECK_FAILED = "CHECK_FAILED"
ACTION_DRY_RUN = "DRY_RUN"


# --------------------------------------------------------------------------- #
# Data records
# --------------------------------------------------------------------------- #

@dataclass
class TokenRecord:
    """One line from tokens-to-recover.txt."""
    timestamp: str
    mint_url: str
    token: str
    error: str = ""


@dataclass
class ResultRecord:
    """Result of processing a single token."""
    timestamp: str
    mint_url: str
    amount: int = 0
    state: str = ""        # UNSPENT / SPENT / ERROR
    action: str = ""       # CHECK_ONLY / SUBMITTED / SKIPPED_SPENT / CHECK_FAILED / DRY_RUN
    submit_status: int | None = None
    submit_body: str = ""
    error: str = ""


# --------------------------------------------------------------------------- #
# Token file parsing
# --------------------------------------------------------------------------- #

def parse_token_file(filepath: str) -> list[TokenRecord]:
    """Parse tokens-to-recover.txt into list of token records.

    Each line: timestamp | mint_url | cashuB_token | rejection_error
    """
    records: list[TokenRecord] = []
    text = Path(filepath).read_text()
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) < 3:
            continue
        records.append(TokenRecord(
            timestamp=parts[0],
            mint_url=parts[1],
            token=parts[2],
            error=parts[3] if len(parts) > 3 else "",
        ))
    return records


# --------------------------------------------------------------------------- #
# Token decoding
# --------------------------------------------------------------------------- #

def decode_cashu_token(token: str) -> dict:
    """Decode a Cashu V3 (cashuB...) or V4 (cashuA...) token.

    Returns the decoded JSON structure with proofs/token/mint info.

    Raises ValueError on unknown prefixes.
    """
    import zlib

    token = token.strip()
    low = token.lower()

    if low.startswith(TOKEN_PREFIX_A.lower()):
        raw = token[len(TOKEN_PREFIX_A):]
        prefix = TOKEN_PREFIX_A
        encoder = "json"
    elif low.startswith(TOKEN_PREFIX_B.lower()):
        raw = token[len(TOKEN_PREFIX_B):]
        prefix = TOKEN_PREFIX_B
        encoder = "cbor"
    else:
        raise ValueError(f"unknown token prefix: {token[:20]}...")

    # base64url decode
    padded = raw + "=" * (4 - len(raw) % 4)
    data = base64.urlsafe_b64decode(padded)

    # Try decompress (Cashu V4 uses gzip)
    try:
        data = zlib.decompress(data, wbits=zlib.MAX_WBITS | 16)
    except Exception:
        pass  # not compressed

    if encoder == "json":
        return json.loads(data)
    else:
        # CBOR decode
        try:
            import cbor2
            return cbor2.loads(data)
        except ImportError:
            # Fallback: try treating as JSON after decompress
            try:
                return json.loads(data)
            except Exception:
                raise ValueError("Cannot decode CBOR token without cbor2 library")


_CBOR_KEY_MAP = {"a": "amount", "s": "secret", "c": "C", "id": "id"}


def _normalize_proof(p: dict) -> dict:
    """Normalize CBOR short keys to long names (a→amount, s→secret, c→C)."""
    if not isinstance(p, dict):
        return p
    # If already has long keys, return as-is
    if "amount" in p or "secret" in p:
        return p
    # Map short keys to long
    result = {}
    for k, v in p.items():
        result[_CBOR_KEY_MAP.get(k, k)] = v
    return result


def extract_proofs(decoded: Any) -> list[dict]:
    """Extract proof list from decoded token structure.

    Handles V3 CBOR format: {"t": [{"i": keyset_id, "p": [...]}], "m": mint_url, "u": "sat"}
    Handles V3 JSON format: {"token": [{"mint": ..., "proofs": [...]}], "unit": "sat"}
    Handles V4 JSON format: {"token": {"mint": ..., "proofs": [...]}, ...}

    CBOR proofs use short keys (a, s, c, id); these are normalized to
    long names (amount, secret, C, id) for consistent access.
    """
    # CBOR short-key format: {"t": [...], "m": ..., "u": ...}
    if isinstance(decoded, dict):
        if "t" in decoded:
            token_list = decoded["t"]
            if isinstance(token_list, list):
                proofs = []
                for entry in token_list:
                    proofs.extend(_normalize_proof(p) for p in entry.get("p", []))
                return proofs
        # JSON nested token
        token_obj = decoded.get("token", decoded)
        if isinstance(token_obj, dict):
            return token_obj.get("proofs", [])
        elif isinstance(token_obj, list):
            proofs = []
            for entry in token_obj:
                proofs.extend(entry.get("proofs", []))
            return proofs
    elif isinstance(decoded, list):
        return decoded
    return []


def extract_mint_url(decoded: Any) -> str:
    """Extract mint URL from decoded token if present."""
    if isinstance(decoded, dict):
        token_obj = decoded.get("token", decoded)
        if isinstance(token_obj, dict):
            return token_obj.get("mint", "")
        elif isinstance(token_obj, list) and token_obj:
            return token_obj[0].get("mint", "")
    return ""


# --------------------------------------------------------------------------- #
# Proof Y-value computation (NUT-07)
# --------------------------------------------------------------------------- #

def compute_proof_y(proof: dict) -> str:
    """Compute the Y (public key) value for a proof's secret.

    Y = PK(secret) where PK is the secp256k1 public key of the secret bytes.
    Returns a 66-char hex string (33-byte compressed pubkey).
    """
    # If proof already has a Y or C field, return that
    if "Y" in proof:
        y = proof["Y"]
        return y.hex() if isinstance(y, (bytes, bytearray)) else str(y)
    if "c" in proof:
        c = proof["c"]
        return c.hex() if isinstance(c, (bytes, bytearray)) else str(c)
    if "C" in proof:
        c = proof["C"]
        return c.hex() if isinstance(c, (bytes, bytearray)) else str(c)

    secret = proof.get("secret") or proof.get("s")
    if secret is None:
        return ""

    if isinstance(secret, (bytes, bytearray)):
        secret_bytes = bytes(secret)
    else:
        # hex string or utf-8
        s = str(secret)
        try:
            secret_bytes = bytes.fromhex(s)
        except (ValueError, AttributeError):
            secret_bytes = s.encode("utf-8")

    # Hash to 32 bytes (required for secp256k1 secret key)
    x = hashlib.sha256(secret_bytes).digest()
    try:
        import coincurve
        pk = coincurve.PublicKey.from_secret(x)
        return pk.format().hex()
    except ImportError:
        # Fallback to ecdsa
        import os
        from ecdsa import SECP256k1, SigningKey
        sk = SigningKey.from_string(x, curve=SECP256k1)
        vk = sk.get_verifying_key()
        return vk.to_string("compressed").hex()


def build_checkstate_request(proofs: list[dict]) -> dict:
    """Build the NUT-07 checkstate request payload.

    Returns {"Ys": [y_hex, ...]} — a top-level list of Y hex strings per NUT-07 spec.
    """
    ys = [compute_proof_y(p) for p in proofs]
    ys = [y for y in ys if y]  # filter empties
    return {"Ys": ys}


# --------------------------------------------------------------------------- #
# Checkstate (network)
# --------------------------------------------------------------------------- #

def check_token_state(mint_url: str, token: str, timeout: int = 10) -> dict:
    """Check if token's proofs are spent at the mint (NUT-07 checkstate).

    Returns dict with 'unspent_count', 'spent_count', 'total_proofs'.
    """
    decoded = decode_cashu_token(token)
    proofs = extract_proofs(decoded)

    if not proofs:
        return {"unspent_count": 0, "spent_count": 0, "total_proofs": 0, "error": "no proofs found"}

    payload = build_checkstate_request(proofs)

    resp = requests.post(
        f"{mint_url}{CHECKSTATE_PATH}",
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()

    states = data.get("states", [])
    unspent = sum(1 for s in states if s.get("state") == "UNSPENT")
    spent = sum(1 for s in states if s.get("state") == "SPENT")

    return {
        "unspent_count": unspent,
        "spent_count": spent,
        "total_proofs": len(states),
    }


def submit_token_to_router(router_ip: str, token: str, timeout: int = 30):
    """Re-submit a Cashu token to the router's API for processing.

    The router API on :2121 accepts the raw token string as POST body
    (not JSON-wrapped) — same format the captive portal uses.
    """
    resp = requests.post(
        f"http://{router_ip}:2121/",
        data=token,
        headers={"Content-Type": "text/plain"},
        timeout=timeout,
    )
    return resp.status_code, resp.text


def get_token_amount(decoded: Any) -> int:
    """Sum all proof amounts from decoded token.

    Handles CBOR short keys (a=amount) and JSON keys (amount=amount).
    """
    proofs = extract_proofs(decoded)
    return sum(p.get("a", p.get("amount", 0)) for p in proofs)


# --------------------------------------------------------------------------- #
# process_tokens — orchestration
# --------------------------------------------------------------------------- #

def process_tokens(
    filepath: str,
    mode: str = "check",
    router_ip: str = "192.168.8.1",
    delay: float = 1.0,
) -> list[ResultRecord]:
    """Process all tokens in the file.

    Modes:
      "check"   — check state only, no submission
      "recover" — check state, submit unspent tokens to router
      "dry-run" — parse + show state, no network submission (state still checked)

    Returns a list of ResultRecord.
    """
    records = parse_token_file(filepath)
    results: list[ResultRecord] = []

    for record in records:
        decoded = None
        amount = 0
        try:
            decoded = decode_cashu_token(record.token)
            amount = get_token_amount(decoded)
        except Exception:
            amount = 0

        result = ResultRecord(
            timestamp=record.timestamp,
            mint_url=record.mint_url,
            amount=amount,
        )

        if mode == "dry-run":
            result.state = "UNSPENT" if amount > 0 else "SPENT"
            result.action = ACTION_DRY_RUN
            results.append(result)
            if delay and len(results) < len(records):
                time.sleep(delay)
            continue

        # Check state at mint
        try:
            raw_state = check_token_state(record.mint_url, record.token)
            # check_token_state may return either {"states": [...]} (raw)
            # or {"unspent_count": ..., "spent_count": ...} (processed)
            if "states" in raw_state:
                states_list = raw_state["states"]
                unspent = sum(1 for s in states_list if s.get("state") == "UNSPENT")
            else:
                unspent = raw_state.get("unspent_count", 0)
            result.state = "UNSPENT" if unspent > 0 else "SPENT"
        except Exception as e:
            result.state = "ERROR"
            result.action = ACTION_CHECK_FAILED
            result.error = str(e)
            results.append(result)
            if delay and len(results) < len(records):
                time.sleep(delay)
            continue

        if mode == "check":
            result.action = ACTION_CHECK_ONLY
        elif mode == "recover":
            if result.state == "UNSPENT":
                try:
                    status, body = submit_token_to_router(router_ip, record.token)
                    result.action = ACTION_SUBMITTED
                    result.submit_status = status
                    result.submit_body = body
                except Exception as e:
                    result.action = ACTION_SUBMITTED
                    result.submit_status = -1
                    result.error = str(e)
            else:
                result.action = ACTION_SKIPPED_SPENT

        results.append(result)
        if delay and len(results) < len(records):
            time.sleep(delay)

    return results


def summarize_results(results: list[ResultRecord]) -> dict:
    """Summarize processing results into counts."""
    summary = {
        "total": len(results),
        "unspent": 0,
        "spent": 0,
        "errors": 0,
        "submitted": 0,
        "skipped": 0,
    }
    for r in results:
        if r.state == "UNSPENT":
            summary["unspent"] += 1
        elif r.state == "SPENT":
            summary["spent"] += 1
        elif r.state == "ERROR":
            summary["errors"] += 1
        if r.action == ACTION_SUBMITTED:
            summary["submitted"] += 1
        elif r.action == ACTION_SKIPPED_SPENT:
            summary["skipped"] += 1
    return summary


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(
        description="Recover stuck Cashu tokens from tokens-to-recover.txt"
    )
    parser.add_argument("--file", required=True, help="Path to tokens-to-recover.txt")
    parser.add_argument("--router", default="192.168.8.1", help="Router IP (default: 192.168.8.1)")
    parser.add_argument("--check", action="store_true", help="Check token states at mint only")
    parser.add_argument("--recover", action="store_true", help="Re-submit unspent tokens to router")
    parser.add_argument("--dry-run", action="store_true", help="Parse and show what would happen")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between tokens in seconds")
    args = parser.parse_args()

    mode = "dry-run" if args.dry_run else ("recover" if args.recover else ("check" if args.check else None))
    if mode is None:
        parser.print_help()
        sys.exit(1)

    records = parse_token_file(args.file)
    print(f"Found {len(records)} tokens to process\n")

    results = process_tokens(args.file, mode=mode, router_ip=args.router, delay=args.delay)

    for i, r in enumerate(results):
        print(f"[{i+1}/{len(results)}] {r.timestamp}")
        print(f"  Mint: {r.mint_url}")
        print(f"  Amount: {r.amount} sats")
        print(f"  State: {r.state}")
        print(f"  Action: {r.action}")
        if r.submit_status is not None:
            print(f"  Submit: HTTP {r.submit_status}")
        if r.error:
            print(f"  Error: {r.error}")
        print()

    summary = summarize_results(results)
    print("=" * 50)
    print("SUMMARY")
    print(f"  Total tokens:  {summary['total']}")
    print(f"  Unspent:      {summary['unspent']}")
    print(f"  Spent (skip): {summary['spent']}")
    print(f"  Submitted:    {summary['submitted']}")
    print(f"  Skipped:      {summary['skipped']}")
    print(f"  Errors:       {summary['errors']}")
    print("=" * 50)

    # Write detailed results
    results_path = Path(args.file).parent / "recovery-results.json"
    Path(results_path).write_text(json.dumps(results, indent=2, default=str))
    print(f"Detailed results: {results_path}")


if __name__ == "__main__":
    main()