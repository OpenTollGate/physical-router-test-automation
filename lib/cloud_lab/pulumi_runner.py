"""SHC VM provider backed by Pulumi Automation API.

Drop-in alternative to :class:`lib.cloud_lab.provider.SHCProvider`. Selected
when ``TOLLGATE_VM_PROVIDER=pulumi`` (or via the ``--pulumi`` flag on
``scripts/cloud-lab.py``). Only the VM create/destroy path is swapped to
Pulumi Automation API; everything else (SSH key injection via
``apply_ssh_key_live``, SSH command execution, raw ``SHCClient`` access) is
inherited unchanged from :class:`SHCProvider` and still uses shc-toolkit.

This is the first incremental step of the gradual Pulumi adoption plan in
``docs/pulumi-shc-spike.md``. The imperative ``SHCProvider`` remains the
default; this class is opt-in.

Why Pulumi only for create/destroy:
    SHC has no custom cloud-init user-data (empirically verified 2026-07-02,
    see ``../shc-toolkit/docs/cloud-init.md``), so the worker bootstrap step
    cannot move to Pulumi — it stays imperative either way. Pulumi's value is
    in the VM lifecycle (declarative state, diff/preview, idempotent up/destroy),
    which is exactly what this class swaps.

State isolation:
    Each VM gets its own Pulumi stack (``tollgate-cloud-lab-<run-id>``) in a
    local file backend rooted at ``PULUMI_WORKDIR`` (default
    ``~/.tollgate-pulumi``). Stacks are removed on destroy. Nothing touches the
    Pulumi service backend.
"""

from __future__ import annotations

import logging
import os
import re

from .provider import SHCProvider, VMInfo

log = logging.getLogger(__name__)

_PROJECT = "tollgate-cloud-lab"
_DEFAULT_SIZE = "dev-2c-8gb"  # mirrors SHCProvider's 2C/8GB default (pkg 81/245)
_PASSPHRASE_DEFAULT = "tollgate-cloud-lab-local-dev"


def _sanitize_stack_name(name: str) -> str:
    """Pulumi stack names: [a-zA-Z][a-zA-Z0-9_.-]*, max 90 chars."""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "-", name).strip("-")
    if cleaned and not cleaned[0].isalpha():
        cleaned = "tollgate-" + cleaned
    return (cleaned or "tollgate-runner")[:90]


class PulumiSHCProvider(SHCProvider):
    """SHC provider with VM lifecycle driven by Pulumi Automation API.

    Inherits ``apply_ssh_key``, ``ssh``, and the ``client`` property from
    :class:`SHCProvider` — only ``create_vm`` / ``wait_for_ready`` /
    ``destroy_vm`` are overridden to route through a Pulumi stack.
    """

    provider_name = "pulumi"
    can_publish = True

    def __init__(self) -> None:
        super().__init__()
        self._stack = None
        self._stack_name: str | None = None

    # -- program -----------------------------------------------------------

    @staticmethod
    def _program(hostname: str, size: str):
        """Return the inline Pulumi program (same shape as the spike's __main__)."""
        def program() -> None:
            import pulumi
            from shc_pulumi import SHCVMResource

            api_key = os.environ.get("SHC_API_KEY", "")
            if not api_key:
                raise RuntimeError(
                    "SHC_API_KEY not set — required for PulumiSHCProvider"
                )
            vm = SHCVMResource(
                "tollgate-runner",
                hostname=hostname,
                size=size,
                api_key=api_key,
            )
            pulumi.export("service_id", vm.service_id)  # type: ignore[attr-defined]
            pulumi.export("ip", vm.ip)  # type: ignore[attr-defined]
            pulumi.export("hostname", vm.hostname)  # type: ignore[attr-defined]
            pulumi.export("os_user", vm.os_user)  # type: ignore[attr-defined]
        return program

    def _workspace_opts(self):
        from pulumi import automation as auto  # type: ignore[attr-defined]

        workdir = os.environ.get("PULUMI_WORKDIR", os.path.expanduser("~/.tollgate-pulumi"))
        os.makedirs(workdir, exist_ok=True)
        return auto.LocalWorkspaceOptions(
            work_dir=workdir,
            secrets_provider="passphrase",
            env_vars={
                "PULUMI_CONFIG_PASSPHRASE": os.environ.get(
                    "PULUMI_CONFIG_PASSPHRASE", _PASSPHRASE_DEFAULT
                ),
            },
        )

    def _get_stack(self, name: str, hostname: str, size: str):
        from pulumi import automation as auto  # type: ignore[attr-defined]

        ws = self._workspace_opts()
        stack = auto.create_or_select_stack(
            stack_name=_sanitize_stack_name(name),
            project_name=_PROJECT,
            program=self._program(hostname, size),
            opts=ws,
        )
        return stack

    # -- VMProvider interface ---------------------------------------------

    def create_vm(self, name, machine_type="", disk_size_gb=0, startup_script=""):
        """Create the VM via ``pulumi up`` (blocks until provisioning_state==ready)."""
        size = self._size_for_machine_type(machine_type)
        self._stack_name = _sanitize_stack_name(name)
        log.info("PulumiSHCProvider: creating VM %r via Pulumi (size=%s)", name, size)
        stack = self._get_stack(name, hostname=name, size=size)
        stack.up()
        self._stack = stack
        outs = stack.outputs()
        sid = int(outs["service_id"].value)
        ip = outs.get("ip").value if outs.get("ip") else ""
        hostname = outs.get("hostname").value if outs.get("hostname") else name
        log.info("PulumiSHCProvider: VM ready service_id=%s ip=%s", sid, ip)
        return VMInfo(name=name, service_id=sid, ip=ip, hostname=hostname,
                      provider=self.provider_name, raw={k: v.value for k, v in outs.items()})

    def wait_for_ready(self, vm, timeout=300):
        """No-op: ``stack.up()`` already blocked until ``provisioning_state==ready``.

        Returns ``vm`` with ip/hostname populated from stack outputs (already
        set by :meth:`create_vm`). Kept for interface conformance.
        """
        if not vm.ip and self._stack is not None:
            outs = self._stack.outputs()
            vm.ip = outs.get("ip").value if outs.get("ip") else ""
            vm.hostname = outs.get("hostname").value if outs.get("hostname") else vm.name
        return vm

    def destroy_vm(self, vm, immediate=True):
        """Destroy via ``pulumi destroy`` (cancels the SHC service immediately)."""
        if self._stack is not None:
            log.info("PulumiSHCProvider: destroying VM %s via Pulumi", vm.service_id)
            self._stack.destroy()
            try:
                self._stack.workspace().remove_stack(self._stack.name)
            except Exception as exc:
                log.debug("remove_stack %s failed (non-fatal): %s", self._stack.name, exc)
            self._stack = None
            self._stack_name = None
            return
        # Fallback: if we never held a stack (e.g. process restart), cancel imperatively.
        log.warning(
            "PulumiSHCProvider: no live stack for service_id=%s; falling back to "
            "imperative cancel_vm", vm.service_id
        )
        super().destroy_vm(vm, immediate=immediate)

    def cleanup_stale(self, max_age_hours=2):
        """Cancel old VMs (inherited) + remove orphaned Pulumi stack state files."""
        count = super().cleanup_stale(max_age_hours=max_age_hours)

        workdir = os.environ.get("PULUMI_WORKDIR", os.path.expanduser("~/.tollgate-pulumi"))
        stacks_dir = os.path.join(workdir, ".pulumi", "stacks", _PROJECT)
        if os.path.isdir(stacks_dir):
            import time as _time
            cutoff = _time.time() - (max_age_hours * 3600)
            for fname in os.listdir(stacks_dir):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(stacks_dir, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        log.info("removed stale Pulumi stack state: %s", fname)
                except OSError as exc:
                    log.debug("remove stack state %s failed: %s", fpath, exc)

        return count

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _size_for_machine_type(machine_type: str) -> str:
        """Map the imperative ``machine_type`` vocab to pulumi ``size`` names."""
        mapping = {
            "": _DEFAULT_SIZE,
            "2C/8GB": "dev-2c-8gb",
            "n1-standard-2": "dev-2c-8gb",
            "1C/4GB": "dev-1c-4gb",
            "4C/16GB": "dev-4c-16gb",
            "n1-standard-4": "dev-4c-16gb",
            "8C/32GB": "dev-8c-32gb",
            "n1-standard-8": "dev-8c-32gb",
        }
        return mapping.get(machine_type, _DEFAULT_SIZE)
