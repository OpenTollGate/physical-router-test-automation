"""Verify the FreedomTechFeed feed release candidate installs and runs on the
attached physical router — for BOTH package formats:

  * OpenWrt 24.x -> opkg -> ``.ipk``
  * OpenWrt 25.x -> apk  -> ``.apk``

Reproducible: the artifact is supplied via ``TOLLGATE_PACKAGE_PATH`` (see
``make verify-feed-rc``, which downloads it from the feed release with
``scripts/download-feed-release.sh`` and sha256-verifies it). This test asserts
the router's package manager matches the artifact format, installs it via the
standard harness path (``lib.deploy.deploy``), and checks version + build
identity + service health.

Usage:
    TOLLGATE_PACKAGE_PATH=/tmp/feed-rc/tollgate-wrt_0.6.0_alpha2_pre_aarch64_cortex-a53.ipk \\
    TOLLGATE_SSH_HOST=192.168.1.1 TOLLGATE_SSH_PASSWORD=... \\
    pytest tests/scenarios/test_feed_package.py -v -s
"""
import os
import time

import pytest

from lib import credential_defaults as cred
from lib import deploy as deploylib

pytestmark = [pytest.mark.story("O5"),pytest.mark.api, pytest.mark.hardware, pytest.mark.timeout(600)]

# Defaults pin the current feed RC; override via env for a new release.
EXPECT_VERSION = os.environ.get("FEED_EXPECT_VERSION", "0.6.0_alpha2_pre-r1")
EXPECT_COMMIT = os.environ.get(
    "FEED_EXPECT_COMMIT", "089e876cb24fd2fa8bd9d36323edb71e347e825b"
)


@pytest.fixture(scope="module")
def feed_package():
    path = os.environ.get("TOLLGATE_PACKAGE_PATH", "")
    if not path or not os.path.isfile(path):
        pytest.skip(
            "Set TOLLGATE_PACKAGE_PATH to the feed .ipk/.apk "
            "(make verify-feed-rc downloads it)"
        )
    ext = os.path.splitext(path)[1].lstrip(".")
    if ext not in ("ipk", "apk"):
        pytest.fail(f"TOLLGATE_PACKAGE_PATH must be .ipk or .apk, got {path}")
    return path, ext


class TestFeedPackage:
    """Install the feed RC over the router's own package manager and verify it."""

    def test_format_matches_router_package_manager(self, router, feed_package):
        _, ext = feed_package
        pm = deploylib.detect_package_manager(router)
        expected_ext = "apk" if pm == "apk" else "ipk"
        assert ext == expected_ext, (
            f"artifact is .{ext} but the router uses {pm} (expects .{expected_ext}) "
            "— wrong OpenWrt major for this artifact"
        )

    def test_install_from_feed(self, router, feed_package):
        path, _ = feed_package
        result = deploylib.deploy(router, path)
        assert result.get("success", True) is not False, f"deploy() failed: {result}"

    def test_installed_version(self, router, feed_package):
        pm = deploylib.detect_package_manager(router)
        if pm == "apk":
            # apk: `apk info -e` prints only the name; `apk list -I` prints
            # "tollgate-wrt-<version> <arch> ... [installed]".
            out = router.ssh(
                "apk list -I tollgate-wrt 2>/dev/null || "
                "apk list --installed 2>/dev/null | grep tollgate-wrt",
                timeout=15,
            )
        else:
            out = router.ssh("opkg list-installed tollgate-wrt 2>/dev/null", timeout=15)
        assert EXPECT_VERSION in out, f"expected {EXPECT_VERSION} in router package list: {out!r}"

    def test_build_identity_commit(self, router, feed_package):
        out = router.ssh(
            "/usr/bin/tollgate version --json 2>/dev/null || "
            "/usr/bin/tollgate version 2>/dev/null",
            timeout=15,
        )
        assert EXPECT_COMMIT in out, (
            f"installed build commit != feed pin {EXPECT_COMMIT}: {out[:300]!r}"
        )

    def test_api_healthy(self, router, feed_package):
        # tollgate-wrt can take ~30-60s on first boot (mint probes + wallet).
        last = ""
        for _ in range(30):
            last = router.ssh(
                "wget -qO- http://127.0.0.1:2121/ 2>/dev/null | head -c 200", timeout=10
            )
            if "kind" in last or "metric" in last or "pubkey" in last:
                return
            time.sleep(3)
        assert False, f"tollgate API on :2121 never responded: {last[:200]!r}"

    def test_services_running(self, router, feed_package):
        services = {"tollgate-wrt": "pidof tollgate-wrt", "nodogsplash": "pidof nodogsplash"}
        for name, cmd in services.items():
            out = router.ssh(f"{cmd} 2>/dev/null || echo DOWN", timeout=10)
            assert "DOWN" not in out, f"{name} is DOWN after installing the feed package"


@pytest.fixture(scope="module", autouse=True)
def _pre_install_root(router):
    """The root credential BEFORE any test in this module installs.

    Module-scoped and autouse, so it is captured before ``test_install_from_feed``
    runs; the credential class below reads the cached value to tell the
    empty->default path (fresh flash) from the preserve path (operator-set).
    """
    return {"state": cred.root_hash_state(router), "hash": cred.root_hash(router)}


class TestFeedCredentialDefaults:
    """The rc4 fixed-default credential behaviour, end to end.

    A fresh flash leaves root EMPTY; the install must set the FIXED default
    ``TollGate`` (never a random one), mark it PROVISIONAL, and serve the board —
    and a WRONG password must be rejected. When root was already set, the install
    must PRESERVE it byte-for-byte. Ordering: this class relies on pytest running
    ``TestFeedPackage.test_install_from_feed`` first (file order); the module
    fixture above captures the pre-install state either way.
    """

    def test_empty_root_installs_the_fixed_default(
        self, router, feed_package, _pre_install_root
    ):
        if _pre_install_root["state"] != "empty":
            pytest.skip(
                f"root was {_pre_install_root['state']!r} before install, not empty — "
                "flash a fresh image to exercise the empty->default path"
            )
        h = cred.root_hash(router)
        assert h, "root hash is still empty after install — the fixed default was not set"
        assert cred.hash_matches(cred.DEFAULT_CREDENTIAL, h), (
            "the installed default is not exactly 'TollGate' (hash: %r)" % h
        )
        assert cred.marker_present(router, cred.ADMIN_MARKER), (
            "admin provisional marker missing — the board would not force a change"
        )
        assert cred.login_works(router.host, cred.DEFAULT_CREDENTIAL), (
            "the router refused its own default credential over a fresh SSH login"
        )
        # The control the whole design exists for: a WRONG password must be
        # REJECTED. If the hash were still empty, rpcd/dropbear would accept ANY
        # password, and this is the assertion that catches it.
        assert not cred.login_works(router.host, "definitely-not-the-password"), (
            "a WRONG password was accepted — the root hash is not really set"
        )

    def test_existing_root_is_preserved(self, router, feed_package, _pre_install_root):
        if _pre_install_root["state"] != "set":
            pytest.skip("root was not already set; the preserve path does not apply")
        assert cred.root_hash(router) == _pre_install_root["hash"], (
            "the operator's existing root hash was rewritten by the install"
        )
        assert not cred.marker_present(router, cred.ADMIN_MARKER), (
            "an admin provisional marker was written although the credential was the operator's own"
        )

    def test_private_wifi_defaults_when_unset(self, router, feed_package):
        key = cred.private_wifi_key(router)
        if key and key != cred.DEFAULT_CREDENTIAL:
            pytest.skip("the operator chose a private-WiFi key; not the default path")
        assert key == cred.DEFAULT_CREDENTIAL, (
            "no private-WiFi key was installed (key=%r)" % key
        )
        assert cred.private_wifi_enabled(router), (
            "the private AP is not enabled with a default key"
        )
        assert cred.marker_present(router, cred.PRIVATE_WIFI_MARKER), (
            "private-WiFi provisional marker missing"
        )

    def test_install_time_credential_sources_are_not_honoured(self, router, feed_package):
        # The package sets the FIXED default, never an install-time value from
        # TOLLGATE_ADMIN_PASSWORD or a staged /etc/tollgate/admin-password file.
        h = cred.root_hash(router)
        for bogus in ("FromEnv-1234", "StagedFile-9x"):
            assert not cred.hash_matches(bogus, h), (
                "root was set to an install-time value (%r) — the default must win" % bogus
            )

