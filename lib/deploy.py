import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("tollgate.deploy")

REPO = "OpenTollGate/tollgate-module-basic-go"
WORKFLOW = "Build and Publish"
BUILD_DIR = Path("/tmp/tollgate-build")


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


def download_artifact(branch: str, arch: str, run_id: str | None = None) -> Path:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    if not run_id:
        log.info("Finding latest successful build for branch '%s'", branch)
        r = subprocess.run(
            [
                "gh", "run", "list",
                "--repo", REPO,
                "--branch", branch,
                "--status", "success",
                "--workflow", WORKFLOW,
                "--limit", "1",
                "--json", "databaseId",
                "--jq", ".[0].databaseId",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(f"gh run list failed: {r.stderr.strip()}")
        run_id = r.stdout.strip()
        if not run_id:
            raise RuntimeError(f"No successful runs found for branch '{branch}'")
        log.info("Found run: %s", run_id)

    log.info("Downloading artifacts from run %s", run_id)
    r = subprocess.run(
        ["gh", "run", "download", run_id, "--repo", REPO, "--dir", str(BUILD_DIR)],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh run download failed: {r.stderr.strip()}")

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


def deploy(router, ipk_path: Path, reboot: bool = False) -> dict:
    ipk_path = Path(ipk_path)
    if not ipk_path.exists():
        raise FileNotFoundError(f"IPK not found: {ipk_path}")

    log.info("Ensuring curl is installed on router")
    router.ssh("opkg update > /dev/null 2>&1; opkg list-installed | grep -q '^curl ' || opkg install curl", timeout=60)

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


def reboot_router(router, wait: bool = True) -> dict:
    log.info("Rebooting router")
    try:
        router.ssh("reboot", timeout=5)
    except (subprocess.TimeoutExpired, Exception):
        pass

    if wait:
        _wait_for_reboot(router)
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


def check_deployed(router) -> dict:
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


def factory_reset(router, reboot: bool = False, expected_mac: str | None = None) -> dict:
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


def deploy_branch(router, branch: str, arch: str | None = None,
                  run_id: str | None = None, force: bool = False,
                  reboot: bool = False) -> dict:
    arch = arch or os.environ.get("TOLLGATE_ROUTER_ARCH", "aarch64_cortex-a53")

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

    ipk_path = download_artifact(branch, arch, run_id=run_id)
    return deploy(router, ipk_path, reboot=reboot)
