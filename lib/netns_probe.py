"""netns_probe — safe host-NIC probing inside a throwaway network namespace.

Probing a router (uboot / web UI / DHCP) on a directly-attached NIC is normally
done by moving that NIC into a netns. If the NIC is the host's *primary*
interface (holds the default route, or a fleet/LAN address) this silently
removes the host from the network — exactly the 2026-10-13 cobrador incident
where the built-in I219 was left stranded in a `probe_router` netns and the
host "lost" its ethernet interface.

This helper makes the operation safe:
  * refuse to move a protected interface (default route / managed CIDR);
  * require an explicit, dedicated probe adapter;
  * always return the interface and delete the netns on exit (try/finally),
    even on error — no orphan nets, no stranded NICs.

Only `ip` is used. All commands are injectable for testing.
"""
from __future__ import annotations

import ipaddress
import shutil
import subprocess
from dataclasses import dataclass, field

MANAGED_CIDRS_DEFAULT = ("192.168.2.0/24",)
NETNS_NAME = "prta-probe"


class UnsafeProbeInterface(RuntimeError):
    """Raised when the requested probe interface is not safe to repurpose."""


def _run(argv, *, runner=subprocess.run, check=False):
    return runner(argv, capture_output=True, text=True, check=check)


def default_route_ifaces(runner=subprocess.run) -> set[str]:
    out = _run(["ip", "-o", "route", "show", "default"], runner=runner).stdout
    devs = set()
    for line in out.splitlines():
        parts = line.split()
        if "dev" in parts:
            devs.add(parts[parts.index("dev") + 1])
    return devs


def iface_addresses(iface: str, runner=subprocess.run) -> list[str]:
    out = _run(["ip", "-o", "addr", "show", "dev", iface], runner=runner).stdout
    addrs = []
    for line in out.splitlines():
        toks = line.split()
        if "inet" in toks:
            addrs.append(toks[toks.index("inet") + 1])  # 192.168.2.43/24
    return addrs


def unsafe_reason(iface: str, *, protected: set[str], managed_cidrs,
                  runner=subprocess.run) -> str | None:
    """Return why `iface` must NOT be moved into a netns, or None if safe."""
    if iface in protected:
        return f"{iface} carries the default route"
    for addr in iface_addresses(iface, runner=runner):
        try:
            ip = ipaddress.ip_interface(addr).ip
        except ValueError:
            continue
        for cidr in managed_cidrs:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return f"{iface} has a managed address {addr} ({cidr})"
    return None


@dataclass
class NetnsProbe:
    iface: str
    namespace: str = NETNS_NAME
    managed_cidrs: tuple = field(default=MANAGED_CIDRS_DEFAULT)
    runner: object = subprocess.run
    _moved: bool = False

    def __enter__(self) -> "NetnsProbe":
        if not self.iface:
            raise UnsafeProbeInterface("no probe interface given (set PRTA_PROBE_IFACE)")
        if shutil.which("ip") is None:
            raise UnsafeProbeInterface("`ip` not found")
        managed = getattr(self.runner, "managed_cidrs", None) or self.managed_cidrs
        reason = unsafe_reason(self.iface, protected=default_route_ifaces(self.runner),
                               managed_cidrs=managed, runner=self.runner)
        if reason:
            raise UnsafeProbeInterface(
                f"refusing to probe on {self.iface}: {reason}; use a dedicated "
                f"adapter (PRTA_PROBE_IFACE)")
        self._run(["ip", "netns", "add", self.namespace])
        self._run(["ip", "link", "set", self.iface, "netns", self.namespace])
        self._moved = True
        self._run(["ip", "netns", "exec", self.namespace, "ip", "link", "set", self.iface, "up"])
        return self

    def _run(self, argv):
        return _run(argv, runner=self.runner)

    def exec(self, *cmd):
        return self._run(["ip", "netns", "exec", self.namespace, *cmd])

    def __exit__(self, *exc):
        # ALWAYS return the interface and drop the netns — never strand a NIC.
        if self._moved:
            self._run(["ip", "netns", "exec", self.namespace,
                       "ip", "link", "set", self.iface, "netns", "1"])
        self._run(["ip", "netns", "del", self.namespace])
        self._moved = False
        return False
