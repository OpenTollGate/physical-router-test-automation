"""Pulumi spike: provision ONE disposable SHC VPS via the local shc-pulumi provider.

Isolation contract
------------------
- This program is NOT imported by anything in ``lib/`` or ``scripts/``.
- It reads credentials only from environment variables / Pulumi config secrets.
- It never prints secrets.
- ``pulumi destroy`` cancels the underlying SHC VPS (auto_cancel=True default).

Asymmetry vs the GCP imperative path
------------------------------------
SHC uses cloud-init (NoCloud seed CD-ROM) for its own provisioning but does NOT
expose a custom user-data injection point on any tier (empirically verified
2026-07-02). On NVMe/SSD/HDD cloud-init runs; on Dev VPS it is disabled by a
marker file. The "write a timestamped marker file on boot" goal from the spike
brief therefore cannot be expressed through the SHC order API. See
``docs/pulumi-shc-spike.md`` and ``../shc-toolkit/docs/cloud-init.md``. The
spike only proves VM lifecycle (create/read/destroy) via Pulumi.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pulumi
from pulumi import Output, ResourceOptions
from shc_pulumi import SHCVMResource


# ---------------------------------------------------------------------------
# Configuration resolution
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str:
    """Resolve the SHC API key.

    Priority:
      1. Pulumi config secret ``shc_api_key`` (if set via ``pulumi config set``)
      2. ``SHC_API_KEY`` environment variable

    Returns an empty string only if neither is set; the SHC provider will then
    raise a clear error. We never log the value.
    """
    cfg = pulumi.Config()
    # `get_secret` returns None when unset (does not raise); we keep the value
    # as an Output internally so Pulumi masks it in diffs/logs.
    from_cfg = cfg.get_secret("shc_api_key")
    if from_cfg:
        pulumi.log.info("shc_api_key: sourced from Pulumi config secret")
        return from_cfg
    from_env = os.environ.get("SHC_API_KEY", "")
    if from_env:
        pulumi.log.info("shc_api_key: sourced from SHC_API_KEY env var")
        return from_env
    pulumi.log.warn(
        "shc_api_key: NOT found in Pulumi config or SHC_API_KEY env var; "
        "the SHC provider will fail at create time."
    )
    return ""


def _resolve_ssh_pubkey() -> str | None:
    """Resolve an optional SSH public key.

    Priority:
      1. ``PULUMI_SPIKE_SSH_PUBKEY_PATH`` env var (absolute path to a .pub file)
      2. ``~/.ssh/id_ed25519.pub`` if it exists
      3. ``~/.ssh/id_rsa.pub`` if it exists
      4. None (VM created with no injected key; SHC default user only)

    The contents are read at preview time and passed as a plain input. The key
    itself is a PUBLIC key, so it is not secret material.
    """
    explicit = os.environ.get("PULUMI_SPIKE_SSH_PUBKEY_PATH", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend([
        Path.home() / ".ssh" / "id_ed25519.pub",
        Path.home() / ".ssh" / "id_rsa.pub",
    ])
    for cand in candidates:
        try:
            if cand.is_file():
                text = cand.read_text().strip()
                # Sanity: SSH public keys start with a known type prefix.
                if re.match(r"^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-\w+|sk-\w+-ssh-ed25519) ", text):
                    pulumi.log.info(f"ssh_key: using public key from {cand}")
                    return text
                pulumi.log.warn(f"ssh_key: {cand} does not look like an SSH public key; skipping")
        except OSError as exc:
            pulumi.log.warn(f"ssh_key: could not read {cand}: {exc}")
    pulumi.log.info("ssh_key: no public key found; VM will be created without one")
    return None


def _sanitize_hostname(value: str) -> str:
    """SHC hostnames must be DNS-safe."""
    cleaned = re.sub(r"[^a-z0-9-]", "-", value.lower()).strip("-")
    return cleaned or "tollgate-pulumi-spike"


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------

def build() -> None:
    cfg = pulumi.Config()

    stack = pulumi.get_stack() or "spike"
    project = pulumi.get_project() or "pulumi-shc-spike"

    # Deterministic, stack-unique hostname. Prefix matches the spike brief's
    # `tollgate-pulumi-spike-*` convention.
    configured_hostname = cfg.get("hostname") or f"tollgate-pulumi-spike-{stack}"
    hostname = _sanitize_hostname(configured_hostname)

    # Cheapest tier by default. Override with `pulumi config set size nvme-2c-8gb`.
    size = cfg.get("size") or "dev-1c-4gb"

    power_state = cfg.get("power_state") or "running"
    if power_state not in ("running", "stopped"):
        raise pulumi.RunError(
            f"power_state must be 'running' or 'stopped', got: {power_state!r}"
        )

    api_key = _resolve_api_key()
    ssh_pubkey = _resolve_ssh_pubkey()

    pulumi.log.info(
        f"spike plan: project={project} stack={stack} hostname={hostname} "
        f"size={size} power_state={power_state} ssh_key={'yes' if ssh_pubkey else 'no'}"
    )

    vm = SHCVMResource(
        "tollgate-runner",
        hostname=hostname,
        size=size,
        api_key=api_key,
        ssh_key=ssh_pubkey,
        auto_cancel=True,        # default; explicit for clarity
        power_state=power_state,
        opts=ResourceOptions(),
    )

    # Compose a human-friendly SSH command as a derived output. Note: SHC's
    # default user varies by template (e.g. `debian`); we surface os_user so the
    # caller can adapt.
    ssh_command = Output.all(vm.ip, vm.os_user).apply(
        lambda parts: (
            f"ssh root@{parts[0]}"
            if not parts[1]
            else f"ssh {parts[1]}@{parts[0]}"
        )
        if parts[0]
        else "(vm has no IP yet)"
    )

    pulumi.export("service_id", vm.service_id)
    pulumi.export("ip", vm.ip)
    pulumi.export("hostname", vm.hostname)
    pulumi.export("os_user", vm.os_user)
    pulumi.export("status", vm.status)
    pulumi.export("ssh_command", ssh_command)
    pulumi.export("destroy_hint", vm.service_id.apply(
        lambda sid: f"Run: ./run-destroy.sh  (cancels SHC service #{sid})"
    ))


if __name__ == "__main__":
    build()
