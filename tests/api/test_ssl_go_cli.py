"""Tests for PR #123: Go-native SSL management CLI.

These tests migrate the self-signed SSL coverage from `mint-health/Makefile`
to pytest while leaving the Makefile targets available as legacy/reference
manual commands. They verify the PR #123 API: `tollgate ssl apply/remove/status`,
not the old PR #117 `tollgate-setup-ssl` shell script.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.api,
    pytest.mark.extended,
    pytest.mark.config,
    pytest.mark.destructive,
    pytest.mark.pr(123),
]

SSL_DIR = "/etc/tollgate/ssl"
SSL_CERT = f"{SSL_DIR}/server.crt"
SSL_KEY = f"{SSL_DIR}/server.key"
SSL_BACKUP = f"{SSL_DIR}/backup"
UHTTPD_CERT = "/etc/uhttpd.crt"
UHTTPD_KEY = "/etc/uhttpd.key"


def _ssh_bool(router, cmd: str) -> bool:
    return router.ssh(f"{cmd} >/dev/null 2>&1 && echo YES || echo NO").strip() == "YES"


def _skip_if_no_pr123_ssl(router):
    out = router.ssh("tollgate ssl status 2>&1 || true")
    if "Unknown" in out or "not found" in out or "Usage" not in out and "SSL" not in out:
        pytest.skip("PR #123 Go SSL CLI not installed")


def _remove_ssl_force(router):
    router.ssh("tollgate ssl remove --yes >/tmp/tollgate-ssl-remove.log 2>&1 || true", timeout=60)
    router.ssh(
        f"rm -rf {SSL_CERT} {SSL_KEY} {SSL_BACKUP} "
        "/tmp/tollgate-test-cert.pem /tmp/tollgate-test-key.pem 2>/dev/null || true"
    )


def _apply_self_signed(router):
    out = router.ssh("tollgate ssl apply --yes 2>&1", timeout=90)
    assert "error" not in out.lower(), out
    assert _ssh_bool(router, f"test -f {SSL_CERT} && test -f {SSL_KEY}"), out
    return out


def _remote_file_mode(router, path: str) -> str:
    return router.ssh(f"ls -l {path} 2>/dev/null | awk '{{print $1}}'").strip()


@pytest.fixture(autouse=True)
def ssl_clean_state(router):
    _skip_if_no_pr123_ssl(router)
    _remove_ssl_force(router)
    yield
    _remove_ssl_force(router)


def test_ssl_go_cli_initial_clean_state(router):
    assert not _ssh_bool(router, f"test -f {SSL_CERT}"), "TollGate SSL cert should not exist initially"
    assert not _ssh_bool(router, f"test -d {SSL_BACKUP}"), "SSL backup should not exist initially"
    cert_uci = router.ssh("uci -q get uhttpd.main.cert 2>/dev/null || true").strip()
    assert cert_uci != SSL_CERT, f"uhttpd should not point to TollGate SSL cert initially: {cert_uci}"
    nds = router.ssh("uci -q get nodogsplash.@nodogsplash[0].users_to_router 2>/dev/null || true")
    assert "allow tcp port 443" not in nds, f"NDS should not allow 443 initially: {nds}"


def test_ssl_go_cli_status_reports_unconfigured(router):
    status = router.ssh("tollgate ssl status 2>&1")
    assert "SSL" in status
    assert "not configured" in status.lower(), status
    assert "tollgate ssl apply" in status, status


def test_ssl_go_cli_apply_self_signed_configures_uhttpd(router):
    _apply_self_signed(router)
    assert router.ssh("uci -q get uhttpd.main.cert").strip() == SSL_CERT
    assert router.ssh("uci -q get uhttpd.main.key").strip() == SSL_KEY
    listen_https = router.ssh("uci -q get uhttpd.main.listen_https").strip()
    assert "443" in listen_https, listen_https
    assert _ssh_bool(router, "netstat -tlnp 2>/dev/null | grep ':443'"), "uhttpd should listen on 443"


def test_ssl_go_cli_apply_self_signed_creates_backup_and_mode(router):
    _apply_self_signed(router)
    assert _ssh_bool(router, f"test -d {SSL_BACKUP}"), "SSL backup directory missing"
    mode = router.ssh(f"cat {SSL_BACKUP}/ssl.mode 2>/dev/null || true").strip()
    assert mode == "self-signed", f"Expected self-signed mode backup, got: {mode}"


def test_ssl_go_cli_generated_cert_has_expected_cn_san_and_key_permissions(router):
    _apply_self_signed(router)
    hostname = router.ssh("uci -q get system.@system[0].hostname").strip()
    expected_domain = f"{hostname}.lan"

    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = Path(tmpdir) / "server.crt"
        cert_path.write_text(router.ssh(f"cat {SSL_CERT}"))
        subject = subprocess.run(
            ["openssl", "x509", "-noout", "-subject", "-in", str(cert_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        san = subprocess.run(
            ["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        subprocess.run(
            ["openssl", "x509", "-checkend", "0", "-in", str(cert_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    assert f"CN = {expected_domain}" in subject or f"CN={expected_domain}" in subject, subject
    assert f"DNS:{expected_domain}" in san, san
    assert f"DNS:{hostname}" in san, san
    key_mode = _remote_file_mode(router, SSL_KEY)
    assert key_mode == "-rw-------", f"Expected SSL key mode 600, got {key_mode}"


def test_ssl_go_cli_allows_https_through_nodogsplash_without_self_signed_dns(router):
    _apply_self_signed(router)
    nds = router.ssh("uci -q get nodogsplash.@nodogsplash[0].users_to_router 2>/dev/null || true")
    assert "allow tcp port 443" in nds, nds
    hostname = router.ssh("uci -q get system.@system[0].hostname").strip()
    domains = router.ssh("uci show dhcp 2>/dev/null | grep '=domain' || true")
    assert f"{hostname}.lan" not in domains, domains


def test_ssl_go_cli_status_reports_self_signed_after_apply(router):
    _apply_self_signed(router)
    status = router.ssh("tollgate ssl status 2>&1")
    assert "SSL" in status
    assert "self-signed" in status.lower(), status


def test_ssl_go_cli_reapply_keeps_valid_state(router):
    _apply_self_signed(router)
    out = router.ssh("tollgate ssl apply --yes 2>&1", timeout=90)
    assert "error" not in out.lower(), out
    assert _ssh_bool(router, f"test -f {SSL_CERT} && test -f {SSL_KEY}"), out
    assert router.ssh("uci -q get uhttpd.main.cert").strip() == SSL_CERT
    assert _ssh_bool(router, "netstat -tlnp 2>/dev/null | grep ':443'"), "uhttpd should still listen on 443"


def test_ssl_go_cli_remove_reverts_self_signed_state(router):
    _apply_self_signed(router)
    out = router.ssh("tollgate ssl remove --yes 2>&1", timeout=90)
    assert "error" not in out.lower(), out
    assert not _ssh_bool(router, f"test -f {SSL_CERT}"), "cert still exists after remove"
    assert not _ssh_bool(router, f"test -d {SSL_BACKUP}"), "backup still exists after remove"
    cert_uci = router.ssh("uci -q get uhttpd.main.cert 2>/dev/null || true").strip()
    assert cert_uci != SSL_CERT, f"uhttpd still points to TollGate cert: {cert_uci}"


def test_ssl_go_cli_remove_without_backup_fails_cleanly(router):
    _remove_ssl_force(router)
    out = router.ssh("tollgate ssl remove 2>&1; echo RC=$?", timeout=60)
    match = re.search(r"RC=(\d+)", out)
    assert match, out
    assert match.group(1) != "0", out
    assert "backup" in out.lower(), out


def test_ssl_go_cli_apply_real_certificate_configures_domain_mode(router):
    domain = os.environ.get("TOLLGATE_SSL_TEST_DOMAIN", "tollgate-python-test.example.com")
    key_path = "/tmp/tollgate-test-key.pem"
    cert_path = "/tmp/tollgate-test-cert.pem"
    router.ssh(
        "openssl req -x509 -newkey rsa:2048 -nodes "
        f"-keyout {key_path} -out {cert_path} -days 2 "
        f"-subj '/CN={domain}' -addext 'subjectAltName=DNS:{domain}' >/dev/null 2>&1",
        timeout=90,
    )
    if not _ssh_bool(router, f"test -s {cert_path} && test -s {key_path}"):
        pytest.skip("router openssl cannot generate local test certificate")

    out = router.ssh(f"tollgate ssl apply --yes {cert_path} {key_path} 2>&1", timeout=90)
    assert "error" not in out.lower(), out
    status = router.ssh("tollgate ssl status 2>&1")
    assert "real-cert" in status.lower(), status
    assert domain in status, status
    gateway_domain = router.ssh("uci -q get nodogsplash.@nodogsplash[0].gatewaydomainname 2>/dev/null || true").strip()
    assert gateway_domain == domain
    dhcp_domains = router.ssh("uci show dhcp 2>/dev/null | grep -E '\\.name=|\\.ip=' || true")
    assert domain in dhcp_domains, dhcp_domains
