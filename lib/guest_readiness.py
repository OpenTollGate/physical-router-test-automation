"""Prove a QEMU guest's readiness dimensions, instead of leaving them unknown.

`read_local_guest` identifies a guest from a pidfile and `/proc` and deliberately
leaves health, topology, lease and client reach unproven — identification is not
readiness. That was right, and it made every guest report `unknown` forever,
because nothing ever *proved* those four dimensions.

This module proves the ones that are provable from the observation host:

* **health** — the guest's own SSH banner answers. Measured on the bench:
  `tg-poc-tap` guest (10.99.99.1) returned `SSH-2.0-dropbear`.
* **client reach** — the guest is reachable on its management address from the
  host that would drive it. Same probe, different meaning: one says "the guest
  is alive", the other says "I can reach it".
* **topology** — the lane's TAP interface exists and is attached to the lane's
  bridge. This is the check that catches "the VM is up but its network is not".
* **lease** — read from the host authority store when one is configured. With no
  store there is no authoritative answer, so it stays `None`. An unreadable lease
  is NOT "free".

Rules that keep this honest:

* a probe that FAILED leaves the dimension `None`, never `False`-as-if-proven;
* `health_ok=False` is reserved for a guest that answered wrong or refused;
* topology is only claimed when the TAP is present AND mastered by the expected
  bridge — "the interface exists" alone proves nothing about the lane.
"""
from __future__ import annotations

import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # pragma: no cover
    from lib.fleet_capacity import GuestObservation

__all__ = [
    "GuestProbe",
    "ProbeResult",
    "guest_observation_from_probe",
    "probe_guest_readiness",
    "parse_ssh_banner",
    "tap_topology",
]

_SSH_BANNER_RE = re.compile(r"^SSH-2\.0-(\S+)")


@dataclass(frozen=True)
class ProbeResult:
    """One dimension, with the evidence that supports it."""

    value: bool | None
    detail: str = ""


def parse_ssh_banner(text: str) -> ProbeResult:
    """Health from a service banner.

    An empty read is `None`: the port may be filtered, the guest may be booting,
    or the probe may have been refused. Only a well-formed SSH banner proves the
    service is up, so only that yields True.
    """
    for line in (text or "").splitlines()[:3]:
        match = _SSH_BANNER_RE.match(line.strip())
        if match:
            return ProbeResult(True, f"ssh banner: {match.group(1)}")
    if (text or "").strip():
        return ProbeResult(False, "answered, but not an SSH banner")
    return ProbeResult(None, "no banner within the timeout")


def tap_topology(
    tap: str,
    bridge: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> ProbeResult:
    """Topology from `bridge link show`, not from `ip link`.

    ``ip link`` proves an interface exists; it does not prove the lane's traffic
    reaches the bridge. Measured on the bench: `tg-poc-tap` is
    ``master tg-poc-br state forwarding`` — that triple is the evidence.
    """
    runner = run or subprocess.run
    try:
        proc = runner(
            ["bridge", "link", "show"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProbeResult(None, f"topology probe failed: {type(exc).__name__}")
    if getattr(proc, "returncode", 1) != 0:
        return ProbeResult(None, "bridge command unavailable")
    for line in (proc.stdout or "").splitlines():
        if f": {tap}:" not in line and not line.strip().startswith(tap):
            continue
        if f"master {bridge}" in line:
            forwarding = "state forwarding" in line
            return ProbeResult(
                True, f"{tap} mastered by {bridge}" + ("" if forwarding else ", state not forwarding")
            )
        return ProbeResult(False, f"{tap} present but not mastered by {bridge}")
    return ProbeResult(False, f"{tap} not present")


def _tcp_banner(host: str, port: int = 22, timeout: float = 3.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            return sock.recv(256).decode("utf-8", errors="replace")
    except OSError:
        return ""


@dataclass(frozen=True)
class GuestProbe:
    """What the observation host learned about one guest lane."""

    health: ProbeResult
    client_reachable: ProbeResult
    topology: ProbeResult
    lease_free: ProbeResult

    @property
    def all_proven(self) -> bool:
        return all(
            d.value is True
            for d in (self.health, self.client_reachable, self.topology, self.lease_free)
        )


def probe_guest_readiness(
    *,
    address: str,
    tap: str = "",
    bridge: str = "",
    lease_free: bool | None = None,
    banner_reader: Callable[[str], str] | None = None,
    topology_reader: Callable[[str, str], ProbeResult] | None = None,
    timeout: float = 3.0,
) -> GuestProbe:
    """Prove what can be proven from here; leave the rest unknown.

    ``lease_free`` is passed in because only the authoritative store can answer
    it. ``None`` means "no authoritative answer", which is different from "held".
    """
    read_banner = banner_reader or (lambda h: _tcp_banner(h, 22, timeout))
    banner = parse_ssh_banner(read_banner(address) if address else "")
    client = (
        ProbeResult(True, f"{address} reachable on :22")
        if banner.value is True
        else ProbeResult(None if banner.value is None else False, banner.detail)
    )
    topo = (
        topology_reader(tap, bridge)
        if (topology_reader and tap and bridge)
        else (tap_topology(tap, bridge) if (tap and bridge) else ProbeResult(None, "lane tap/bridge not declared"))
    )
    lease = (
        ProbeResult(lease_free, "authority says " + ("free" if lease_free else "held"))
        if lease_free is not None
        else ProbeResult(None, "no authoritative lease source")
    )
    return GuestProbe(health=banner, client_reachable=client, topology=topo, lease_free=lease)


def guest_observation_from_probe(
    probe: GuestProbe,
    *,
    fleet_id: str,
    resource_id: str,
    now: float,
    ttl_s: float,
    authority_epoch: int,
    pid: int | None = None,
    pid_start_ticks: int | None = None,
    expected_pid_start_ticks: int | None = None,
) -> "GuestObservation":
    """Mount proven dimensions onto the identity `read_local_guest` produced.

    Identity comes from the pidfile/`/proc` (a different question), readiness
    comes from here. Keeping them separate is what stops "the process exists"
    from being reported as "the lane is usable".
    """
    from lib.fleet_capacity import guest_observation

    return guest_observation(
        fleet_id=fleet_id,
        resource_id=resource_id,
        now=now,
        ttl_s=ttl_s,
        authority_epoch=authority_epoch,
        pid=pid,
        pid_start_ticks=pid_start_ticks,
        expected_pid_start_ticks=expected_pid_start_ticks,
        bridge_present=probe.topology.value,
        health_ok=probe.health.value,
        topology_ok=probe.topology.value,
        lease_free=probe.lease_free.value,
        client_reachable=probe.client_reachable.value,
    )
