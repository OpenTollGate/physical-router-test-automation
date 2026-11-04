"""Measure QEMU vCPU/RAM claims from process command lines.

The repo has said for several slices that ``cpu_committed`` "cannot be measured
from /proc", and that is why ``new_vm_slots`` answers NOT_QUALIFIED. That premise
is false: every QEMU process states its own claim on its command line
(``-smp 2``, ``-m 1024``), and ``/proc/<pid>/cmdline`` is as readable as the rest
of ``/proc``.

Measured on this host 2026-11-03 — 4 live guests, claims recovered from cmdline:

    qemu-system-x86_64 ... -smp 2 -m 512 ...        (openwrt lab)
    qemu-system-x86_64 ... -memory 1536 -cores 2    (android emulator)
    qemu-system-x86_64 ... -smp 2 -m 1024 ...       (archy lab)

Why not just count cores? Because a claim is what a guest *reserved*, and it is
what a new lane must not oversubscribe. ``-smp`` under-counts when it carries a
topology (``-smp 4,sockets=2,cores=2``) and ``-cores`` is the only signal some
front-ends emit, so both spellings are parsed and the topology form is expanded.

Honesty rules:

* A guest whose claim cannot be parsed is NOT silently priced at zero — it is
  reported as ``unpriced``, which makes the whole figure UNKNOWN. Pricing an
  unreadable guest at zero is the dangerous direction.
* Nothing here is measured for a guest that is not running: a guess about idle
  processes is not evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "GuestClaim",
    "GuestClaims",
    "parse_qemu_claim",
    "measure_guest_claims",
]


@dataclass(frozen=True)
class GuestClaim:
    """One running guest's declared reservation."""

    pid: int
    vcpus: int
    ram_mib: int
    name: str = ""

    @property
    def priced(self) -> bool:
        return self.vcpus > 0 and self.ram_mib > 0


@dataclass(frozen=True)
class GuestClaims:
    """Every running guest's claim, plus the ones that could not be priced."""

    claimed: tuple[GuestClaim, ...] = ()
    unpriced: tuple[int, ...] = ()
    scanned: int = 0

    @property
    def complete(self) -> bool:
        """False when at least one guest's claim could not be recovered."""
        return not self.unpriced

    @property
    def vcpus(self) -> int:
        return sum(c.vcpus for c in self.claimed)

    @property
    def ram_mib(self) -> int:
        return sum(c.ram_mib for c in self.claimed)


_SMP_RE = re.compile(r"(?:^|\s)-smp\s+(\S+)")
_CORES_RE = re.compile(r"(?:^|\s)-cores\s+(\d+)")
_MEM_M_RE = re.compile(r"(?:^|\s)-m\s+(\d+)([MGmg]?)(?:\s|$)")
_MEM_LONG_RE = re.compile(r"(?:^|\s)-memory\s+(\d+)")
_NAME_RE = re.compile(r"(?:^|\s)-name\s+(\S+)")


def _expand_smp(value: str) -> int:
    """vCPUs from an ``-smp`` value.

    ``2`` → 2. ``4,sockets=2,cores=2,threads=1`` → 4. A topology WITHOUT a
    leading total (``sockets=2,cores=2``) is multiplied out, because a guest
    that reserved 2x2 is not 2 CPUs — and ``maxcpus`` wins when present because
    that is the ceiling the guest was given.
    """
    parts = [p.strip() for p in value.split(",")]
    # A leading bare integer is the vCPU total; everything else is topology.
    # Splitting on the FIRST comma would put ``sockets=2`` in the total slot and
    # silently drop it (measured: ``sockets=2,cores=2`` read as 2, not 4).
    total = 0
    if parts and parts[0].isdigit():
        total = int(parts[0])
        parts = parts[1:]
    if total > 0:
        return total

    factors: dict[str, int] = {}
    for part in parts:
        key, _, val = part.partition("=")
        if key and val.isdigit():
            factors[key.strip()] = int(val)
    if factors.get("maxcpus", 0) > 0:
        return factors["maxcpus"]
    product = 1
    for key in ("sockets", "dies", "cores", "threads"):
        if factors.get(key, 0) > 0:
            product *= factors[key]
    return product if product > 1 else 0


def parse_qemu_claim(pid: int, cmdline: str) -> GuestClaim:
    """Recover one guest's declared vCPU/RAM from its command line."""
    vcpus = 0
    smp = _SMP_RE.search(cmdline)
    if smp:
        # ``-smp`` is the primary signal; consult ``-cores`` only when ``-smp``
        # is absent or carries no usable number. Checking ``-cores`` first makes
        # ``-smp sockets=2,cores=2`` read as 2 instead of 4.
        vcpus = _expand_smp(smp.group(1))
    if vcpus <= 0:
        cores = _CORES_RE.search(cmdline)
        if cores:
            vcpus = int(cores.group(1))

    ram_mib = 0
    mem = _MEM_M_RE.search(cmdline)
    if mem:
        value = int(mem.group(1))
        unit = (mem.group(2) or "").lower()
        ram_mib = value * 1024 if unit == "g" else value
    if ram_mib <= 0:
        long_mem = _MEM_LONG_RE.search(cmdline)
        if long_mem:
            ram_mib = int(long_mem.group(1))

    name_m = _NAME_RE.search(cmdline)
    return GuestClaim(pid=pid, vcpus=vcpus, ram_mib=ram_mib, name=name_m.group(1) if name_m else "")


def measure_guest_claims(
    *,
    proc_root: Path | str = Path("/proc"),
    pids: Sequence[int] | None = None,
) -> GuestClaims:
    """Every running QEMU guest's claim, read from ``/proc/<pid>/cmdline``.

    ``pids`` is for tests; production scans ``/proc`` for ``qemu-system*``.
    A process whose cmdline cannot be read is skipped (it exited under us) —
    that is absence of evidence, not a zero-cost guest. A guest that IS running
    but whose claim cannot be parsed goes to ``unpriced``.
    """
    root = Path(proc_root)
    if pids is None:
        candidates: Iterable[int] = _scan_qemu_pids(root)
    else:
        candidates = list(pids)

    claimed: list[GuestClaim] = []
    unpriced: list[int] = []
    scanned = 0
    for pid in candidates:
        try:
            raw = (root / str(pid) / "cmdline").read_bytes()
        except OSError:
            continue  # gone: not a guest that exists to price
        if not raw:
            continue  # zombie/kernel-thread artefact
        scanned += 1
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        claim = parse_qemu_claim(pid, cmdline)
        if claim.priced:
            claimed.append(claim)
        else:
            unpriced.append(pid)
    return GuestClaims(claimed=tuple(claimed), unpriced=tuple(unpriced), scanned=scanned)


def _scan_qemu_pids(root: Path) -> list[int]:
    found: list[int] = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if b"qemu-system" in raw:
            found.append(int(entry.name))
    return sorted(found)
