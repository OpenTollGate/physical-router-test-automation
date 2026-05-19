import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended]


def _pending_token_cgi_available(router):
    code = router.ssh(f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 2 {router.cgi_url('tollgate-pending-token')}")
    return code.strip().strip("'") not in ("404", "000", "500", "503")


def test_pending_token_empty_when_none(router):
    if not _pending_token_cgi_available(router):
        pytest.skip("tollgate-pending-token CGI not available on this router")
    router.ssh("rm -f /tmp/tg-pending-token")
    resp = router.ssh(f"curl -s {router.cgi_url('tollgate-pending-token')}")
    assert resp.strip() == "", f"Expected empty response, got: {resp[:200]}"


def test_pending_token_returns_and_consumes(router):
    if not _pending_token_cgi_available(router):
        pytest.skip("tollgate-pending-token CGI not available on this router")
    test_token = "cashuAtest_token_pending_12345"
    router.ssh_stdin("cat > /tmp/tg-pending-token", test_token)

    resp1 = router.ssh(f"curl -s {router.cgi_url('tollgate-pending-token')}")
    assert test_token in resp1, f"First GET did not return token: {resp1[:200]}"

    resp2 = router.ssh(f"curl -s {router.cgi_url('tollgate-pending-token')}")
    assert resp2.strip() == "", \
        f"Second GET returned token — consume-on-read failed: {resp2[:200]}"


def test_pending_token_no_cache_headers(router):
    if not _pending_token_cgi_available(router):
        pytest.skip("tollgate-pending-token CGI not available on this router")
    resp = router.ssh(f"curl -sI {router.cgi_url('tollgate-pending-token')}")
    assert "no-cache" in resp.lower() or "no-store" in resp.lower(), \
        f"Missing no-cache/no-store headers: {resp[:200]}"
