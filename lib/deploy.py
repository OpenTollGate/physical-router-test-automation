import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("tollgate.deploy")

REPO = "OpenTollGate/tollgate-module-basic-go"
WORKFLOW = "Build and Publish"
BUILD_DIR = Path("/tmp/tollgate-build")

# Packages required by the test framework on the router.
# Factory reset wipes all opkg packages; these must be reinstalled.
TEST_DEPS = ["curl", "socat", "nodogsplash", "jq", "luci", "px5g-mbedtls"]


def detect_arch(router) -> str:
    """Detect the package architecture from a running OpenWrt router via SSH.

    Uses ``opkg print-architecture`` which lists all supported arches with
    priority numbers.  The *highest-priority* (largest number) non-trivial
    arch (i.e. not ``all`` / ``noarch``) is the native one.

    Falls back to parsing ``/etc/openwrt_release`` DISTRIB_ARCH.
    """
    try:
        out = router.ssh("opkg print-architecture 2>/dev/null", timeout=10)
    except Exception:
        out = ""

    if out:
        best_name, best_prio = None, -1
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "arch":
                name, prio = parts[1], int(parts[2])
                if name in ("all", "noarch"):
                    continue
                if prio > best_prio:
                    best_name, best_prio = name, prio
        if best_name:
            log.info("Detected router arch via opkg: %s", best_name)
            return best_name

    # Fallback: /etc/openwrt_release
    try:
        out = router.ssh(". /etc/openwrt_release && echo $DISTRIB_ARCH", timeout=10)
        if out.strip():
            log.info("Detected router arch via openwrt_release: %s", out.strip())
            return out.strip()
    except Exception:
        pass

    raise RuntimeError("Cannot detect router architecture via SSH")


def _ssh_env():
    pw = os.environ.get("TOLLGATE_SSH_PASSWORD") or os.environ.get("TOLLGATE_LUCI_PASSWORD")
    if not pw:
        return os.environ
    env = os.environ.copy()
    env["SSHPASS"] = pw
    return env


def _scp_to_router(router, local_path, remote_path):
    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        "-o", "LogLevel=ERROR",
    ]
    if router.identity_file:
        cmd = ["scp", "-O", "-i", router.identity_file] + ssh_opts
    else:
        pw = os.environ.get("TOLLGATE_SSH_PASSWORD") or os.environ.get("TOLLGATE_LUCI_PASSWORD")
        cmd = (["sshpass", "-e", "scp", "-O"] if pw else ["scp", "-O"]) + ssh_opts

    if router.jump_host:
        cmd += ["-J", router.jump_host]

    if router.port:
        cmd += ["-P", str(router.port)]

    cmd += [str(local_path), f"root@{router.host}:{remote_path}"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=_ssh_env())
    if r.returncode != 0:
        raise RuntimeError(f"SCP failed (exit {r.returncode}): {r.stderr.strip()[:300]}")


def _parse_version(opkg_line):
    if not opkg_line:
        return None
    parts = opkg_line.split()
    return parts[2] if len(parts) >= 3 else (parts[1] if len(parts) >= 2 else opkg_line)


def _wait_for_health(router, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if router.api_status("/") == 200:
            return True
        time.sleep(2)
    return False


def _wait_for_reboot(router, timeout=180):
    log.info("Waiting for router to come back online...")
    time.sleep(10)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            out = router.ssh("echo UP", timeout=5)
            if "UP" in out:
                log.info("Router is back online")
                return True
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, Exception):
            pass
        time.sleep(5)
    return False


def install_test_deps(router):
    log.info("Installing test dependencies: %s", ", ".join(TEST_DEPS))
    router.ssh("opkg update", timeout=60)
    router.ssh(f"opkg install {' '.join(TEST_DEPS)}", timeout=120)
    log.info("Test dependencies installed")


def download_artifact(branch: str, arch: str, run_id: str | None = None,
                      repo: str | None = None, workflow: str | None = None) -> Path:
    artifact_repo = repo or REPO
    artifact_workflow = workflow or WORKFLOW
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    if not run_id:
        log.info("Finding latest build for branch '%s'", branch)
        for status_filter in ("success", "completed"):
            r = subprocess.run(
                [
                    "gh", "run", "list",
                    "--repo", artifact_repo,
                    "--branch", branch,
                    "--status", status_filter,
                    "--workflow", artifact_workflow,
                    "--limit", "1",
                    "--json", "databaseId,status",
                    "--jq", ".[0].databaseId",
                ],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                run_id = r.stdout.strip()
                break
        if not run_id:
            if repo and repo != REPO:
                log.info("No build found on fork '%s', trying upstream '%s'", repo, REPO)
                return download_artifact(branch, arch, run_id=run_id, repo=None, workflow=workflow)
            raise RuntimeError(f"No builds found for branch '{branch}' on {artifact_repo}")
        log.info("Found run: %s", run_id)

    log.info("Downloading artifacts from run %s", run_id)
    r = subprocess.run(
        ["gh", "run", "download", run_id, "--repo", artifact_repo, "--dir", str(BUILD_DIR)],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        err = r.stderr.strip() or r.stdout.strip() or "unknown gh error"
        hint = (
            f"Could not download CI artifacts for {artifact_repo}@{branch} "
            f"(workflow={artifact_workflow!r}, run={run_id}, required_arch={arch!r})."
        )
        if "no valid artifacts" in err.lower():
            hint += (
                " GitHub reports no valid downloadable artifacts; this usually means "
                "the run artifacts expired, were deleted, or the release/tag did not upload them. "
                "For the GCP virtual lab, provide a fresh x86_64 .ipk via a new CI run/release "
                "or use a branch with current x86_64 artifacts."
            )
        raise RuntimeError(f"{hint} gh run download failed: {err}")

    matches = [p for p in BUILD_DIR.rglob(f"*{arch}*.ipk") if p.is_file() and "upx" not in p.name]
    if not matches:
        available = [str(p.relative_to(BUILD_DIR)) for p in BUILD_DIR.rglob("*.ipk") if p.is_file()]
        raise RuntimeError(f"No .ipk found for arch '{arch}'. Available: {available or 'none'}")

    src = matches[0]
    flat = BUILD_DIR / f"tollgate-wrt-{arch}.ipk"
    if src.resolve() != flat.resolve():
        shutil.copy2(src, flat)

    log.info("Artifact: %s (%.1f MB)", flat.name, flat.stat().st_size / (1024 * 1024))
    return flat


def deploy(router, ipk_path: Path, reboot: bool = False) -> dict[str, object]:
    ipk_path = Path(ipk_path)
    if not ipk_path.exists():
        raise FileNotFoundError(f"IPK not found: {ipk_path}")

    log.info("Installing test dependencies on router")
    install_test_deps(router)

    log.info("Copying %s to router", ipk_path.name)
    _scp_to_router(router, ipk_path, "/tmp/tollgate-wrt.ipk")

    log.info("Installing tollgate-wrt")
    router.ssh(
        "opkg install --force-overwrite /tmp/tollgate-wrt.ipk"
        " && /etc/init.d/tollgate-wrt restart"
        " && /etc/init.d/tollgate-basic restart 2>/dev/null"
        "; /etc/init.d/uhttpd restart 2>/dev/null"
        "; rm -f /tmp/tollgate-wrt.ipk",
        timeout=120,
    )

    if reboot:
        return reboot_router(router)

    log.info("Waiting for backend health on port 2121")
    healthy = _wait_for_health(router)
    version_out = router.ssh("opkg list-installed | grep tollgate-wrt", timeout=10)
    installed_version = _parse_version(version_out)
    health_code = 200 if healthy else router.api_status("/")

    return {
        "installed_version": installed_version,
        "health_code": health_code,
        "success": health_code == 200,
    }


def reboot_router(router, wait: bool = True) -> dict[str, object]:
    log.info("Rebooting router")
    try:
        router.ssh("reboot", timeout=5)
    except (subprocess.TimeoutExpired, Exception):
        pass

    if wait:
        _wait_for_reboot(router)
        install_test_deps(router)
        log.info("Waiting for backend health after reboot")
        healthy = _wait_for_health(router, timeout=120)
    else:
        healthy = False

    version_out = ""
    if healthy:
        try:
            version_out = router.ssh("opkg list-installed | grep tollgate-wrt", timeout=10)
        except Exception:
            pass

    return {
        "installed_version": _parse_version(version_out) if version_out else None,
        "health_code": 200 if healthy else 0,
        "success": healthy,
        "rebooted": True,
    }


def check_deployed(router) -> dict[str, object]:
    try:
        version_out = router.ssh("opkg list-installed | grep tollgate-wrt", timeout=10)
    except Exception:
        version_out = ""
    version = _parse_version(version_out) if version_out else None

    health_code = router.api_status("/")

    try:
        ps_out = router.ssh("ps | grep tollgate-wrt | grep -v grep", timeout=10)
        running = bool(ps_out.strip())
    except Exception:
        running = False

    return {
        "version": version,
        "healthy": health_code == 200,
        "running": running,
        "health_code": health_code,
    }


def factory_reset(router, reboot: bool = False, expected_mac: str | None = None) -> dict[str, object]:
    guard_mac = expected_mac or os.environ.get("TOLLGATE_EXPECTED_MAC", "")
    if guard_mac:
        log.info("Verifying router MAC address before factory reset")
        try:
            mac_out = router.ssh("cat /sys/class/net/br-lan/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null", timeout=5)
            actual_mac = mac_out.strip().lower()
            expected = guard_mac.lower()
            if actual_mac != expected:
                raise RuntimeError(
                    f"MAC MISMATCH — aborting factory reset! "
                    f"Expected {expected}, got {actual_mac}. "
                    f"Wrong router?"
                )
            log.info("MAC verified: %s", actual_mac)
        except RuntimeError:
            raise
        except Exception as e:
            log.warning("Could not verify MAC (%s) — proceeding anyway", e)

    log.info("Removing tollgate-wrt package")
    router.ssh("opkg remove tollgate-wrt 2>/dev/null", timeout=30)

    log.info("Cleaning config, firewall rules, uci-defaults")
    router.ssh(
        "rm -rf /etc/tollgate"
        " /etc/config/firewall-tollgate"
        " /etc/nodogsplash/htdocs"
        " /tmp/tollgate-debug.log"
        " /tmp/tollgate-portal.log",
        timeout=10,
    )
    router.ssh("rm -f /etc/uci-defaults/90-tollgate-captive-portal-symlink"
               " /etc/uci-defaults/95-tollgate*"
               " /etc/uci-defaults/98-tollgate*"
               " /etc/uci-defaults/99-tollgate*"
               " /etc/uci-defaults/99a-tollgate*"
               " /etc/uci-defaults/99b-tollgate*", timeout=10)

    log.info("Restoring uhttpd to port 80")
    router.ssh(
        "uci get uhttpd.main.listen_http | grep -q 8080"
        " && uci delete uhttpd.main.listen_http"
        " && uci add_list uhttpd.main.listen_http='0.0.0.0:80'"
        " && uci add_list uhttpd.main.listen_http='[::]:80'"
        " && uci commit uhttpd"
        " || true",
        timeout=15,
    )

    log.info("Disabling nodogsplash")
    router.ssh("/etc/init.d/nodogsplash stop 2>/dev/null; /etc/init.d/nodogsplash disable 2>/dev/null", timeout=10)

    router.ssh("fw4 restart 2>/dev/null", timeout=15)
    router.ssh("/etc/init.d/uhttpd restart 2>/dev/null", timeout=15)

    if reboot:
        return reboot_router(router)

    return {"success": True, "rebooted": False}


def firstboot_reset(router, expected_mac: str | None = None) -> dict[str, object]:
    guard_mac = expected_mac or os.environ.get("TOLLGATE_EXPECTED_MAC", "")
    if guard_mac:
        log.info("Verifying router MAC address before firstboot reset")
        try:
            mac_out = router.ssh("cat /sys/class/net/br-lan/address 2>/dev/null || cat /sys/class/net/eth0/address 2>/dev/null", timeout=5)
            actual_mac = mac_out.strip().lower()
            expected = guard_mac.lower()
            if actual_mac != expected:
                raise RuntimeError(
                    f"MAC MISMATCH — aborting firstboot reset! "
                    f"Expected {expected}, got {actual_mac}. "
                    f"Wrong router?"
                )
            log.info("MAC verified: %s", actual_mac)
        except RuntimeError:
            raise
        except Exception as e:
            log.warning("Could not verify MAC (%s) — proceeding anyway", e)

    log.info("Running firstboot -y && reboot")
    try:
        router.ssh("firstboot -y && reboot", timeout=10)
    except (subprocess.TimeoutExpired, Exception):
        pass

    if not _wait_for_reboot(router):
        return {"success": False, "rebooted": True, "error": "Router did not come back after firstboot"}

    install_test_deps(router)

    return {"success": True, "rebooted": True}


def deploy_branch(router, branch: str, arch: str | None = None,
                  run_id: str | None = None, force: bool = False,
                  reboot: bool = False, repo: str | None = None,
                  backend=None) -> dict[str, object]:
    if not arch:
        env_arch = os.environ.get("TOLLGATE_ROUTER_ARCH")
        if env_arch:
            arch = env_arch
        else:
            arch = detect_arch(router)
            log.info("Auto-detected router arch: %s", arch)

    if not force:
        status = check_deployed(router)
        if status["healthy"] and status["running"] and status["version"]:
            log.info("Already deployed: version=%s — skipping", status["version"])
            return {
                "installed_version": status["version"],
                "health_code": 200,
                "success": True,
                "skipped": True,
            }

    artifact_repo = repo or (backend.repo if backend else None)
    artifact_workflow = backend.workflow if backend else None
    ipk_path = download_artifact(branch, arch, run_id=run_id,
                                 repo=artifact_repo, workflow=artifact_workflow)
    return deploy(router, ipk_path, reboot=reboot)
