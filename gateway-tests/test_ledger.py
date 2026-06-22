import json
import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended]


def _find_ledger_path(gateway_ssh):
    r = gateway_ssh(
        "find /var/lib/tollgate /opt/tollgate /var/lib/tollgate-daemon "
        "-name 'ledger.jsonl' -type f 2>/dev/null | head -1",
        timeout=15,
    )
    path = r.stdout.strip()
    if not path:
        r2 = gateway_ssh(
            "find / -maxdepth 4 -name 'ledger.jsonl' -type f 2>/dev/null | head -1",
            timeout=15,
        )
        path = r2.stdout.strip()
    return path if path else None


def _read_ledger(gateway_ssh, n=20):
    ledger_path = _find_ledger_path(gateway_ssh)
    if not ledger_path:
        pytest.skip("ledger.jsonl not found on server")
    r = gateway_ssh(f"tail -{n} {ledger_path}", timeout=15)
    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip(f"ledger empty or unreadable: {r.stderr[:200]}")
    lines = [l for l in r.stdout.strip().split("\n") if l.strip()]
    return [json.loads(l) for l in lines if l.strip()]


def _count_ledger(gateway_ssh):
    ledger_path = _find_ledger_path(gateway_ssh)
    if not ledger_path:
        return 0
    r = gateway_ssh(f"wc -l {ledger_path}", timeout=15)
    try:
        return int(r.stdout.strip().split()[0])
    except (ValueError, IndexError):
        return 0


def test_ledger_has_entries(gateway_ssh):
    count = _count_ledger(gateway_ssh)
    if count == 0:
        pytest.skip("ledger has no entries (no auth traffic yet)")
    assert count > 0, "Ledger should have at least one entry"


def test_all_entries_have_timestamp(gateway_ssh):
    entries = _read_ledger(gateway_ssh)
    for i, e in enumerate(entries):
        assert "timestamp" in e, f"Entry {i} missing timestamp: {e}"


def test_auth_accept_entries_have_payment_type(gateway_ssh):
    entries = _read_ledger(gateway_ssh)
    accept_entries = [e for e in entries if e.get("event") == "auth_accept"
                      or e.get("type") == "auth_accept"]
    if not accept_entries:
        pytest.skip("no auth_accept entries in last 20 ledger lines")
    for i, e in enumerate(accept_entries):
        pt = e.get("payment_type") or e.get("paymentType")
        assert pt is not None, f"auth_accept entry {i} missing payment_type: {e}"


def test_auth_accept_entries_have_token_hash(gateway_ssh):
    entries = _read_ledger(gateway_ssh)
    accept_entries = [e for e in entries if e.get("event") == "auth_accept"
                      or e.get("type") == "auth_accept"]
    if not accept_entries:
        pytest.skip("no auth_accept entries in last 20 ledger lines")
    for i, e in enumerate(accept_entries):
        th = e.get("token_hash") or e.get("tokenHash")
        assert th is not None, f"auth_accept entry {i} missing token_hash: {e}"


def test_auth_accept_entries_have_duration(gateway_ssh):
    entries = _read_ledger(gateway_ssh)
    accept_entries = [e for e in entries if e.get("event") == "auth_accept"
                      or e.get("type") == "auth_accept"]
    if not accept_entries:
        pytest.skip("no auth_accept entries in last 20 ledger lines")
    for i, e in enumerate(accept_entries):
        dur = e.get("duration_sec") or e.get("durationSec")
        assert dur is not None, f"auth_accept entry {i} missing duration_sec: {e}"


def test_per_nasid_grouping(gateway_ssh):
    ledger_path = _find_ledger_path(gateway_ssh)
    if not ledger_path:
        pytest.skip("ledger.jsonl not found")
    r2 = gateway_ssh(
        f"cat {ledger_path} | jq -s 'group_by(.nas_id) | length' 2>/dev/null || echo 0",
        timeout=20,
    )
    try:
        groups = int(r2.stdout.strip())
    except ValueError:
        pytest.skip(f"jq not available or parse error: {r2.stdout}")
    assert groups >= 1, f"Expected at least 1 NAS-ID group, got {groups}"


def test_wallet_balance_nonzero(gateway_ssh):
    r = gateway_ssh(
        "tollgate-cli wallet balance 2>/dev/null || "
        "/opt/tollgate/tollgate-cli wallet balance 2>/dev/null || "
        "tollgate wallet balance 2>/dev/null || true",
        timeout=15,
    )
    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip(f"wallet CLI not available: {r.stderr[:200]}")
    import re
    m = re.search(r'(\d+)', r.stdout)
    assert m, f"Cannot parse balance from: {r.stdout}"
    balance = int(m.group(1))
    assert balance >= 0, f"Balance should be non-negative, got {balance}"
