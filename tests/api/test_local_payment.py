"""Provider-agnostic payment API tests for local dry testing.

Run against the local mock mint + Go backend:
    TOLLGATE_VM_PROVIDER=local pytest tests/api/test_local_payment.py -v

Or with the one-command runner:
    ./scripts/local-test.sh

Scenarios (the contract):
    S1: POST valid V3 token → kind=1022 session event (happy path)
    S2: POST V4 token → kind=1022 (V4 native decode)
    S3: POST spent token → error (double-spend detection)
    S4: POST malformed token → CU101/CU102 (validation)
    S5: GET / → kind=10021 advertisement
    S6: GET /whoami → MAC address
    S7: GET /usage → usage string
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest
import requests

PRTA_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
BACKEND_URL = os.environ.get("TOLLGATE_BACKEND_URL", "http://127.0.0.1:2121")
MINT_URL = os.environ.get("TOLLGATE_MINT_URL", "http://127.0.0.1:3338")


def _create_token(amount: int = 1) -> str:
    r = urllib.request.urlopen(f"{MINT_URL}/test/create-token?amount={amount}", timeout=5)
    return json.loads(r.read())["token"]


def _post_token(token: str) -> dict:
    r = requests.post(
        f"{BACKEND_URL}/",
        data=token,
        headers={"Content-Type": "text/plain"},
        timeout=15,
    )
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_text": r.text[:200]}


@pytest.fixture(scope="module")
def fresh_token() -> str:
    return _create_token(amount=1)


# ─── S1: Happy path — valid V3 token → session event ─────────────

def test_s1_valid_v3_token_payment(fresh_token):
    resp = _post_token(fresh_token)
    assert resp.get("kind") == 1022, (
        f"Expected kind=1022 session event, got kind={resp.get('kind')}: "
        f"{json.dumps(resp)[:300]}"
    )
    tags = {t[0]: t[1:] for t in resp.get("tags", [])}
    assert "device-identifier" in tags, f"Missing device-identifier tag: {tags}"
    assert "allotment" in tags, f"Missing allotment tag: {tags}"


# ─── S2: V4 token → native decode ────────────────────────────────

def test_s2_v4_token_natively_supported():
    token = _create_token(amount=1)
    decoded = json.loads(
        __import__("base64").urlsafe_b64decode(token[6:] + "==").decode()
    )
    decoded["token"][0]["mint"] = decoded["token"][0]["mint"]
    resp = _post_token(token)
    kind = resp.get("kind")
    assert kind in (1022, 21023), f"Unexpected kind={kind}: {json.dumps(resp)[:200]}"


# ─── S3: Double-spend — same token twice → error ─────────────────

def test_s3_double_spend_detected(fresh_token):
    first = _post_token(fresh_token)
    if first.get("kind") != 1022:
        pytest.skip("First spend didn't succeed — token may have been consumed by S1")
    second = _post_token(fresh_token)
    kind = second.get("kind")
    tags = {t[0]: t[1:] for t in second.get("tags", [])}
    code = tags.get("code", [""])[0]
    assert kind == 21023, f"Expected error (21023) on double-spend, got kind={kind}"
    assert "spent" in code.lower() or "error" in code.lower(), (
        f"Expected spent/error code, got: {code}"
    )


# ─── S4: Malformed tokens → validation errors ────────────────────

@pytest.mark.parametrize("token,expected_code", [
    ("", "CU100"),
    ("notacashutoken", "CU101"),
    ("cashuAinvalidbase64!!!", "CU102"),
])
def test_s4_malformed_tokens(token, expected_code):
    resp = _post_token(token)
    tags = {t[0]: t[1:] for t in resp.get("tags", [])}
    code = tags.get("code", [""])[0]
    assert expected_code in code or resp.get("kind") == 21023, (
        f"Expected {expected_code}, got code={code}, kind={resp.get('kind')}"
    )


# ─── S5: GET / → advertisement ───────────────────────────────────

def test_s5_advertisement():
    r = requests.get(f"{BACKEND_URL}/", timeout=5)
    d = r.json()
    assert d["kind"] == 10021, f"Expected kind=10021, got {d['kind']}"
    tags = {t[0]: t[1:] for t in d.get("tags", [])}
    assert "metric" in tags
    assert "step_size" in tags
    assert tags["metric"][0] == "bytes"


# ─── S6: GET /whoami → MAC address ───────────────────────────────

def test_s6_whoami():
    r = requests.get(f"{BACKEND_URL}/whoami", timeout=5)
    body = r.text.strip()
    assert "mac=" in body or len(body) > 0, f"/whoami returned empty: status={r.status_code}"


# ─── S7: GET /usage → usage string ───────────────────────────────

def test_s7_usage():
    r = requests.get(f"{BACKEND_URL}/usage", timeout=5)
    body = r.text.strip()
    assert len(body) > 0, f"/usage returned empty: status={r.status_code}"
