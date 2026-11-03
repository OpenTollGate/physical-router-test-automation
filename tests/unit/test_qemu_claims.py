"""Unit tests for lib/qemu_claims.py — measuring what guests actually reserved.

The repo has declared for several slices that `cpu_committed` "cannot be
measured from /proc", and used that to justify NOT_QUALIFIED VM headroom. The
premise is false: a QEMU process states its own claim on its command line and
`/proc/<pid>/cmdline` is readable.

Real command lines captured on this host 2026-11-03 are used verbatim as
fixtures, so a parser change that breaks against the actual front-ends fails
here rather than in production.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.qemu_claims import (
    GuestClaims,
    measure_guest_claims,
    parse_qemu_claim,
)

# Verbatim from `ps -eo pid,args` on x280, 2026-11-03.
REAL_OPENWRT_LAB = (
    "qemu-system-x86_64 -machine q35,accel=kvm:tcg -cpu max -smp 2 -m 512 "
    "-drive file=/home/c/wt/qemu-lab/router-nds.qcow2,if=virtio,format=qcow2 "
    "-display none -daemonize -pidfile /home/c/wt/qemu-lab/qemu.pid"
)
REAL_ANDROID_EMU = (
    "/home/c/android-sdk/emulator/qemu/linux-x86_64/qemu-system-x86_64-headless "
    "-avd tollgate -no-window -no-snapshot -no-boot-anim -memory 1536 -cores 2 "
    "-gpu swiftshader_indirect -port 5580"
)
REAL_ARCHY_LAB = (
    "qemu-system-x86_64 -machine q35,accel=kvm:tcg -cpu max -smp 2 -m 1024 "
    "-drive file=/home/c/archy-tollgate-lab/images/openwrt.img,format=raw "
    "-pidfile /home/c/lab/qemu.pid -name pr1142-lab.qemu"
)


class TestParseRealCommandLines:
    def test_openwrt_lab_smp_and_megabytes(self):
        c = parse_qemu_claim(2018074, REAL_OPENWRT_LAB)
        assert (c.vcpus, c.ram_mib) == (2, 512)

    def test_android_emulator_uses_long_spellings(self):
        """The emulator emits ``-memory``/``-cores``, never ``-smp``/``-m``."""
        c = parse_qemu_claim(2018248, REAL_ANDROID_EMU)
        assert (c.vcpus, c.ram_mib) == (2, 1536)

    def test_archy_lab_and_name(self):
        c = parse_qemu_claim(2021276, REAL_ARCHY_LAB)
        assert (c.vcpus, c.ram_mib) == (2, 1024)
        assert c.name == "pr1142-lab.qemu"

    def test_three_real_guests_sum_correctly(self):
        caps = [parse_qemu_claim(p, t) for p, t in (
            (1, REAL_OPENWRT_LAB), (2, REAL_ANDROID_EMU), (3, REAL_ARCHY_LAB)
        )]
        assert sum(c.vcpus for c in caps) == 6
        assert sum(c.ram_mib for c in caps) == 3072


class TestSmpSpellings:
    def test_bare_total(self):
        assert parse_qemu_claim(1, "qemu-system-x86_64 -smp 8 -m 512").vcpus == 8

    def test_topology_style_total_wins(self):
        c = parse_qemu_claim(1, "qemu-system-x86_64 -smp 4,sockets=2,cores=2,threads=1 -m 512")
        assert c.vcpus == 4

    def test_topology_without_total_is_multiplied_out(self):
        """2 sockets x 2 cores is 4 vCPUs, not 2."""
        c = parse_qemu_claim(1, "qemu-system-x86_64 -smp sockets=2,cores=2 -m 512")
        assert c.vcpus == 4

    def test_maxcpus_wins_when_present(self):
        c = parse_qemu_claim(1, "qemu-system-x86_64 -smp maxcpus=6,cores=2 -m 512")
        assert c.vcpus == 6


class TestMemoryUnits:
    def test_gigabytes_convert_to_mib(self):
        assert parse_qemu_claim(1, "qemu-system-x86_64 -smp 2 -m 4G").ram_mib == 4096

    def test_megabyte_suffix_is_plain(self):
        assert parse_qemu_claim(1, "qemu-system-x86_64 -smp 2 -m 2048M").ram_mib == 2048

    def test_cores_only_frontend_still_prices(self):
        c = parse_qemu_claim(1, "qemu-system-x86_64 -cores 4 -m 2048")
        assert c.vcpus == 4


class TestUnpricedIsNotZero:
    def test_missing_smp_is_unpriced_not_zero_cost(self):
        c = parse_qemu_claim(1, "qemu-system-x86_64 -m 512")
        assert c.vcpus == 0
        assert not c.priced

    def test_missing_memory_is_unpriced(self):
        c = parse_qemu_claim(1, "qemu-system-x86_64 -smp 2")
        assert not c.priced


def _fake_proc(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    root = tmp_path / "proc"
    for pid, cmdline in entries.items():
        d = root / pid
        d.mkdir(parents=True)
        (d / "cmdline").write_bytes(cmdline)
    return root


def _cmd(s: str) -> bytes:
    return s.replace(" ", "\x00").encode() + b"\x00"


class TestMeasureGuests:
    def test_scans_only_qemu_processes(self, tmp_path):
        root = _fake_proc(tmp_path, {
            "10": _cmd(REAL_OPENWRT_LAB),
            "11": _cmd("/usr/bin/python3 -c pass"),
            "12": _cmd(REAL_ARCHY_LAB),
            "13": b"",
        })
        claims = measure_guest_claims(proc_root=root)
        assert sorted(c.pid for c in claims.claimed) == [10, 12]
        assert claims.complete
        assert claims.vcpus == 4
        assert claims.ram_mib == 1536

    def test_an_unpriceable_guest_makes_the_result_incomplete(self, tmp_path):
        """Pricing an unreadable guest at zero is the dangerous direction."""
        root = _fake_proc(tmp_path, {
            "10": _cmd(REAL_OPENWRT_LAB),
            "11": _cmd("qemu-system-x86_64 -display none"),
        })
        claims = measure_guest_claims(proc_root=root)
        assert claims.unpriced == (11,)
        assert not claims.complete

    def test_vanished_process_is_absence_not_zero(self, tmp_path):
        root = _fake_proc(tmp_path, {"10": _cmd(REAL_OPENWRT_LAB)})
        claims = measure_guest_claims(proc_root=root, pids=[10, 999])
        assert [c.pid for c in claims.claimed] == [10]
        assert claims.unpriced == ()

    def test_empty_scan_is_complete_and_zero(self, tmp_path):
        root = _fake_proc(tmp_path, {"11": _cmd("/usr/bin/python3 -c pass")})
        claims = measure_guest_claims(proc_root=root)
        assert claims.claimed == ()
        assert claims.complete
        assert claims.vcpus == 0

    def test_unnamed_claims_object_defaults_to_complete(self):
        assert GuestClaims().complete
