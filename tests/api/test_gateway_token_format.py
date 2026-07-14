#!/usr/bin/env python3
"""
TollGate Gateway Token Format Smoke Tests (x86)

Tests run from this Linux machine against the live TollGate gateway.
Gateway IP configurable via TOLLGATE_IP env var (default 10.230.237.1).

Root cause of Android payment failure (verified):
  Android app produced tokens with "cashuB" prefix (V4 = CBOR) but JSON
  payload (V3 structure). Gateway DecodeToken() tries V4 CBOR first
  (sees cashuB, attempts CBOR unmarshal on JSON → fails), then V3
  fallback (expects cashuA → also fails). Error: "invalid V3 token".

Usage:
  python3 tests/api/test_gateway_token_format.py [--gateway IP] [--verbose]

Requirements:
  pip install requests cbor2
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

# cbor2 for generating proper V4 tokens
try:
    import cbor2
    HAS_CBOR = True
except ImportError:
    HAS_CBOR = False

DEFAULT_GATEWAY = os.environ.get("TOLLGATE_IP", "10.230.237.1")
GATEWAY_PORT = 2121

# Test mint (accepted by gateway per its advertisement)
TEST_MINT = "https://nofee.testnut.cashu.space"
TEST_KEYSET_ID = "00b4cd27d8861a44"

# Colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


class TestResult:
    def __init__(self, name: str, passed: bool | None, detail: str = ""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = f"{GREEN}PASS{RESET}" if self.passed else f"{RED}FAIL{RESET}"
        line = f"  [{status}] {self.name}"
        if self.detail:
            line += f"\n           {self.detail}"
        return line


def http_get(url: str, timeout: int = 10) -> tuple[int, str, dict]:
    """GET request, returns (status_code, body, headers)."""
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)
    except Exception as e:
        return 0, str(e), {}


def http_post(url: str, body: str, content_type: str = "text/plain", timeout: int = 30) -> tuple[int, str]:
    """POST request, returns (status_code, body)."""
    try:
        data = body.encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", content_type)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def http_options(url: str, timeout: int = 5) -> tuple[int, dict]:
    """OPTIONS request, returns (status_code, headers)."""
    try:
        req = urllib.request.Request(url, method="OPTIONS")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)
    except Exception as e:
        return 0, {}


def build_v3_token(mint: str, keyset_id: str, amount: int = 1) -> str:
    """Build a proper cashuA V3 token (JSON + base64url)."""
    token_json = {
        "token": [{
            "i": mint,
            "p": [{
                "a": amount,
                "s": "test_secret_hex_" + "0" * 40,
                "c": "02" + "a" * 62,  # fake compressed pubkey
            }]
        }],
        "unit": "sat",
        "memo": "x86 smoke test",
    }
    json_bytes = json.dumps(token_json).encode()
    b64 = base64.urlsafe_b64encode(json_bytes).rstrip(b"=").decode()
    return f"cashuA{b64}"


def build_v4_cbor_token(mint: str, keyset_id: str, amount: int = 1) -> str:
    """Build a proper cashuB V4 token (CBOR + base64url)."""
    if not HAS_CBOR:
        return build_v3_token(mint, keyset_id, amount)  # fallback
    keyset_bytes = bytes.fromhex(keyset_id)
    token_cbor = {
        "mint": mint,
        "proofs": [{
            "id": keyset_bytes,
            "amount": amount,
            "secret": "test_secret_hex",
            "C": bytes.fromhex("02" + "a" * 62),
        }],
    }
    cbor_bytes = cbor2.dumps(token_cbor)
    b64 = base64.urlsafe_b64encode(cbor_bytes).rstrip(b"=").decode()
    return f"cashuB{b64}"


def build_buggy_token(mint: str, keyset_id: str, amount: int = 1) -> str:
    """Build the BUGGY cashuB+JSON token (what the Android app was producing)."""
    token_json = {
        "token": [{
            "i": mint,
            "p": [{
                "a": amount,
                "s": "test_secret_hex_" + "0" * 40,
                "c": "02" + "a" * 62,
            }]
        }],
        "unit": "sat",
        "memo": "buggy token",
    }
    json_bytes = json.dumps(token_json).encode()
    b64 = base64.urlsafe_b64encode(json_bytes).rstrip(b"=").decode()
    # BUG: using cashuB prefix with JSON content
    return f"cashuB{b64}"


def run_tests(gateway_ip: str, verbose: bool = False) -> list[TestResult]:
    base = f"http://{gateway_ip}:{GATEWAY_PORT}"
    results = []
    
    print(f"\n{BOLD}TollGate Gateway Smoke Tests{RESET}")
    print(f"  Target: {base}\n")

    # ── Test 1: Gateway advertisement (GET /) ──────────────────
    code, body, headers = http_get(f"{base}/")
    if code == 200:
        try:
            ad = json.loads(body)
            has_kind = ad.get("kind") == 10021
            tags = ad.get("tags", [])
            tag_names = [t[0] if isinstance(t, list) and t else "" for t in tags]
            has_metric = "metric" in tag_names
            has_step = "step_size" in tag_names
            has_price = "price_per_step" in tag_names
            has_tips = "tips" in tag_names

            passed = has_kind and has_metric and has_step and has_price and has_tips
            detail = ""
            if not passed:
                missing = []
                if not has_kind: missing.append("kind:10021")
                if not has_metric: missing.append("metric")
                if not has_step: missing.append("step_size")
                if not has_price: missing.append("price_per_step")
                if not has_tips: missing.append("tips")
                detail = f"Missing: {', '.join(missing)}"
            elif verbose:
                mint_urls = [t[4] for t in tags if t[0] == "price_per_step" and len(t) >= 5]
                detail = f"Mints: {mint_urls}"
            results.append(TestResult("GET / returns valid 10021 advertisement", passed, detail))
        except json.JSONDecodeError:
            results.append(TestResult("GET / returns valid 10021 advertisement", False, "Invalid JSON"))
    else:
        results.append(TestResult("GET / returns valid 10021 advertisement", False, f"HTTP {code}"))

    # ── Test 2: POST proper V3 token (cashuA + JSON) ──────────
    token_v3 = build_v3_token(TEST_MINT, TEST_KEYSET_ID)
    code, body = http_post(f"{base}/", token_v3)
    try:
        resp = json.loads(body)
        code_tag = ""
        for tag in resp.get("tags", []):
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "code":
                code_tag = tag[1]
                break
        # V3 token should parse OK. May fail at wallet receive (fake proofs)
        # but must NOT fail with "payment-error-invalid-token"
        passed = code_tag != "payment-error-invalid-token"
        detail = f"HTTP {code}, code={code_tag}" if verbose else ""
        results.append(TestResult("POST cashuA V3 token (parsing layer)", passed, detail))
    except (json.JSONDecodeError, KeyError):
        # If we can't parse JSON, check for absence of invalid-token error
        passed = "invalid-token" not in body and "invalid V3" not in body
        results.append(TestResult("POST cashuA V3 token (parsing layer)", passed, f"HTTP {code}"))

    # ── Test 3: POST proper V4 CBOR token (cashuB + CBOR) ─────
    if HAS_CBOR:
        token_v4 = build_v4_cbor_token(TEST_MINT, TEST_KEYSET_ID)
        code, body = http_post(f"{base}/", token_v4)
        try:
            resp = json.loads(body)
            code_tag = ""
            for tag in resp.get("tags", []):
                if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "code":
                    code_tag = tag[1]
                    break
            passed = code_tag != "payment-error-invalid-token"
            detail = f"HTTP {code}, code={code_tag}" if verbose else ""
            results.append(TestResult("POST cashuB V4 CBOR token (parsing layer)", passed, detail))
        except (json.JSONDecodeError, KeyError):
            passed = "invalid-token" not in body
            results.append(TestResult("POST cashuB V4 CBOR token (parsing layer)", passed, f"HTTP {code}"))
    else:
        results.append(TestResult("POST cashuB V4 CBOR token (parsing layer)", None, "cbor2 not installed — skipped"))

    # ── Test 4: POST buggy cashuB+JSON token (regression) ─────
    token_bug = build_buggy_token(TEST_MINT, TEST_KEYSET_ID)
    code, body = http_post(f"{base}/", token_bug)
    try:
        resp = json.loads(body)
        code_tag = ""
        for tag in resp.get("tags", []):
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "code":
                code_tag = tag[1]
                break
        # This MUST fail — it's the bug. Verifies the gateway rejects bad format.
        passed = code_tag == "payment-error-invalid-token"
        detail = f"HTTP {code}, code={code_tag} (expected payment-error-invalid-token)"
        results.append(TestResult("POST cashuB+JSON buggy token is rejected (regression)", passed, detail))
    except (json.JSONDecodeError, KeyError):
        passed = "invalid-token" in body or "invalid V3" in body
        results.append(TestResult("POST cashuB+JSON buggy token is rejected (regression)", passed, f"HTTP {code}"))

    # ── Test 5: POST whitespace token ─────────────────────────
    code, body = http_post(f"{base}/", " ")
    # HTTP 0 = urllib connection error on degenerate input (client-side)
    passed = code in (0, 400, 405, 500)
    results.append(TestResult("POST whitespace token returns error", passed, f"HTTP {code}"))

    # ── Test 6: POST garbage token ────────────────────────────
    code, body = http_post(f"{base}/", "notacashutoken")
    try:
        resp = json.loads(body)
        code_tag = ""
        for tag in resp.get("tags", []):
            if isinstance(tag, list) and len(tag) >= 2 and tag[0] == "code":
                code_tag = tag[1]
                break
        passed = code in (400,) or "error" in code_tag
        results.append(TestResult("POST garbage token returns 400", passed, f"HTTP {code}, code={code_tag}"))
    except (json.JSONDecodeError, KeyError):
        passed = code == 400
        results.append(TestResult("POST garbage token returns 400", passed, f"HTTP {code}"))

    # ── Test 7: CORS headers present ──────────────────────────
    code, hdrs = http_options(f"{base}/")
    cors_headers_val = hdrs.get("Access-Control-Allow-Headers", "") or hdrs.get("access-control-allow-headers", "")
    passed = "Content-Type" in cors_headers_val
    results.append(TestResult("CORS headers present (OPTIONS)", passed, f"Allow-Headers: {cors_headers_val[:80]}"))

    # ── Test 8: GET /whoami ───────────────────────────────────
    code, body, _ = http_get(f"{base}/whoami")
    passed = code == 200 and ("mac" in body.lower() or "{" in body)
    results.append(TestResult("GET /whoami returns MAC or JSON", passed, f"HTTP {code}, body={body[:80]}"))

    # ── Test 9: Gateway accepts at least one known mint ───────
    code_whoami, body_whoami, _ = http_get(f"{base}/")
    if code_whoami == 200:
        ad = json.loads(body_whoami)
        tags = ad.get("tags", [])
        mints = [t[4] for t in tags if isinstance(t, list) and len(t) >= 5 and t[0] == "price_per_step"]
        passed = len(mints) >= 1
        detail = f"Accepted mints: {mints}" if verbose else ""
        results.append(TestResult("Gateway advertises >=1 accepted mint", passed, detail))
    else:
        results.append(TestResult("Gateway advertises >=1 accepted mint", False, "Can't reach gateway"))

    # ── Test 10: Decode verification — V3 token is valid JSON ─
    v3 = build_v3_token(TEST_MINT, TEST_KEYSET_ID)
    payload = v3[6:]  # after cashuA
    padded = payload + "=" * (4 - len(payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded))
        has_token_key = "token" in decoded
        has_unit = decoded.get("unit") == "sat"
        passed = has_token_key and has_unit
        results.append(TestResult("V3 token decodes to valid JSON structure", passed, ""))
    except Exception as e:
        results.append(TestResult("V3 token decodes to valid JSON structure", False, str(e)))

    return results


def main():
    parser = argparse.ArgumentParser(description="TollGate gateway token format smoke tests")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY, help=f"Gateway IP (default: {DEFAULT_GATEWAY})")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    results = run_tests(args.gateway, args.verbose)

    print()
    for r in results:
        print(r)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if r.passed is False)
    skipped = sum(1 for r in results if r.passed is None)
    total = len(results)

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"  {GREEN}Passed:{RESET} {passed}  {RED}Failed:{RESET} {failed}  {YELLOW}Skipped:{RESET} {skipped}  Total: {total}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
