"""VM provider abstraction — switch between GCP and SHC for cloud lab VMs.

Usage:
    from lib.cloud_lab.provider import get_provider, VMProvider

    provider = get_provider()  # reads TOLLGATE_VM_PROVIDER env var
    vm = provider.create_vm(name="test-runner", machine_type="2C/8GB")
    provider.wait_for_ready(vm)
    provider.apply_ssh_key(vm, ssh_pubkey)
    output = provider.ssh(vm, "uname -a")
    provider.destroy_vm(vm)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class VMInfo:
    name: str
    service_id: str | int
    ip: str = ""
    hostname: str = ""
    provider: str = ""
    zone: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class VMProvider:
    """Abstract interface for cloud lab VM lifecycle."""

    provider_name: str = "base"

    def create_vm(
        self,
        name: str,
        machine_type: str = "",
        disk_size_gb: int = 0,
        startup_script: str = "",
    ) -> VMInfo:
        raise NotImplementedError

    def wait_for_ready(self, vm: VMInfo, timeout: int = 300) -> VMInfo:
        raise NotImplementedError

    def apply_ssh_key(self, vm: VMInfo, public_key: str) -> None:
        raise NotImplementedError

    def ssh(self, vm: VMInfo, command: str, timeout: int = 300) -> str:
        raise NotImplementedError

    def scp_upload(self, vm: VMInfo, local_path: str, remote_path: str) -> None:
        raise NotImplementedError

    def destroy_vm(self, vm: VMInfo, immediate: bool = True) -> None:
        raise NotImplementedError

    def list_vms(self) -> list[VMInfo]:
        raise NotImplementedError

    def cleanup_stale(self, max_age_hours: int = 2) -> int:
        raise NotImplementedError


class GCPProvider(VMProvider):
    """GCP provider — wraps lib.cloud_lab.gcp functions."""

    provider_name = "gcloud"

    def create_vm(self, name, machine_type="", disk_size_gb=0, startup_script=""):
        from lib.cloud_lab.gcp import vm_up, get_project
        from lib.cloud_lab.constants import DEFAULT_ZONE, DEFAULT_MACHINE_TYPE, DEFAULT_DISK_SIZE_GB

        project = get_project()
        zone = os.environ.get("TOLLGATE_GCP_ZONE", DEFAULT_ZONE)
        mt = machine_type or os.environ.get("TOLLGATE_MACHINE_TYPE", DEFAULT_MACHINE_TYPE)
        disk = disk_size_gb or DEFAULT_DISK_SIZE_GB

        rc = vm_up(name, zone=zone, machine_type=mt, disk_size_gb=disk)
        if rc != 0:
            raise RuntimeError(f"GCP vm_up failed for {name}")

        from lib.cloud_lab.gcp import vm_external_ip
        ip = vm_external_ip(project, zone, name) or ""
        return VMInfo(
            name=name, service_id=name, ip=ip, provider="gcloud", zone=zone,
            raw={"project": project},
        )

    def wait_for_ready(self, vm, timeout=300):
        from lib.cloud_lab.gcp import vm_status, get_project

        project = vm.raw.get("project", get_project())
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = vm_status(project, vm.zone, vm.name)
            if status == "RUNNING":
                from lib.cloud_lab.gcp import vm_external_ip
                vm.ip = vm_external_ip(project, vm.zone, vm.name) or vm.ip
                return vm
            time.sleep(5)
        raise TimeoutError(f"VM {vm.name} not RUNNING after {timeout}s")

    def apply_ssh_key(self, vm, public_key):
        pass  # GCP uses metadata-based SSH keys, not live injection

    def ssh(self, vm, command, timeout=300):
        import subprocess
        from lib.cloud_lab.constants import SSH_KEY

        cmd = [
            "gcloud", "compute", "ssh", vm.name,
            f"--zone={vm.zone}", "--command", command,
            "--ssh-flag=-o StrictHostKeyChecking=no",
            "--ssh-flag=-o UserKnownHostsFile=/dev/null",
        ]
        if SSH_KEY:
            cmd.extend(["--ssh-key-file", SSH_KEY])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"SSH failed: {r.stderr}")
        return r.stdout

    def scp_upload(self, vm, local_path, remote_path):
        import subprocess
        cmd = [
            "gcloud", "compute", "scp", local_path,
            f"{vm.name}:{remote_path}",
            f"--zone={vm.zone}",
            "--ssh-flag=-o StrictHostKeyChecking=no",
        ]
        subprocess.run(cmd, check=True, timeout=120)

    def destroy_vm(self, vm, immediate=True):
        from lib.cloud_lab.gcp import vm_down, get_project
        project = vm.raw.get("project", get_project())
        vm_down(vm.name, zone=vm.zone)

    def list_vms(self):
        from lib.cloud_lab.gcp import _run_gcloud, get_project
        project = get_project()
        r = _run_gcloud([
            "compute", "instances", "list",
            f"--project={project}",
            "--format=json",
        ])
        if r.returncode != 0 or not r.stdout.strip():
            return []
        import json
        items = json.loads(r.stdout)
        return [
            VMInfo(
                name=i.get("name", ""),
                service_id=i.get("name", ""),
                ip=i.get("networkInterfaces", [{}])[0].get("accessConfigs", [{}])[0].get("natIP", ""),
                provider="gcloud",
                zone=i.get("zone", "").split("/")[-1],
                raw=i,
            )
            for i in items
            if "tollgate" in i.get("name", "").lower()
        ]

    def cleanup_stale(self, max_age_hours=2):
        from lib.cloud_lab.gcp import cleanup_stale
        return cleanup_stale(max_age_hours=max_age_hours)


class SHCProvider(VMProvider):
    """SHC provider — uses the shc-toolkit for VM lifecycle."""

    provider_name = "shc"

    _PACKAGE_MAP = {
        "n1-standard-2": (81, 245),
        "n1-standard-4": (82, 249),
        "n1-standard-8": (83, 253),
        "2C/8GB": (81, 245),
        "4C/16GB": (82, 249),
        "8C/32GB": (83, 253),
    }

    _CACHE_KEYS = [
        "blossomfs-8784100",
        "vwifi-host-server-072cdb8",
        "vwifi-host-ctrl-072cdb8",
        "vwifi-guest-client-072cdb8",
    ]

    def __init__(self):
        self._client = None

    def _choose_machine_type(self, requested: str) -> str:
        """Downgrade to Standard if all cache keys are available on Blossom.

        If every compilable binary is cached, we don't need extra CPU for
        compilation — a 2C/8GB VM downloads just as fast as 4C/16GB.
        Only upgrade to Professional when compilation is actually needed.
        """
        if requested and requested not in ("", "auto"):
            return requested

        try:
            import sys as _sys
            _sys.path.insert(0, os.path.dirname(__file__) + "/..")
            from lib.build_cache import BuildCache
            cache = BuildCache()
            if cache.all_cached(self._CACHE_KEYS):
                log.info("All cache keys available — ordering Standard (2C/8GB)")
                return "2C/8GB"
            log.info("Cache incomplete — ordering Professional (4C/16GB) for compilation")
            return "4C/16GB"
        except Exception as e:
            log.debug("Cache check failed, defaulting to Standard: %s", e)
            return "2C/8GB"

    @property
    def client(self):
        if self._client is None:
            sys_path = os.environ.get("SHC_TOOLKIT_PATH", "/Users/macbook/src/shc-toolkit")
            if sys_path not in __import__("sys").path:
                __import__("sys").path.insert(0, sys_path)
            from shc_toolkit.client import SHCClient
            self._client = SHCClient()
        return self._client

    def create_vm(self, name, machine_type="", disk_size_gb=0, startup_script=""):
        import uuid

        machine_type = self._choose_machine_type(machine_type)
        pkg = self._PACKAGE_MAP.get(machine_type, self._PACKAGE_MAP["2C/8GB"])
        result = self.client.submit_order(
            hostname=name,
            package_id=pkg[0],
            pricing_id=pkg[1],
        )
        sids = result.get("service_ids", [])
        if not sids:
            raise RuntimeError(f"SHC order failed: {result}")
        sid = int(sids[0])
        return VMInfo(name=name, service_id=sid, provider="shc", raw=result)

    def wait_for_ready(self, vm, timeout=300):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                info = self.client.get_vm(int(vm.service_id))
                state = info.get("provisioning_state", "unknown")
                if state == "ready":
                    ips = info.get("ips", [])
                    vm.ip = ips[0]["ip"] if ips else ""
                    vm.hostname = info.get("hostname", vm.name)
                    return vm
                if state in ("failed", "error"):
                    raise RuntimeError(f"SHC VM {vm.service_id} provisioning failed: {state}")
            except Exception as e:
                if "not_found" in str(e):
                    pass
                else:
                    log.debug(f"Polling SHC VM {vm.service_id}: {e}")
            time.sleep(5)
        raise TimeoutError(f"SHC VM {vm.service_id} not ready after {timeout}s")

    def apply_ssh_key(self, vm, public_key):
        self.client.apply_ssh_key_live(int(vm.service_id), public_key)

    def ssh(self, vm, command, timeout=300):
        import subprocess
        user = os.environ.get("SHC_SSH_USER", "debian")
        cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", f"ConnectTimeout={min(timeout, 30)}",
            f"{user}@{vm.ip}", command,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"SSH failed: {r.stderr}")
        return r.stdout

    def scp_upload(self, vm, local_path, remote_path):
        import subprocess
        user = os.environ.get("SHC_SSH_USER", "debian")
        cmd = [
            "scp", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            local_path, f"{user}@{vm.ip}:{remote_path}",
        ]
        subprocess.run(cmd, check=True, timeout=120)

    def destroy_vm(self, vm, immediate=True):
        self.client.cancel_vm(int(vm.service_id), immediate=immediate)

    def list_vms(self):
        vms = self.client.list_vms()
        return [
            VMInfo(
                name=v.get("hostname", ""),
                service_id=v.get("id"),
                ip=v.get("ips", [{}])[0].get("ip", "") if v.get("ips") else "",
                hostname=v.get("hostname", ""),
                provider="shc",
                raw=v,
            )
            for v in vms
            if "tollgate" in v.get("hostname", "").lower() or "test" in v.get("hostname", "").lower()
        ]

    def cleanup_stale(self, max_age_hours=2):
        count = 0
        for vm in self.list_vms():
            try:
                self.destroy_vm(vm, immediate=True)
                count += 1
            except Exception:
                pass
        return count


_PROVIDERS: dict[str, type[VMProvider]] = {
    "gcloud": GCPProvider,
    "gcp": GCPProvider,
    "shc": SHCProvider,
}


def get_provider(provider_name: str | None = None) -> VMProvider:
    """Get the configured VM provider.

    Reads TOLLGATE_VM_PROVIDER env var (default: gcloud).
    """
    name = (provider_name or os.environ.get("TOLLGATE_VM_PROVIDER", "gcloud")).lower()
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown VM provider: {name}. "
            f"Must be one of: {', '.join(_PROVIDERS.keys())}"
        )
    return cls()
