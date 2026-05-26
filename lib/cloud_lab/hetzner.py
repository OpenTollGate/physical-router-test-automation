"""Hetzner Cloud provider for fire-and-forget cloud lab runs.

Uses curl to the Hetzner Cloud REST API (no Python SDK dependency).
Mirrors the GCP provider pattern: create server from snapshot → worker runs → self-delete.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.cloud_lab.artifact import ensure_target_artifact
from lib.cloud_lab.constants import (
    HETZNER_API_TOKEN,
    HETZNER_API_URL,
    HETZNER_LOCATION,
    HETZNER_SERVER_TYPE,
    HETZNER_SNAPSHOT_NAME,
    HETZNER_SSH_KEY,
    HETZNER_SSH_KEY_ID,
    SUITE_REPO,
)
from lib.cloud_lab.gcp import _gh_token, _sanitize_vm_name, _suite_ref
from lib.cloud_lab.provider import CloudProvider
from lib.cloud_lab.resolve import RunTarget
from lib.cloud_lab.vps import _build_suite_overlay


def _require_token() -> str:
    if not HETZNER_API_TOKEN:
        print("ERROR: HETZNER_API_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)
    return HETZNER_API_TOKEN


def _run_hcloud(
    method: str,
    path: str,
    data: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    token = _require_token()
    cmd = [
        "curl", "-sf",
        "-X", method,
        f"{HETZNER_API_URL}{path}",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
    ]
    if data is not None:
        cmd.extend(["-d", json.dumps(data)])

    markers = ("Could not resolve", "Connection timed out", "Network is unreachable", "timed out")
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if r.returncode != 0:
                if any(m in (r.stderr or "") for m in markers) and attempt < 3:
                    time.sleep(3 * attempt)
                    continue
                if r.stderr and "error" in r.stderr.lower():
                    raise RuntimeError(f"Hetzner API error: {r.stderr.strip()[:500]}")
            if r.stdout.strip():
                return json.loads(r.stdout)
            return {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(3 * attempt)
    raise RuntimeError(f"Hetzner API call failed after 3 attempts: {last_exc}")


def _find_snapshot_id(name: str) -> int | None:
    r = _run_hcloud("GET", "/images?type=snapshot", timeout=30)
    for img in r.get("images", []):
        if img.get("name") == name or img.get("description") == name:
            return img["id"]
    return None


def _wait_for_ssh(ip: str, timeout: int = 180) -> bool:
    key = os.path.expanduser(HETZNER_SSH_KEY)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            [
                "ssh", "-i", key,
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=5",
                f"root@{ip}",
                "echo OK",
            ],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if r.returncode == 0 and "OK" in r.stdout:
            return True
        time.sleep(5)
    return False


def _ssh_to_server(
    ip: str,
    cmd: str,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    key = os.path.expanduser(HETZNER_SSH_KEY)
    r = subprocess.run(
        [
            "ssh", "-i", key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            f"root@{ip}",
            cmd,
        ],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"SSH command failed (rc={r.returncode}): {cmd[:200]}\n"
            f"stderr: {(r.stderr or '').strip()[:500]}"
        )
    return r


def _scp_to_server(local: str, ip: str, remote: str) -> subprocess.CompletedProcess[str]:
    key = os.path.expanduser(HETZNER_SSH_KEY)
    return subprocess.run(
        [
            "scp", "-O", "-i", key,
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            local, f"root@{ip}:{remote}",
        ],
        capture_output=True, text=True, timeout=60, check=False,
    )


class HetznerProvider(CloudProvider):
    """Hetzner Cloud provider using curl-based API calls."""

    @property
    def name(self) -> str:
        return "hetzner"

    def vm_up(self, name: str, **kwargs: Any) -> int:
        _require_token()
        snapshot_id = _find_snapshot_id(HETZNER_SNAPSHOT_NAME)
        if not snapshot_id:
            print(f"ERROR: Snapshot '{HETZNER_SNAPSHOT_NAME}' not found. "
                  "Run: python3 -m lib.cloud_lab.hetzner_snapshot --bake", file=sys.stderr)
            return 1

        payload: dict[str, Any] = {
            "name": name,
            "server_type": kwargs.get("server_type", HETZNER_SERVER_TYPE),
            "image": snapshot_id,
            "location": kwargs.get("location", HETZNER_LOCATION),
            "labels": {"tollgate_run": "true"},
            "public_net": {"enable_ipv4": True, "enable_ipv6": False},
        }
        if HETZNER_SSH_KEY_ID:
            payload["ssh_keys"] = [int(HETZNER_SSH_KEY_ID)]

        print(f"Creating Hetzner server '{name}' from snapshot {snapshot_id}...")
        r = _run_hcloud("POST", "/servers", data=payload, timeout=120)
        server = r.get("server", {})
        server_id = server.get("id")
        ip = ""
        ipv4_info = server.get("public_net", {}).get("ipv4", {})
        if isinstance(ipv4_info, dict):
            ip = ipv4_info.get("ip", "")
        print(f"Server {name} created (id={server_id}, ip={ip or 'pending'})")
        return 0

    def vm_down(self, name: str, **kwargs: Any) -> int:
        server_id = kwargs.get("server_id")
        if not server_id:
            server_id = self._find_server_id(name)
        if not server_id:
            print(f"Server '{name}' not found")
            return 0
        print(f"Deleting Hetzner server {name} (id={server_id})...")
        _run_hcloud("DELETE", f"/servers/{server_id}", timeout=60)
        print("Deleted")
        return 0

    def vm_status(self, name: str, **kwargs: Any) -> str | None:
        server_id = kwargs.get("server_id") or self._find_server_id(name)
        if not server_id:
            return None
        r = _run_hcloud("GET", f"/servers/{server_id}", timeout=30)
        return r.get("server", {}).get("status")

    def vm_external_ip(self, name: str, **kwargs: Any) -> str | None:
        server_id = kwargs.get("server_id") or self._find_server_id(name)
        if not server_id:
            return None
        r = _run_hcloud("GET", f"/servers/{server_id}", timeout=30)
        ipv4 = r.get("server", {}).get("public_net", {}).get("ipv4", {})
        if isinstance(ipv4, dict):
            return ipv4.get("ip")
        return None

    def submit_run(
        self,
        target: RunTarget,
        *,
        publish: bool = False,
        artifact_timeout_s: int = 1800,
        reseller_scenarios: bool = False,
        secondary_router_host: str = "",
        secondary_router_port: str = "",
        keep_vm_on_failure: bool = False,
        **kwargs: Any,
    ) -> dict[str, str]:
        _require_token()

        print(f"Waiting for CI artifact ({target.repo}@{target.branch}, arch=x86_64)...")
        artifact_run_id = ensure_target_artifact(target, timeout_s=artifact_timeout_s)
        print(f"Artifact ready: run {artifact_run_id}")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        short = (target.sut_commit or target.branch)[:7].replace("/", "-")
        run_id = f"{timestamp}-{short}"
        suite_ref = "main"
        local_ref = _suite_ref()
        if local_ref != "main":
            print(f"Note: suite_ref {local_ref[:7]} not reachable from VM, using 'main'")
        token = _gh_token()

        snapshot_id = _find_snapshot_id(HETZNER_SNAPSHOT_NAME)
        if not snapshot_id:
            raise RuntimeError(
                f"Snapshot '{HETZNER_SNAPSHOT_NAME}' not found. "
                "Run: python3 -m lib.cloud_lab.hetzner_snapshot --bake"
            )

        server_name = _sanitize_vm_name(run_id)
        print(f"Creating Hetzner server '{server_name}' from snapshot {snapshot_id}...")

        create_payload: dict[str, Any] = {
            "name": server_name,
            "server_type": kwargs.get("server_type", HETZNER_SERVER_TYPE),
            "image": snapshot_id,
            "location": kwargs.get("location", HETZNER_LOCATION),
            "labels": {"tollgate_run": "true", "tollgate_run_id": run_id},
            "public_net": {"enable_ipv4": True, "enable_ipv6": False},
        }
        if HETZNER_SSH_KEY_ID:
            create_payload["ssh_keys"] = [int(HETZNER_SSH_KEY_ID)]

        r = _run_hcloud("POST", "/servers", data=create_payload, timeout=120)
        server = r.get("server", {})
        server_id = server.get("id")
        ipv4 = server.get("public_net", {}).get("ipv4", {})
        server_ip = ipv4.get("ip", "") if isinstance(ipv4, dict) else ""

        if not server_ip:
            print("Waiting for server to get an IP...")
            for _ in range(30):
                time.sleep(2)
                r2 = _run_hcloud("GET", f"/servers/{server_id}", timeout=15)
                ipv4_info = r2.get("server", {}).get("public_net", {}).get("ipv4", {})
                if isinstance(ipv4_info, dict) and ipv4_info.get("ip"):
                    server_ip = ipv4_info["ip"]
                    break

        if not server_ip:
            raise RuntimeError(f"Server {server_name} did not get an IP")

        print(f"Server created: id={server_id} ip={server_ip}")

        print("Waiting for SSH...")
        if not _wait_for_ssh(server_ip, timeout=180):
            raise RuntimeError(f"SSH not available on {server_ip} after 180s")

        config = {
            "run_id": run_id,
            "sut_branch": target.branch,
            "sut_commit": target.sut_commit or "",
            "sut_pr": target.pr or "",
            "artifact_run_id": artifact_run_id,
            "artifact_repo": target.repo,
            "suite_ref": suite_ref,
            "backend": target.backend,
            "reseller_scenarios": reseller_scenarios,
            "secondary_router_host": secondary_router_host,
            "secondary_router_port": secondary_router_port,
            "keep_vm_on_failure": keep_vm_on_failure,
            "publish": publish,
            "provider": "hetzner",
            "gh_token": token,
            "hetzner_api_token": HETZNER_API_TOKEN,
            "project": "",
            "zone": HETZNER_LOCATION,
            "vm_name": str(server_id),
        }

        config_json = json.dumps(config, indent=2)
        local_config = Path(f"/tmp/tollgate-hetzner-config-{run_id}.json")
        local_config.write_text(config_json)

        print("Uploading worker config...")
        r_scp = _scp_to_server(str(local_config), server_ip, "/tmp/tollgate-worker-config.json")
        if r_scp.returncode != 0:
            local_config.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to upload config: {r_scp.stderr.strip()}")
        local_config.unlink(missing_ok=True)

        suite_overlay_b64 = _build_suite_overlay()
        overlay_local = Path(f"/tmp/tollgate-hetzner-overlay-{run_id}.b64")
        overlay_local.write_text(suite_overlay_b64)
        r_scp = _scp_to_server(str(overlay_local), server_ip, "/tmp/tollgate-suite-overlay.tar.gz.b64")
        overlay_local.unlink(missing_ok=True)
        if r_scp.returncode != 0:
            raise RuntimeError(f"Failed to upload overlay: {r_scp.stderr.strip()}")

        worker_cmd = (
            f"export GH_TOKEN={shlex.quote(token)} && "
            "cd /opt/tollgate-test && "
            f"git fetch origin && git checkout {shlex.quote(suite_ref)} || git checkout main && "
            "base64 -d /tmp/tollgate-suite-overlay.tar.gz.b64 > /tmp/tollgate-suite-overlay.tar.gz && "
            "tar xzf /tmp/tollgate-suite-overlay.tar.gz -C /opt/tollgate-test && "
            "echo 'Applied suite overlay' && "
            "if [ -d /opt/tollgate-venv ]; then "
            "/opt/tollgate-venv/bin/pip install -q -r requirements.txt 2>/dev/null || true; "
            "else "
            "python3 -m venv /opt/tollgate-venv && "
            "/opt/tollgate-venv/bin/pip install -q -r requirements.txt; "
            "fi && "
            "/opt/tollgate-venv/bin/python3 -m lib.cloud_lab.worker --from-file /tmp/tollgate-worker-config.json "
            ">> /var/log/tollgate-run.log 2>&1"
        )

        print(f"Starting worker on {server_ip}...")
        _ssh_to_server(
            server_ip,
            f"nohup bash -c {shlex.quote(worker_cmd)} >> /var/log/tollgate-run.log 2>&1 & echo $!",
            timeout=30,
            check=False,
        )

        log_hint = f"ssh -i {HETZNER_SSH_KEY} root@{server_ip} 'tail -f /var/log/tollgate-run.log'"
        return {
            "run_id": run_id,
            "vm_name": str(server_id),
            "project": "",
            "zone": HETZNER_LOCATION,
            "artifact_run_id": artifact_run_id,
            "suite_ref": suite_ref,
            "log_hint": log_hint,
            "server_ip": server_ip,
        }

    def status_run(self, run_id: str, **kwargs: Any) -> int:
        r = _run_hcloud("GET", "/servers?label_selector=tollgate_run=true", timeout=30)
        servers = r.get("servers", [])
        found = None
        for srv in servers:
            if srv.get("labels", {}).get("tollgate_run_id") == run_id:
                found = srv
                break
        if not found:
            print(f"Run {run_id}: server deleted (likely finished). Check https://tests.tollgate.me/")
            return 0
        name = found.get("name", "?")
        status = found.get("status", "?")
        srv_id = found.get("id", "?")
        ipv4 = found.get("public_net", {}).get("ipv4", {})
        ip = ipv4.get("ip", "N/A") if isinstance(ipv4, dict) else "N/A"
        print(f"Run: {run_id}")
        print(f"Server: {name} (id={srv_id}, status={status})")
        print(f"IP: {ip}")
        print(f"Logs: ssh -i {HETZNER_SSH_KEY} root@{ip} 'tail -f /var/log/tollgate-run.log'")
        return 0

    def cleanup_stale(self, max_age_hours: int = 2, **kwargs: Any) -> int:
        r = _run_hcloud("GET", "/servers?label_selector=tollgate_run=true", timeout=60)
        servers = r.get("servers", [])
        cutoff = time.time() - max_age_hours * 3600
        deleted = 0
        for srv in servers:
            srv_id = srv.get("id")
            created_str = srv.get("created", "")
            if not srv_id:
                continue
            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if created > cutoff:
                continue
            name = srv.get("name", "?")
            print(f"Deleting stale server {name} (id={srv_id}, created {created_str})...")
            _run_hcloud("DELETE", f"/servers/{srv_id}", timeout=60)
            deleted += 1
        print(f"Deleted {deleted} stale server(s)")
        return 0

    def cleanup_all(self, **kwargs: Any) -> int:
        r = _run_hcloud("GET", "/servers?label_selector=tollgate_run=true", timeout=60)
        servers = r.get("servers", [])
        deleted = 0
        for srv in servers:
            srv_id = srv.get("id")
            name = srv.get("name", "?")
            if not srv_id:
                continue
            print(f"Deleting server {name} (id={srv_id})...")
            _run_hcloud("DELETE", f"/servers/{srv_id}", timeout=60)
            deleted += 1
        print(f"Deleted {deleted} server(s)")
        return 0

    def ssh_command(self, name: str, user: str = "root") -> list[str]:
        ip = name
        if name.isdigit():
            ip_result = self.vm_external_ip(name, server_id=int(name))
            ip = ip_result or name
        return [
            "ssh", "-i", os.path.expanduser(HETZNER_SSH_KEY),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            f"{user}@{ip}",
        ]

    def _find_server_id(self, name: str) -> int | None:
        r = _run_hcloud("GET", "/servers?label_selector=tollgate_run=true", timeout=30)
        for srv in r.get("servers", []):
            if srv.get("name") == name:
                return srv["id"]
            if str(srv.get("id")) == name:
                return srv["id"]
        return None
