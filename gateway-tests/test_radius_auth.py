import time
import pytest

pytestmark = [pytest.mark.api, pytest.mark.critical]


def _lnurlw_code():
    ts = int(time.time() * 1000)
    return f"lnurlwDP3qXmTest{ts}"


def _skip_no_radclient(r):
    if r.returncode == -1 and "not found" in r.stderr:
        pytest.skip("radclient not installed locally")


def test_lnurlw_in_username_accepted(radtest, unique_mac):
    code = _lnurlw_code()
    r = radtest(code, "", mac=unique_mac)
    _skip_no_radclient(r)
    assert "Access-Accept" in r.stdout or "Response-Packet-Type = Access-Accept" in r.stdout, \
        f"Expected Access-Accept for valid LNURLw code: {r.stdout}"


def test_lnurlw_replay_rejected(radtest, unique_mac):
    code = _lnurlw_code()
    r1 = radtest(code, "", mac=unique_mac)
    _skip_no_radclient(r1)
    assert "Access-Accept" in r1.stdout or "Response-Packet-Type = Access-Accept" in r1.stdout, \
        f"First auth should Accept: {r1.stdout}"
    r2 = radtest(code, "", mac=unique_mac)
    assert "Access-Reject" in r2.stdout or "Response-Packet-Type = Access-Reject" in r2.stdout, \
        f"Replay should Reject: {r2.stdout}"


def test_reconnect_with_new_code_same_mac(radtest, unique_mac):
    code1 = _lnurlw_code()
    r1 = radtest(code1, "", mac=unique_mac)
    _skip_no_radclient(r1)
    assert "Access-Accept" in r1.stdout or "Response-Packet-Type = Access-Accept" in r1.stdout, \
        f"First auth should Accept: {r1.stdout}"
    code2 = _lnurlw_code()
    r2 = radtest(code2, "", mac=unique_mac)
    assert "Access-Accept" in r2.stdout or "Response-Packet-Type = Access-Accept" in r2.stdout, \
        f"Reconnect with new code should Accept: {r2.stdout}"


def test_lnurlw_in_password_accepted(radtest, unique_mac):
    code = _lnurlw_code()
    r = radtest("tollgate", code, mac=unique_mac)
    _skip_no_radclient(r)
    assert "Access-Accept" in r.stdout or "Response-Packet-Type = Access-Accept" in r.stdout, \
        f"LNURLw in password should Accept: {r.stdout}"


def test_invalid_credentials_rejected(radtest, unique_mac):
    r = radtest("invalid_user_xyz", "invalid_pass_xyz", mac=unique_mac)
    _skip_no_radclient(r)
    assert "Access-Reject" in r.stdout or "Response-Packet-Type = Access-Reject" in r.stdout, \
        f"Invalid credentials should Reject: {r.stdout}"


def test_lnurlw_with_nasid_accepted(radtest, unique_mac):
    code = _lnurlw_code()
    test_npub = "npub1test0000000000000000000000000000000000000000000000000000dead"
    r = radtest(code, "", mac=unique_mac, nas_id=test_npub)
    _skip_no_radclient(r)
    assert "Access-Accept" in r.stdout or "Response-Packet-Type = Access-Accept" in r.stdout, \
        f"LNURLw with NAS-ID should Accept: {r.stdout}"
