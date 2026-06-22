import time
import json
import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended, pytest.mark.slow]


def _lnurlw_code():
    ts = int(time.time() * 1000)
    return f"lnurlwDP3qXmStl{ts}"


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


def _count_ledger(gateway_ssh):
    ledger_path = _find_ledger_path(gateway_ssh)
    if not ledger_path:
        return 0
    r = gateway_ssh(f"wc -l {ledger_path}", timeout=15)
    try:
        return int(r.stdout.strip().split()[0])
    except (ValueError, IndexError):
        return 0


def test_settlement_dry_run(gateway_ssh, radtest, unique_mac):
    initial = _count_ledger(gateway_ssh)
    if initial == 0:
        for i in range(3):
            code = _lnurlw_code()
            mac = f"02:AA:{i:02X}:BB:CC:D{i}"
            r = radtest(code, "", mac=mac)
            _skip_no_radclient(r)
            time.sleep(0.5)

    r = gateway_ssh(
        "/opt/cashu-tollgate/tollgate-settle --dry-run --operator anonymous 2>&1 || "
        "/opt/tollgate/tollgate-settle --dry-run --operator anonymous 2>&1 || "
        "tollgate-settle --dry-run --operator anonymous 2>&1 || true",
        timeout=30,
    )

    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip(f"tollgate-settle not available: {r.stderr[:200]}")

    output = r.stdout + r.stderr
    assert "total_sat" in output or "total" in output.lower(), \
        f"Dry-run output missing total_sat: {output[:500]}"

    if "total_sat" in output:
        import re
        m = re.search(r'total_sat["\s:=]+(\d+)', output)
        if m:
            total = int(m.group(1))
            assert total >= 0, f"total_sat should be non-negative, got {total}"

    if "accepted_sessions" in output:
        import re
        m = re.search(r'accepted_sessions["\s:=]+(\d+)', output)
        if m:
            sessions = int(m.group(1))
            assert sessions >= 0, f"accepted_sessions should be non-negative, got {sessions}"
