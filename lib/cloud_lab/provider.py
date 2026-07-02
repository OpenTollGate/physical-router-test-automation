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
    """Abstract interface for cloud lab VM lifecycle.

    Privacy: the ``can_publish`` property controls whether test results
    may be published to Nostr/tests.tollgate.me. Cloud providers (SHC,
    gcloud) use ephemeral VMs with no real user data — safe to publish.
    Local and physical providers may contain real SSIDs, MACs, IPs, and
    SSH keys — results must stay local (gitignored ``results/`` dir).
    """

    provider_name: str = "base"
    can_publish: bool = False

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

    provider_name = "gcloud"
    can_publish = True

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
    can_publish = True

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
        ssh_key = os.environ.get("SHC_SSH_KEY") or os.environ.get("TOLLGATE_GCP_SSH_KEY")
        if not ssh_key:
            for candidate in ["~/.ssh/id_ed25519", "~/.ssh/google_compute_engine"]:
                expanded = os.path.expanduser(candidate)
                if os.path.exists(expanded):
                    ssh_key = expanded
                    break
        cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", f"ConnectTimeout={min(timeout, 30)}",
        ]
        if ssh_key and os.path.exists(os.path.expanduser(ssh_key)):
            cmd.extend(["-i", os.path.expanduser(ssh_key)])
        cmd.extend([f"{user}@{vm.ip}", command])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"SSH failed: {r.stderr}")
        return r.stdout

    def scp_upload(self, vm, local_path, remote_path):
        import subprocess
        user = os.environ.get("SHC_SSH_USER", "debian")
        ssh_key = os.environ.get("SHC_SSH_KEY") or os.environ.get("TOLLGATE_GCP_SSH_KEY")
        if not ssh_key:
            for candidate in ["~/.ssh/id_ed25519", "~/.ssh/google_compute_engine"]:
                expanded = os.path.expanduser(candidate)
                if os.path.exists(expanded):
                    ssh_key = expanded
                    break
        cmd = [
            "scp", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
        ]
        if ssh_key and os.path.exists(os.path.expanduser(ssh_key)):
            cmd.extend(["-i", os.path.expanduser(ssh_key)])
        cmd.extend([local_path, f"{user}@{vm.ip}:{remote_path}"])
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
        ]

    _REAPABLE_PREFIXES = (
        "tollgate-",
        "ci-pulumi-",
        "ci-",
        "fips-cloud-",
        "fips-test-",
    )

    def cleanup_stale(self, max_age_hours=2):
        import datetime
        count = 0
        now = datetime.datetime.now(datetime.timezone.utc)
        for vm in self.list_vms():
            hostname = vm.hostname
            if not any(hostname.startswith(p) for p in self._REAPABLE_PREFIXES):
                continue
            try:
                created_str = vm.raw.get("date_created", "")
                created = datetime.datetime.strptime(
                    created_str, "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=datetime.timezone.utc)
            except (ValueError, AttributeError):
                continue
            age_hours = (now - created).total_seconds() / 3600
            if age_hours < max_age_hours:
                continue
            print(f"  Cancelling stale SHC VM: {hostname} ({age_hours:.1f}h old)")
            self.destroy_vm(vm, immediate=True)
            count += 1
        return count


_PROVIDERS: dict[str, type[VMProvider]] = {
    "gcloud": GCPProvider,
    "gcp": GCPProvider,
    "shc": SHCProvider,
    "shc-pulumi": None,  # populated below (lazy import to avoid circular dependency)
    "local": None,  # populated below
    "physical": None,  # populated below
}


class LocalProvider(VMProvider):
    """Local KVM/QEMU provider — uses a VM already running on this machine.

    Does NOT create or destroy VMs. The caller is responsible for starting
    the QEMU VM (e.g., via scripts/virtual-lab.py) before tests run.

    Privacy: can_publish=False — local VMs may contain real network
    configs, SSIDs, and device MACs from the operator's environment.
    Results are stored in the gitignored ``results/`` directory only.
    """

    provider_name = "local"
    can_publish = False

    def __init__(self):
        self._host = os.environ.get("TOLLGATE_SSH_HOST", "192.168.1.1")
        self._port = int(os.environ.get("TOLLGATE_SSH_PORT", "22"))
        self._user = os.environ.get("TOLLGATE_SSH_USER", "root")

    def create_vm(self, name="", **kwargs):
        return VMInfo(
            name=name or "local-vm",
            service_id="local",
            ip=self._host,
            provider="local",
        )

    def wait_for_ready(self, vm, timeout=30):
        import subprocess
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=no",
                 f"{self._user}@{vm.ip}", "echo READY"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return vm
            time.sleep(2)
        raise TimeoutError(f"Local VM at {vm.ip} not reachable after {timeout}s")

    def apply_ssh_key(self, vm, public_key):
        pass  # Local VMs use pre-configured SSH keys

    def ssh(self, vm, command, timeout=300):
        import subprocess
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no",
               f"{self._user}@{vm.ip}", command]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"SSH failed: {r.stderr}")
        return r.stdout

    def scp_upload(self, vm, local_path, remote_path):
        import subprocess
        subprocess.run(
            ["scp", "-O", "-o", "StrictHostKeyChecking=no",
             local_path, f"{self._user}@{vm.ip}:{remote_path}"],
            check=True, timeout=120,
        )

    def destroy_vm(self, vm, immediate=True):
        pass  # Caller manages local VM lifecycle

    def list_vms(self):
        return [VMInfo(name="local-vm", service_id="local", ip=self._host, provider="local")]

    def cleanup_stale(self, max_age_hours=2):
        return 0  # No cloud cleanup needed


class PhysicalProvider(VMProvider):
    """Physical router provider — connects to an existing physical router.

    No VM lifecycle at all. Tests run directly against a physical OpenWrt
    device via SSH. The router must be powered on and reachable.

    Privacy: can_publish=False — physical routers contain the operator's
    real network configuration, SSIDs, MAC addresses, and potentially
    SSH keys or passwords. Results NEVER leave the local machine.
    """

    provider_name = "physical"
    can_publish = False

    def __init__(self):
        self._host = os.environ.get("TOLLGATE_SSH_HOST") or os.environ.get("ROUTER_IP", "192.168.1.1")
        self._user = os.environ.get("TOLLGATE_SSH_USER", "root")
        self._port = int(os.environ.get("TOLLGATE_SSH_PORT", "22"))
        self._key = os.environ.get("TOLLGATE_SSH_KEY")
        self._jump = os.environ.get("TOLLGATE_JUMP_HOST")

    def create_vm(self, name="", **kwargs):
        return VMInfo(
            name=name or "physical-router",
            service_id="physical",
            ip=self._host,
            provider="physical",
        )

    def wait_for_ready(self, vm, timeout=30):
        import subprocess
        deadline = time.time() + timeout
        while time.time() < deadline:
            ssh_cmd = ["ssh", "-o", f"ConnectTimeout=3", "-o", "StrictHostKeyChecking=no"]
            if self._key:
                ssh_cmd.extend(["-i", self._key])
            if self._port != 22:
                ssh_cmd.extend(["-p", str(self._port)])
            if self._jump:
                ssh_cmd.extend(["-J", self._jump])
            ssh_cmd.extend([f"{self._user}@{vm.ip}", "echo READY"])
            r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return vm
            time.sleep(2)
        raise TimeoutError(f"Physical router at {vm.ip} not reachable after {timeout}s")

    def apply_ssh_key(self, vm, public_key):
        pass  # Physical routers use pre-installed keys

    def ssh(self, vm, command, timeout=300):
        import subprocess
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
        if self._key:
            cmd.extend(["-i", self._key])
        if self._port != 22:
            cmd.extend(["-p", str(self._port)])
        if self._jump:
            cmd.extend(["-J", self._jump])
        cmd.extend([f"{self._user}@{vm.ip}", command])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"SSH failed: {r.stderr}")
        return r.stdout

    def scp_upload(self, vm, local_path, remote_path):
        import subprocess
        cmd = ["scp", "-O", "-o", "StrictHostKeyChecking=no"]
        if self._key:
            cmd.extend(["-i", self._key])
        if self._port != 22:
            cmd.extend(["-P", str(self._port)])
        if self._jump:
            cmd.extend(["-J", self._jump])
        cmd.extend([local_path, f"{self._user}@{vm.ip}:{remote_path}"])
        subprocess.run(cmd, check=True, timeout=120)

    def destroy_vm(self, vm, immediate=True):
        pass  # Never power off a physical router

    def list_vms(self):
        return [VMInfo(name="physical-router", service_id="physical", ip=self._host, provider="physical")]

    def cleanup_stale(self, max_age_hours=2):
        return 0  # No cleanup for physical routers


_PROVIDERS["local"] = LocalProvider
_PROVIDERS["physical"] = PhysicalProvider


def get_provider(provider_name: str | None = None) -> VMProvider:
    """Get the configured VM provider.

    Reads TOLLGATE_VM_PROVIDER env var. Supported providers:

    - ``shc`` — Sovereign Hybrid Compute (ephemeral cloud VM, can_publish=True)
    - ``gcloud`` — Google Cloud Platform (ephemeral cloud VM, can_publish=True)
    - ``local`` — Local QEMU/KVM VM (privacy: results local only)
    - ``physical`` — Physical router via SSH (privacy: results local only)

    Default: ``shc`` (cheapest, proven in testing).
    """
    name = (provider_name or os.environ.get("TOLLGATE_VM_PROVIDER", "shc")).lower()
    # shc-pulumi is registered lazily to avoid a circular import (pulumi_runner
    # imports SHCProvider from this module). Pulumi is a required dependency.
    if name == "shc-pulumi":
        from .pulumi_runner import PulumiSHCProvider
        _PROVIDERS["shc-pulumi"] = PulumiSHCProvider
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown VM provider: {name}. "
            f"Must be one of: {', '.join(_PROVIDERS.keys())}"
        )
    return cls()
