import time
import json
import pytest

pytestmark = [pytest.mark.api, pytest.mark.critical]


def _lnurlw_code():
    ts = int(time.time() * 1000)
    return f"lnurlwDP3qXmNas{ts}"


def _skip_no_radclient(r):
    if r.returncode == -1 and "not found" in r.stderr:
        pytest.skip("radclient not installed locally")


def _find_ledger_path(gateway_ssh):
    r = gateway_ssh(
        "find /var/lib/tollgate /opt/tollgate /var/lib/tollgate-daemon "
        "-name 'ledger.jsonl' -type f 2>/dev/null | head -1",
        timeout=15,
    )
    return r.stdout.strip() or None


def _get_last_ledger_entry(gateway_ssh):
    ledger_path = _find_ledger_path(gateway_ssh)
    if not ledger_path:
        pytest.skip("ledger.jsonl not found on server")
    r = gateway_ssh(f"tail -1 {ledger_path}", timeout=15)
    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip(f"ledger empty: {r.stderr[:200]}")
    return json.loads(r.stdout.strip())


def test_nasid_propagated_to_ledger(radtest, gateway_ssh, unique_mac):
    code = _lnurlw_code()
    test_npub = f"npub1nasid{int(time.time()):010d}00000000000000000000000000000000"
    r = radtest(code, "", mac=unique_mac, nas_id=test_npub)
    _skip_no_radclient(r)
    assert "Access-Accept" in r.stdout, f"Auth should Accept: {r.stdout}"
    time.sleep(1)
    entry = _get_last_ledger_entry(gateway_ssh)
    nas_id = entry.get("nas_id") or entry.get("nasid") or entry.get("nas_identifier")
    assert nas_id is not None, f"Ledger entry missing nas_id: {entry}"
    assert test_npub in str(nas_id), \
        f"NAS-ID mismatch: expected {test_npub}, got {nas_id}"


def test_different_nasids_create_separate_entries(radtest, gateway_ssh, unique_mac):
    code1 = _lnurlw_code()
    npub1 = f"npub1aaa{int(time.time()):010d}00000000000000000000000000000000"
    r1 = radtest(code1, "", mac=unique_mac, nas_id=npub1)
    _skip_no_radclient(r1)
    assert "Access-Accept" in r1.stdout

    time.sleep(0.5)
    mac2 = unique_mac.replace("02:", "03:")
    code2 = _lnurlw_code()
    npub2 = f"npub1bbb{int(time.time()):010d}00000000000000000000000000000000"
    r2 = radtest(code2, "", mac=mac2, nas_id=npub2)
    assert "Access-Accept" in r2.stdout

    time.sleep(1)
    ledger_path = _find_ledger_path(gateway_ssh)
    if not ledger_path:
        pytest.skip("ledger.jsonl not found")
    r = gateway_ssh(f"tail -2 {ledger_path}", timeout=15)
    assert r.returncode == 0 and r.stdout.strip(), f"Cannot read ledger: {r.stderr}"
    lines = [l for l in r.stdout.strip().split("\n") if l.strip()]
    assert len(lines) >= 2, f"Expected >=2 entries, got {len(lines)}"
    e1 = json.loads(lines[-2])
    e2 = json.loads(lines[-1])
    n1 = e1.get("nas_id") or e1.get("nasid") or ""
    n2 = e2.get("nas_id") or e2.get("nasid") or ""
    assert n1 != n2 or npub1 in str(n1) or npub2 in str(n2), \
        f"Separate NAS-IDs should create distinguishable entries: n1={n1}, n2={n2}"


def test_ledger_entry_has_required_fields(radtest, gateway_ssh, unique_mac):
    code = _lnurlw_code()
    test_npub = f"npub1fld{int(time.time()):010d}00000000000000000000000000000000"
    r = radtest(code, "", mac=unique_mac, nas_id=test_npub)
    _skip_no_radclient(r)
    assert "Access-Accept" in r.stdout

    time.sleep(1)
    entry = _get_last_ledger_entry(gateway_ssh)
    assert "timestamp" in entry, f"Missing timestamp: {entry}"
    payment_type = entry.get("payment_type") or entry.get("type")
    assert payment_type is not None, f"Missing payment_type: {entry}"
    token_hash = entry.get("token_hash") or entry.get("tokenHash")
    assert token_hash is not None, f"Missing token_hash: {entry}"
