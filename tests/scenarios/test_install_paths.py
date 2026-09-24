"""Dual-install-path end-to-end coverage on a *freshly flashed* bench router.

Two scenarios, each on a clean OpenWrt 25.12.x image, each asserting the same
three things plus the UI flow:

  A. direct package install  — fetch the published artifact for the router's
     arch/package manager, verify its sha256 against the release manifest,
     push + ``apk --no-check-certificate add --allow-untrusted``, then run the
     happy path;
  B. installer path          — the canonical operator command
     ``bash <(curl -fsSL <raw url>) --tag <tag> <router> <password> <ln>``,
     then run the happy path.

Both assert (1) **artifact identity** — the router's
``/usr/bin/tollgate-wrt`` sha256 equals the sha256 of that binary inside the
installed artifact, (2) the installed **version string**, (3) the **surfaces**
(``:2051`` splash, ``:2050`` stub, ``:2121`` API, ``:8080`` -> 307, SSH alive),
(4) the **POLICY** state (nodogsplash ``users_to_router`` set equality, the
``31-admin-board-not-guest-reachable.nft`` guard, ``:8090`` unreachable from a
br-lan client) and (5) that the *existing* happy-path UI suite passes
(``tests/protocol/captive-portal.spec.mjs``, describe ``captive portal — happy
path`` — the same spec ``make test-captive-portal-happy`` drives).

The bench is a single shared resource: every router-touching test here runs
under the bench lock (``lib.bench_lock``, ``~/.hermes/state/bench-mt3000.lock``)
and skips — loudly, naming the holder — if someone else owns it.

Run it (see docs/install-paths-e2e.md):

    TOLLGATE_SSH_HOST=192.168.1.1 TOLLGATE_SSH_PASSWORD=... \
    TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=true TOLLGATE_LN_ADDRESS=you@coinos.io \
    python -m pytest tests/scenarios/test_install_paths.py --no-deploy -v

``--no-deploy`` is REQUIRED: the session deploy fixture would otherwise rewrite
mints and enable the debug portal, which would invalidate the "policy as
installed" claim.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lib import fresh_flash as ff
from lib import install_paths as ip
from lib.bench_lock import BenchBusy, BenchLock

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.physical_only,
    pytest.mark.install_paths,
    pytest.mark.slow,
]

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"
#: the existing happy-path suite (reused, never re-implemented here)
HAPPY_PATH_REPORT = TESTS_DIR / "report" / "report.json"

FEED_TAG = os.environ.get("TOLLGATE_FEED_TAG", ip.FEED_RELEASE_DEFAULT)
FEED_VERSION = os.environ.get("TOLLGATE_FEED_VERSION", ip.FEED_VERSION_DEFAULT)
BENCH_TASK_ID = os.environ.get("TOLLGATE_BENCH_TASK_ID", "t_a05094ad")
ARTIFACT_DIR = Path(
    os.environ.get("TOLLGATE_ARTIFACT_DIR", Path.home() / ".cache" / "prta-install-paths" / FEED_TAG)
)
LN_ADDRESS = os.environ.get("TOLLGATE_LN_ADDRESS", "")
CAPTIVE_PORTAL_PORT = os.environ.get("TOLLGATE_CAPTIVE_PORTAL_PORT", "2051")
ALLOW_NONEMPTY_WALLET = os.environ.get("TOLLGATE_ALLOW_NONEMPTY_WALLET") == "1"
FLASH_ENABLED = os.environ.get("TOLLGATE_ENABLE_SYSUPGRADE_FLASHING") == "true"
ALLOW_HAPPY_PATH_SKIP = os.environ.get("TOLLGATE_HAPPY_PATH_ALLOW_SKIP") == "1"
ENABLED = os.environ.get("TOLLGATE_ENABLE_INSTALL_PATH_E2E") == "1"
INSTALLER_CHANNEL = os.environ.get("TOLLGATE_INSTALLER_CHANNEL", "")
INSTALLER_URL = os.environ.get("TOLLGATE_INSTALLER_URL", ip.INSTALLER_SCRIPT_URL)

#: state shared between scenario A and scenario B (policy parity)
SNAPSHOTS: dict[str, Any] = {}
#: host-side facts established once per session
HOST_FACTS: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# low-level helpers (raw exit codes matter — Router.ssh() drops them)
# ---------------------------------------------------------------------------


def _ssh(host: str, command: str, *, timeout: int = 60, password: str | None = None) -> tuple[str, int]:
    """Run *command* on the router.  Returns ``(combined_output, exit_code)``."""
    pw = password or os.environ.get("TOLLGATE_SSH_PASSWORD") or os.environ.get("TOLLGATE_LUCI_PASSWORD") or ""
    args = [
        "sshpass",
        "-e",
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "LogLevel=ERROR",
        f"root@{host}",
        command,
    ]
    env = os.environ.copy()
    env["SSHPASS"] = pw
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    return (result.stdout + result.stderr).strip(), result.returncode


def _http_code(url: str, *, timeout: int = 8) -> str:
    """HTTP status from the host (a br-lan client).  '000' = refused/timeout."""
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", str(timeout), url],
        capture_output=True,
        text=True,
        timeout=timeout + 10,
    )
    return (result.stdout or "000").strip() or "000"


def _surface_codes(host: str) -> dict[int, str]:
    """Probe the four expected surfaces plus :8090 from a br-lan client."""
    return {
        port: _http_code(f"http://{host}:{port}/", timeout=6)
        for port in (2051, 2050, 2121, 8080, 8090)
    }


def _installed_version(host: str, package_manager: str) -> str:
    out, _ = _ssh(host, ip.package_version_command(package_manager), timeout=30)
    return out.strip()


def _installed_binary_sha(host: str) -> str:
    out, _ = _ssh(host, ip.installed_binary_hash_command(), timeout=30)
    return out.strip()


def _capture_policy(host: str) -> dict:
    """Capture the full policy surface in one round trip (evidence + parity)."""
    uci_out, _ = _ssh(host, ip.uci_show_users_to_router_command(), timeout=30)
    guard_out, _ = _ssh(
        host,
        f"if [ -f {ip.GUARD_NFT_FILE} ]; then cat {ip.GUARD_NFT_FILE}; echo __rc=0; "
        "else echo __rc=1; fi",
        timeout=30,
    )
    guard_present = "__rc=0" in guard_out
    ruleset_out, _ = _ssh(
        host,
        "nft list ruleset 2>/dev/null | grep -c 'admin-board-not-guest-reachable' || true",
        timeout=30,
    )
    choices_out, _ = _ssh(
        host,
        "uci -q get system.@system[0].hostname; "
        "uci -q get wireless.@wifi-iface[1].ssid || uci -q get wireless.@wifi-iface[0].ssid; "
        "jq -r '.public_identities[]? | select(.name==\"owner\") | .lightning_address' "
        "/etc/tollgate/identities.json 2>/dev/null",
        timeout=30,
    )
    hostname, ssid, ln = (choices_out.splitlines() + ["", "", ""])[:3]
    return {
        "users_to_router_ports": sorted(ip.parse_users_to_router_ports(uci_out)),
        "guard_present": guard_present,
        "guard_content": guard_out,
        "guard_drops_br_lan": guard_present and ip.GUARD_DROP_MARKER in guard_out,
        "guard_loaded_rules": ruleset_out.strip(),
        "choices": {"hostname": hostname.strip(), "ssid": ssid.strip(), "lightning_address": ln.strip()},
        "raw_uci": uci_out,
    }


def _wait_for_router(host: str, timeout: int = 240) -> bool:
    """Wait for SSH to answer after a flash (ICMP is dropped — probe TCP 22)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ip.tcp_probe(host, 22, timeout=3):
            out, rc = _ssh(host, "echo ready", timeout=15)
            if rc == 0 and "ready" in out:
                return True
        time.sleep(5)
    return False


def _run_happy_path_suite(host: str) -> ip.HappyPathResult:
    """Drive the EXISTING happy-path UI suite and parse its Playwright report."""
    env = os.environ.copy()
    env.setdefault("TOLLGATE_CAPTIVE_PORTAL_HOST", host)
    env.setdefault("TOLLGATE_CAPTIVE_PORTAL_PORT", CAPTIVE_PORTAL_PORT)
    env.setdefault("ROUTER_IP", host)
    if HAPPY_PATH_REPORT.exists():
        HAPPY_PATH_REPORT.unlink()
    result = subprocess.run(
        ip.happy_path_suite_command(),
        cwd=str(TESTS_DIR),
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    HOST_FACTS["happy_path_stdout_tail"] = (result.stdout or "")[-4000:]
    HOST_FACTS["happy_path_stderr_tail"] = (result.stderr or "")[-2000:]
    if not HAPPY_PATH_REPORT.exists():
        pytest.fail(
            "the happy-path UI suite produced no JSON report at "
            f"{HAPPY_PATH_REPORT} (exit {result.returncode})\n{result.stdout[-2000:]}"
        )
    parsed = ip.parse_happy_path_report(ip.parse_report_json(HAPPY_PATH_REPORT))
    parsed.exit_code = result.returncode
    return parsed


def _stage_over_scp(host: str, local: Path, remote: str, *, timeout: int = 600) -> None:
    """Push a file with the legacy -O scp protocol (OpenWrt has no sftp-server)."""
    password = os.environ.get("TOLLGATE_SSH_PASSWORD") or os.environ.get("TOLLGATE_LUCI_PASSWORD") or ""
    proc = subprocess.run(
        [
            "sshpass", "-e", "scp", "-O",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            str(local), f"root@{host}:{remote}",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "SSHPASS": password},
    )
    assert proc.returncode == 0, f"scp -O failed ({proc.returncode}): {proc.stderr.strip()[:400]}"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bench_lock() -> Iterator[BenchLock]:
    """Hold the bench lock for the whole module, or skip naming the holder."""
    lock = BenchLock(
        purpose="prta-dual-install-path-e2e",
        task_id=BENCH_TASK_ID,
        path=os.environ.get("TOLLGATE_BENCH_LOCK"),
    )
    try:
        lock.acquire()
    except BenchBusy as exc:
        pytest.skip(f"bench not available — {exc}")
    SNAPSHOTS["bench_lock_holder"] = lock.holder.raw
    yield lock
    lock.release()


@pytest.fixture(scope="module", autouse=True)
def install_path_gate(request):
    """Opt-in only (like the reseller scenarios), and never with auto-deploy.

    This module flashes the bench and owns it for two full cycles, so it must
    not be picked up by a generic hardware sweep such as
    ``pytest tests/scenarios/ -m hardware``.  Enable it explicitly with
    ``TOLLGATE_ENABLE_INSTALL_PATH_E2E=1``.
    """
    if not ENABLED:
        pytest.skip(
            "install-path e2e is opt-in: set TOLLGATE_ENABLE_INSTALL_PATH_E2E=1 "
            "(it flashes the bench and holds the lock; see docs/install-paths-e2e.md)"
        )
    if not request.config.getoption("--no-deploy"):
        pytest.fail(
            "install-path coverage must run with --no-deploy: the session deploy fixture would "
            "rewrite mints (replace_mints), enable the debug portal and health-check the backend, "
            "which changes the policy state this module measures. "
            "Re-run: pytest tests/scenarios/test_install_paths.py --no-deploy"
        )


@pytest.fixture(scope="module", autouse=True)
def router_host(router, bench_lock) -> str:
    if router is None:
        pytest.skip("no router configured (set TOLLGATE_SSH_HOST/ROUTER_IP and TOLLGATE_SSH_PASSWORD)")
    if not ip.tcp_probe(router.host, 22, timeout=5):
        pytest.skip(f"{router.host}:22 not reachable — bench is down (this router drops ICMP)")
    return router.host


@pytest.fixture(scope="module")
def package_manager(router_host) -> str:
    """Detect apk vs opkg on the *image*, not from the host tooling."""
    out, _ = _ssh(router_host, "command -v apk >/dev/null 2>&1 && echo apk || echo opkg", timeout=20)
    pm = "apk" if out.strip() == "apk" else "opkg"
    release, _ = _ssh(router_host, ip.openwrt_release_command(), timeout=20)
    HOST_FACTS["openwrt_release"] = release
    HOST_FACTS["package_manager"] = pm
    return pm


@pytest.fixture(scope="module")
def router_arch(router_host, package_manager) -> str:
    release, _ = _ssh(router_host, ip.openwrt_release_command(), timeout=20)
    match = re.search(
        r"(aarch64_cortex-a53|aarch64_cortex-a72|arm_cortex-a7|mips64_octeonplus|mipsel_24kc|mips_24kc|x86_64)",
        release,
    )
    if not match:
        out, _ = _ssh(router_host, ip.installed_arch_command(package_manager), timeout=30)
        match = re.search(
            r"(aarch64_cortex-a53|aarch64_cortex-a72|arm_cortex-a7|mips64_octeonplus|mipsel_24kc|mips_24kc|x86_64)",
            out,
        )
    if not match:
        pytest.fail(f"could not determine the router arch (openwrt_release={release!r})")
    return match.group(1)


@pytest.fixture(scope="module")
def release_manifest() -> dict[str, str]:
    """SHA256SUMS for the feed release (host-side, no router contact)."""
    cache = ARTIFACT_DIR / "SHA256SUMS"
    if cache.exists():
        text = cache.read_text()
    else:
        text = ip.http_get(ip.release_asset_url(FEED_TAG, "SHA256SUMS")).decode("utf-8", "replace")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text)
    manifest = ip.parse_manifest(text)
    if not manifest:
        pytest.fail(f"empty manifest for {FEED_TAG}")
    HOST_FACTS["manifest_entries"] = len(manifest)
    return manifest


@pytest.fixture(scope="module")
def selection(release_manifest, router_arch, package_manager) -> ip.ArtifactSelection:
    return ip.select_artifact(release_manifest, router_arch, package_manager, release_tag=FEED_TAG, version=FEED_VERSION)


@pytest.fixture(scope="module")
def artifact(selection) -> ip.Artifact:
    """Download the selected artifact and verify it against the manifest."""
    art = selection.require()
    target = ARTIFACT_DIR / art.filename
    if not target.exists():
        ip.download(art.url, target)
    ok, actual = ip.verify_sha256(target, art.sha256)
    if not ok:
        pytest.fail(f"{art.filename}: sha256 {actual} != manifest {art.sha256} (delete {target} and retry)")
    HOST_FACTS["artifact"] = {
        "filename": art.filename,
        "url": art.url,
        "sha256": art.sha256,
        "bytes": target.stat().st_size,
        "path": str(target),
    }
    return art


@pytest.fixture(scope="module")
def expected_binary_sha(artifact) -> str:
    """The artifact-identity gate's expected value: the binary *inside* the artifact."""
    digest = ip.binary_sha256_from_artifact(ARTIFACT_DIR / artifact.filename, artifact.fmt)
    HOST_FACTS["expected_binary_sha256"] = digest
    return digest


# ---------------------------------------------------------------------------
# 0. fresh-flash prerequisite (wallet drained, image verified, sysupgrade -n)
# ---------------------------------------------------------------------------


class TestFreshFlashPrerequisite:
    """Everything that must be true *before* an install path is exercised."""

    def test_00_bench_lock_is_held_and_names_us(self, bench_lock):
        assert bench_lock.held
        line = bench_lock.holder.raw
        assert BENCH_TASK_ID in line, f"holder line does not name the task: {line}"
        assert "purpose=prta-dual-install-path-e2e" in line

    def test_01_image_is_a_verified_openwrt_25_12_mt3000_sysupgrade(self, tmp_path):
        image = os.environ.get("TOLLGATE_FRESH_FLASH_IMAGE") or None
        try:
            path = ff.resolve_image_path(image)
        except ff.ImageInvalid as exc:
            pytest.skip(f"no local sysupgrade image: {exc}")
        problems = ff.verify_image(path)
        assert not problems, problems
        HOST_FACTS["fresh_flash_image"] = {
            "path": path,
            "filename": os.path.basename(path),
            "sha256": ff.BENCH_IMAGE_SHA256,
        }

    def test_02_wallet_drain_gate_refuses_to_flash_money(self, router_host, package_manager):
        """The operator drains; the harness only verifies and refuses."""
        if package_manager != "apk":
            pytest.skip("wallet CLI probe is defined for the apk/25.x image")
        balance_out, _ = _ssh(router_host, ff.WALLET_BALANCE_COMMAND, timeout=60)
        listing_out, _ = _ssh(router_host, ff.ECASH_LISTING_COMMAND, timeout=30)
        state = ff.parse_wallet_state(balance_out, listing_out)
        HOST_FACTS["wallet_before_flash"] = {
            "total_sats": state.total_sats,
            "nonempty_ecash_files": state.nonempty_files,
            "summary": state.summary(),
        }
        if state.empty:
            return
        if ALLOW_NONEMPTY_WALLET:
            pytest.skip(f"non-empty wallet accepted via TOLLGATE_ALLOW_NONEMPTY_WALLET=1 — {state.summary()}")
        pytest.fail(
            f"REFUSING TO FLASH: wallet not empty ({state.summary()}). Drain first: "
            f"`{ff.DRAIN_COMMAND}` (operator step; the tokens are real money), then re-run. "
            "Set TOLLGATE_ALLOW_NONEMPTY_WALLET=1 only to accept the loss."
        )

    def test_03_sysupgrade_wipe_flash(self, router_host, package_manager, request):
        """Flash the image with ``sysupgrade -n`` — opt-in, never implicit."""
        if not FLASH_ENABLED:
            pytest.skip(
                "flashing disabled: set TOLLGATE_ENABLE_SYSUPGRADE_FLASHING=true (and have a "
                "recovery path) to run the two full flash cycles; see docs/install-paths-e2e.md"
            )
        try:
            path = ff.resolve_image_path(os.environ.get("TOLLGATE_FRESH_FLASH_IMAGE") or None)
        except ff.ImageInvalid as exc:
            pytest.skip(f"no local sysupgrade image: {exc}")
        assert not ff.verify_image(path), "image must be verified before flashing"

        remote = ff.remote_image_path(os.path.basename(path))
        _stage_over_scp(router_host, Path(path), remote, timeout=900)
        local_sha = ip.sha256_file(path)
        remote_sha, _ = _ssh(router_host, f"sha256sum {remote} | cut -d' ' -f1", timeout=120)
        assert remote_sha.strip() == local_sha, f"staged image sha mismatch: {remote_sha} != {local_sha}"
        HOST_FACTS["flash"] = {"image": os.path.basename(path), "sha256": local_sha, "mode": "sysupgrade -n"}
        cmd = ff.sysupgrade_command(remote)
        _ssh(router_host, cmd, timeout=30)  # the connection drops here; that is expected
        assert _wait_for_router(router_host), "router did not come back after sysupgrade"

    def test_04_image_is_fresh_and_apk_only(self, router_host, package_manager):
        release, _ = _ssh(router_host, ip.openwrt_release_command(), timeout=20)
        assert not ff.fresh_image_ready_violations(release), release
        installed, _ = _ssh(router_host, ip.package_version_command(package_manager), timeout=30)
        listing, _ = _ssh(router_host, f"ls {ff.TOLLGATE_DIR} 2>/dev/null", timeout=20)
        marker, _ = _ssh(router_host, ff.setup_marker_command(), timeout=20)
        assert not ff.no_tollgate_state_violations(
            installed, listing, setup_marker_present=bool(marker.strip())
        ), f"not a fresh image: installed={installed!r} dir={listing!r} marker={marker!r}"


# ---------------------------------------------------------------------------
# A. direct package install
# ---------------------------------------------------------------------------


class TestDirectPackageInstall:
    """fresh flash -> .apk (or .ipk under opkg) -> happy path."""

    def test_a1_artifact_selected_by_package_manager(self, selection, package_manager, router_arch):
        if selection.skipped:
            pytest.skip(selection.skip_reason)
        art = selection.require()
        assert art.arch == router_arch
        assert art.fmt == ip.format_for_package_manager(package_manager)

    def test_a2_artifact_matches_the_release_manifest(self, artifact):
        target = ARTIFACT_DIR / artifact.filename
        ok, actual = ip.verify_sha256(target, artifact.sha256)
        assert ok, f"{artifact.filename}: {actual} != manifest {artifact.sha256}"

    def test_a2b_artifact_ships_the_policy_material(self, artifact):
        """Flash-free: the payload must carry the guard and the port removal.

        If it does not, the POLICY assertions further down are unreachable with
        this release and the failure belongs *here*, with the reason, not in a
        mysterious port-probe failure later.
        """
        target = ARTIFACT_DIR / artifact.filename
        files = ip.payload_files(target, artifact.fmt)
        try:
            setup = ip.payload_text(target, artifact.fmt, ip.SETUP_SCRIPT)
        except ip.InstallPathError:
            setup = ""
        readiness = ip.artifact_policy_readiness(files, setup)
        HOST_FACTS["payload_policy_readiness"] = {
            **readiness,
            "nftables_d": [f for f in files if "nftables.d" in f],
        }
        assert not readiness["problems"], (
            f"{artifact.filename} ({FEED_TAG}) cannot satisfy the policy assertions: "
            + "; ".join(readiness["problems"])
        )

    def test_a3_ipk_is_rejected_on_an_apk_image(self, router_host, selection, package_manager):
        """Proves the *selection* matters: an .ipk cannot be installed on 25.x."""
        if package_manager != "apk":
            pytest.skip("only meaningful on an apk-only image")
        ipk = ARTIFACT_DIR / ip.artifact_filename(FEED_VERSION, selection.require().arch, "ipk")
        if not ipk.exists():
            ip.download(ip.release_asset_url(FEED_TAG, ipk.name), ipk)
        remote = f"/tmp/{ipk.name}"
        out, rc = _ssh(router_host, f"cat > {remote} < /dev/null; ls -l {remote}", timeout=60)
        assert rc == 0, out
        subprocess.run(
            ["sshpass", "-e", "scp", "-O", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", str(ipk), f"root@{router_host}:{remote}"],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "SSHPASS": os.environ.get("TOLLGATE_SSH_PASSWORD", "")},
        )
        reject_out, reject_rc = _ssh(router_host, ip.apk_install_command(remote), timeout=120)
        HOST_FACTS["ipk_rejection"] = {"exit_code": reject_rc, "output": reject_out[-800:]}
        assert reject_rc != 0, f"apk ACCEPTED an .ipk on an apk-only image:\n{reject_out}"
        assert ip.IPK_REJECTION_NEEDLE in reject_out, (
            f"expected {ip.IPK_REJECTION_NEEDLE!r} in the rejection, got:\n{reject_out}"
        )
        assert reject_rc == ip.IPK_REJECTION_EXIT or f"exit {ip.IPK_REJECTION_EXIT}" in reject_out
        _ssh(router_host, f"rm -f {remote}", timeout=30)

    def test_a4_direct_install_pushes_and_installs_the_artifact(self, router_host, artifact):
        local = ARTIFACT_DIR / artifact.filename
        remote = f"/tmp/{artifact.filename}"
        proc = subprocess.run(
            ["sshpass", "-e", "scp", "-O", "-o", "StrictHostKeyChecking=no",
             "-o", "UserKnownHostsFile=/dev/null", str(local), f"root@{router_host}:{remote}"],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "SSHPASS": os.environ.get("TOLLGATE_SSH_PASSWORD", "")},
        )
        assert proc.returncode == 0, proc.stderr
        staged, _ = _ssh(router_host, f"sha256sum {remote} | cut -d' ' -f1", timeout=120)
        assert staged.strip() == artifact.sha256, f"staged bytes differ: {staged} != {artifact.sha256}"

        out, rc = _ssh(router_host, ip.install_command("apk", remote), timeout=600)
        HOST_FACTS["install_A_output"] = out[-2000:]
        assert rc == 0, f"apk add failed ({rc}):\n{out}"
        assert "ERROR" not in out.upper(), out
        time.sleep(10)  # postinst restarts network/wifi/nodogsplash/uhttpd

    def test_a5_artifact_identity_gate(self, router_host, artifact, expected_binary_sha):
        """The installed binary must BE the artifact's binary — not merely 'a' build."""
        installed = _installed_binary_sha(router_host)
        problems = ip.identity_violations(installed, expected_binary_sha, artifact.filename)
        HOST_FACTS["identity_A"] = {"installed": installed, "expected": expected_binary_sha}
        assert not problems, problems

    def test_a6_version_string_matches_the_artifact(self, router_host, artifact, package_manager):
        version = _installed_version(router_host, package_manager)
        HOST_FACTS["version_A"] = version
        assert not ip.version_violations(version, artifact.filename), (
            f"installed version {version!r} does not match {artifact.filename}"
        )

    def test_a7_surfaces_and_policy(self, router_host, package_manager):
        codes = _surface_codes(router_host)
        policy = _capture_policy(router_host)
        SNAPSHOTS["A"] = {"surfaces": codes, "policy": policy, "package_manager": package_manager}
        HOST_FACTS["surfaces_A"] = codes
        HOST_FACTS["policy_A"] = policy

        surface_problems = ip.surface_violations(codes)
        policy_problems = ip.policy_violations(
            set(policy["users_to_router_ports"]),
            guard_present=policy["guard_present"],
            guard_drops_br_lan=policy["guard_drops_br_lan"],
            ssh_alive=ip.tcp_probe(router_host, 22, timeout=5),
        )
        assert not surface_problems, surface_problems
        assert not policy_problems, policy_problems

    def test_a8_happy_path_ui_flow_completes(self, router_host):
        """Drive the existing happy-path suite — do not invent a flow."""
        result = _run_happy_path_suite(router_host)
        SNAPSHOTS["happy_path_A"] = {
            "passed": result.passed,
            "failed": result.failed,
            "skipped": result.skipped,
            "missing": result.missing,
            "exit_code": result.exit_code,
        }
        problems = result.violations(allow_skip=ALLOW_HAPPY_PATH_SKIP)
        assert not problems, problems


# ---------------------------------------------------------------------------
# B. the canonical installer path
# ---------------------------------------------------------------------------


class TestInstallerPath:
    """fresh flash -> ``bash <(curl ... install-and-test.sh) --tag ...`` -> happy path."""

    def test_b1_installer_command_shape(self, tmp_path):
        """Fetch the installer from its repo URL and prove the command is right."""
        script = tmp_path / "install-and-test.sh"
        body = ip.http_get(INSTALLER_URL).decode("utf-8", "replace")
        script.write_text(body)
        HOST_FACTS["installer_script"] = {
            "url": INSTALLER_URL,
            "sha256": ip.sha256_file(script),
            "bytes": len(body),
            "supports_tag": ip.installer_supports_tag(body),
        }
        assert ip.installer_supports_tag(body), (
            "the fetched installer does not advertise --tag; the canonical command in this "
            "harness would silently install the pinned default instead of the release under test"
        )
        syntax = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, timeout=60)
        assert syntax.returncode == 0, syntax.stderr
        if not LN_ADDRESS:
            pytest.skip("set TOLLGATE_LN_ADDRESS (the installer's operator choice) to run scenario B")
        cmd = ip.installer_command(
            "192.168.1.1", "lab-password", LN_ADDRESS, tag=FEED_TAG, script_url=INSTALLER_URL
        )
        assert cmd[0] == "bash" and "--tag" in cmd[2]

    def test_b2_installer_deploys_the_release(self, router_host):
        if not LN_ADDRESS:
            pytest.skip("set TOLLGATE_LN_ADDRESS to run scenario B")
        password = os.environ.get("TOLLGATE_SSH_PASSWORD") or os.environ.get("TOLLGATE_LUCI_PASSWORD", "")
        assert password, "TOLLGATE_SSH_PASSWORD is required (the installer needs the router password)"
        cmd = ip.installer_command(
            router_host, password, LN_ADDRESS, tag=FEED_TAG, script_url=INSTALLER_URL
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        HOST_FACTS["install_B_output"] = (result.stdout or "")[-4000:]
        HOST_FACTS["install_B_exit"] = result.returncode
        assert result.returncode == 0, (
            f"installer path failed (exit {result.returncode}):\n{(result.stdout or '')[-3000:]}"
            f"\n{(result.stderr or '')[-1000:]}"
        )
        assert "=== DEPLOY COMPLETE ===" in (result.stdout or ""), (
            "the installer never reported DEPLOY COMPLETE — refusing to call this a pass"
        )
        assert _wait_for_router(router_host, timeout=120), "SSH did not survive the installer deploy"

    def test_b3_artifact_identity_gate(self, router_host, artifact, expected_binary_sha):
        installed = _installed_binary_sha(router_host)
        problems = ip.identity_violations(installed, expected_binary_sha, artifact.filename)
        HOST_FACTS["identity_B"] = {"installed": installed, "expected": expected_binary_sha}
        assert not problems, problems

    def test_b4_version_surfaces_and_policy(self, router_host, artifact, package_manager):
        version = _installed_version(router_host, package_manager)
        codes = _surface_codes(router_host)
        policy = _capture_policy(router_host)
        SNAPSHOTS["B"] = {"surfaces": codes, "policy": policy, "version": version}
        HOST_FACTS["version_B"] = version
        HOST_FACTS["surfaces_B"] = codes
        HOST_FACTS["policy_B"] = policy

        assert not ip.version_violations(version, artifact.filename), version
        surface_problems = ip.surface_violations(codes)
        policy_problems = ip.policy_violations(
            set(policy["users_to_router_ports"]),
            guard_present=policy["guard_present"],
            guard_drops_br_lan=policy["guard_drops_br_lan"],
            ssh_alive=ip.tcp_probe(router_host, 22, timeout=5),
        )
        assert not surface_problems, surface_problems
        assert not policy_problems, policy_problems

    def test_b5_happy_path_ui_flow_completes(self, router_host):
        result = _run_happy_path_suite(router_host)
        SNAPSHOTS["happy_path_B"] = {
            "passed": result.passed,
            "failed": result.failed,
            "skipped": result.skipped,
            "missing": result.missing,
            "exit_code": result.exit_code,
        }
        problems = result.violations(allow_skip=ALLOW_HAPPY_PATH_SKIP)
        assert not problems, problems

    def test_b6_policy_is_identical_to_the_package_only_install(self):
        """The card's claim: the two paths differ in CHOICES, not in policy."""
        if "A" not in SNAPSHOTS or "B" not in SNAPSHOTS:
            pytest.skip("both scenarios must have completed for the parity comparison")
        a, b = SNAPSHOTS["A"]["policy"], SNAPSHOTS["B"]["policy"]
        assert a["users_to_router_ports"] == b["users_to_router_ports"], (
            f"policy differs between install paths: {a['users_to_router_ports']} vs "
            f"{b['users_to_router_ports']}"
        )
        assert a["guard_present"] == b["guard_present"] is True
        assert a["guard_drops_br_lan"] == b["guard_drops_br_lan"] is True
        policy_same = {
            "users_to_router": a["users_to_router_ports"] == b["users_to_router_ports"],
            "guard_present": a["guard_present"] and b["guard_present"],
            "guard_drops_br_lan": a["guard_drops_br_lan"] and b["guard_drops_br_lan"],
        }
        diff = {
            "choices": {"A": a["choices"], "B": b["choices"]},
            "policy": policy_same,
            "expected_diff": "choices only",
        }
        HOST_FACTS["state_diff"] = diff
        assert diff["policy"] == {"users_to_router": True, "guard_present": True, "guard_drops_br_lan": True}


def test_zz_zz_dump_evidence(tmp_path):
    """Write the raw facts this run collected (evidence for the PR / card)."""
    if not HOST_FACTS:
        pytest.skip("nothing collected")
    target = Path(
        os.environ.get("TOLLGATE_INSTALL_PATHS_EVIDENCE") or (tmp_path / "install-paths-evidence.json")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(HOST_FACTS, indent=2, sort_keys=True, default=str))
    shutil.copyfile(target, tmp_path / "install-paths-evidence.json")
    print(f"\n[install-paths] evidence: {target}")
