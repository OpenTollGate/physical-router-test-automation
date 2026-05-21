import logging
import json
import os
import shutil
import subprocess
import tempfile
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


def _list_workflow_runs(
    repo: str,
    workflow: str,
    *,
    branch: str | None = None,
    commit: str | None = None,
    limit: int = 10,
) -> list[dict]:
    cmd = [
        "gh", "run", "list",
        "--repo", repo,
        "--workflow", workflow,
        "--limit", str(limit),
        "--json", "databaseId,status,conclusion,headBranch,headSha",
    ]
    if commit:
        cmd.extend(["--commit", commit])
    elif branch:
        cmd.extend(["--branch", branch])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if r.returncode != 0:
        err = r.stderr.strip() or r.stdout.strip() or "unknown gh error"
        raise RuntimeError(f"gh run list failed: {err}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse gh run list output: {exc}") from exc
    return data if isinstance(data, list) else []


def _run_has_arch_artifact(repo: str, run_id: str, arch: str) -> bool:
    """Return True if the workflow run has a downloadable .ipk for arch.

    Uses the GitHub API to check artifact names without downloading.
    Falls back to download-based check if the API call fails.
    """
    try:
        r = subprocess.run(
            ["gh", "api",
             f"repos/{repo}/actions/runs/{run_id}/artifacts",
             "--paginate", "-q", ".artifacts[].name"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                if arch in line and ".ipk" in line and "upx" not in line:
                    log.info("Found artifact '%s' via API check", line)
                    return True
            return False
    except (subprocess.TimeoutExpired, Exception):
        pass

    with tempfile.TemporaryDirectory(prefix="tollgate-artifact-check-") as tmp:
        r = subprocess.run(
            ["gh", "run", "download", run_id, "--repo", repo, "--dir", tmp],
            capture_output=True, text=True, timeout=300, check=False,
        )
        if r.returncode != 0:
            return False
        matches = [
            p for p in Path(tmp).rglob("*.ipk")
            if p.is_file() and arch in p.name and "upx" not in p.name
        ]
        return bool(matches)


def _watch_run(repo: str, run_id: str, timeout_s: int) -> bool:
    """Wait for a workflow run to finish. Returns True if watch succeeded."""
    r = subprocess.run(
        [
            "gh", "run", "watch", run_id,
            "--repo", repo,
            "--exit-status",
            "--interval", "15",
        ],
        capture_output=True,
        text=True,
        timeout=max(timeout_s, 60),
        check=False,
    )
    return r.returncode == 0


def ensure_artifact(
    *,
    branch: str,
    arch: str,
    repo: str,
    workflow: str,
    commit: str | None = None,
    timeout_s: int = 1800,
) -> str:
    """Wait until a CI run has a downloadable artifact for arch. Never triggers builds.

    Returns the GitHub Actions run database ID.
    """
    deadline = time.time() + timeout_s
    actions_url = f"https://github.com/{repo}/actions/workflows"

    while time.time() < deadline:
        try:
            runs = _list_workflow_runs(repo, workflow, branch=branch, commit=commit, limit=15)
        except RuntimeError as exc:
            log.warning("%s", exc)
            runs = []

        if not runs:
            remaining = int(deadline - time.time())
            log.info(
                "No workflow runs yet for %s@%s (workflow=%r). Waiting... (%ds left)",
                repo, branch or commit, workflow, max(remaining, 0),
            )
            time.sleep(min(30, max(remaining, 1)))
            continue

        for run in runs:
            run_id = str(run.get("databaseId", ""))
            if not run_id:
                continue
            status = str(run.get("status", "")).lower()
            conclusion = str(run.get("conclusion") or "").lower()

            if status in ("queued", "in_progress", "pending", "waiting", "requested"):
                remaining = max(int(deadline - time.time()), 60)
                log.info("Run %s is %s — waiting up to %ds", run_id, status, remaining)
                _watch_run(repo, run_id, remaining)
                status = "completed"
                conclusion = ""
                view = subprocess.run(
                    ["gh", "run", "view", run_id, "--repo", repo, "--json", "conclusion,status"],
                    capture_output=True, text=True, timeout=30, check=False,
                )
                if view.returncode == 0:
                    try:
                        info = json.loads(view.stdout)
                        conclusion = str(info.get("conclusion") or "").lower()
                        status = str(info.get("status", "")).lower()
                    except json.JSONDecodeError:
                        pass

            if status == "completed" and conclusion not in ("", "success"):
                log.info("Run %s has conclusion=%s — checking for usable artifacts anyway", run_id, conclusion)
                if _run_has_arch_artifact(repo, run_id, arch):
                    log.info("Artifact ready: run %s has %s .ipk (despite overall failure)", run_id, arch)
                    return run_id
                continue

            if status == "completed" or conclusion == "success":
                if _run_has_arch_artifact(repo, run_id, arch):
                    log.info("Artifact ready: run %s has %s .ipk", run_id, arch)
                    return run_id
                log.info("Run %s succeeded but has no %s artifact yet", run_id, arch)

        remaining = int(deadline - time.time())
        if remaining <= 0:
            break
        log.info("No downloadable artifact yet — rechecking in 30s (%ds left)", remaining)
        time.sleep(min(30, remaining))

    ref = commit or branch
    raise RuntimeError(
        f"No downloadable {arch} CI artifact for {repo}@{ref} within {timeout_s}s. "
        f"Push to the branch and wait for workflow '{workflow}' to complete: {actions_url}"
    )


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

    def _download(run: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["gh", "run", "download", run, "--repo", artifact_repo, "--dir", str(BUILD_DIR)],
            capture_output=True, text=True, timeout=300,
        )

    log.info("Downloading artifacts from run %s", run_id)
    r = _download(run_id)
    if r.returncode != 0:
        err = r.stderr.strip() or r.stdout.strip() or "unknown gh error"
        if "no valid artifacts" in err.lower() and not os.environ.get("TOLLGATE_DISABLE_ARTIFACT_RERUN"):
            log.warning("No valid artifacts for run %s; trying to rerun x86_64/arch-specific build job", run_id)
            rerun = _rerun_arch_job(artifact_repo, run_id, arch)
            if rerun:
                if BUILD_DIR.exists():
                    shutil.rmtree(BUILD_DIR)
                BUILD_DIR.mkdir(parents=True, exist_ok=True)
                r = _download(run_id)
                if r.returncode == 0:
                    log.info("Artifact download succeeded after rerun")
                    err = ""
            if r.returncode == 0:
                pass
            else:
                err = r.stderr.strip() or r.stdout.strip() or err
        if r.returncode == 0:
            pass
        else:
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


def _rerun_arch_job(repo: str, run_id: str, arch: str) -> bool:
    needle = "x86_64" if arch == "x86_64" else arch
    r = subprocess.run(
        [
            "gh", "run", "view", run_id,
            "--repo", repo,
            "--json", "jobs",
        ],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        log.warning("Could not inspect run jobs for %s: %s", run_id, r.stderr.strip())
        return False
    try:
        jobs = json.loads(r.stdout).get("jobs", [])
    except json.JSONDecodeError as exc:
        log.warning("Could not parse run jobs for %s: %s", run_id, exc)
        return False

    candidates = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = str(job.get("name", ""))
        database_id = job.get("databaseId")
        if needle in name and database_id:
            candidates.append((name, str(database_id)))
    if not candidates:
        log.warning("No rerunnable job matched arch '%s' in run %s", arch, run_id)
        return False

    name, job_id = candidates[0]
    log.info("Rerunning job %s (%s)", job_id, name)
    rerun = subprocess.run(
        ["gh", "run", "rerun", "--repo", repo, "--job", job_id],
        capture_output=True, text=True, timeout=30,
    )
    if rerun.returncode != 0:
        log.warning("Could not rerun job %s: %s", job_id, rerun.stderr.strip())
        return False
    watch = subprocess.run(
        ["gh", "run", "watch", run_id, "--repo", repo, "--exit-status", "--interval", "15"],
        capture_output=True, text=True, timeout=1800,
    )
    if watch.returncode != 0:
        log.warning("Rerun did not complete successfully for run %s: %s", run_id, watch.stderr.strip())
        return False
    return True


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
