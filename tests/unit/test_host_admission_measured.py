"""Host admission now uses MEASURED guest claims, not a placeholder.

Before this, `cpu_committed` was pinned to 0 with the dimension marked unknown,
which forced `new_vm_slots` to answer NOT_QUALIFIED for every host — VM headroom
could never be reported, whatever the machine was actually doing.

These tests pin the three behaviours that make the number trustworthy:

1. a complete measurement yields a real slot count;
2. an incomplete one (a guest whose claim could not be read) yields UNKNOWN, not
   a slot count derived from the partial set;
3. no measurement at all keeps the old fail-closed behaviour.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.fleet_capacity import Reason, State, new_vm_slots
from lib.fleet_observe import (
    UNMEASURED_HOST_DIMENSIONS,
    HostMetrics,
    host_observation_from_metrics,
)
from lib.qemu_claims import GuestClaim, GuestClaims

NOW = 1_800_000_000.0
TTL = 60.0
EPOCH = 3


def _metrics(**over) -> HostMetrics:
    base = dict(
        endpoint="this-host:8099",
        observed_at=NOW,
        hostname="this-host",
        cpu_total=8,
        load1=0.5,
        ram_total_mib=8192,
        ram_available_mib=6144,  # committed = total - available = 2048
        swap_total_mib=0,
        swap_free_mib=0,
        ram_reserve_mib=512,
        disk_path="/",
        disk_total_mib=100_000,
        disk_free_mib=50_000,
        inode_total=2_000_000,
        inode_free=1_000_000,
        kvm_present=True,
        kvm_usable=True,
        architecture="x86_64",
    )
    base.update(over)
    return HostMetrics(**base)  # type: ignore[arg-type]


def _claims(*specs, unpriced=()) -> GuestClaims:
    return GuestClaims(
        claimed=tuple(GuestClaim(pid=p, vcpus=c, ram_mib=r) for p, c, r in specs),
        unpriced=tuple(unpriced),
        scanned=len(specs) + len(unpriced),
    )


def _host(claims):
    return host_observation_from_metrics(
        _metrics(), fleet_id="f", resource_id="host:f", ttl_s=TTL,
        authority_epoch=EPOCH, guest_claims=claims,
    )


def _slots(host, vcpus=2, ram=1024):
    from lib.fleet_capacity import HostDemand

    return new_vm_slots(
        host,
        demand=HostDemand(vcpus=vcpus, ram_mib=ram, disk_mib=1024),
        now=NOW,
        authority_epoch=EPOCH,
    )


class TestMeasuredClaimsYieldSlots:
    def test_two_guests_holding_4_vcpus_leave_real_headroom(self):
        """8 vCPU host, 4 committed -> 4 free -> 2 slots at 2 vCPU each."""
        host = _host(_claims((1, 2, 1024), (2, 2, 1024)))
        assert host.cpu_committed == 4.0
        assert host.unknown_dimensions == frozenset()
        verdict = _slots(host)
        assert verdict.state is State.NEW_VM_SLOTS_AVAILABLE
        assert verdict.slots == 2

    def test_cpu_commitment_is_reported_in_the_verdict_headroom(self):
        host = _host(_claims((1, 6, 1024)))
        verdict = _slots(host)
        assert verdict.headroom["cpu"] == 2  # 8 total - 6 committed

    def test_a_fully_committed_host_offers_no_slots(self):
        host = _host(_claims((1, 8, 1024)))
        verdict = _slots(host)
        assert verdict.slots == 0
        assert Reason.CPU_INSUFFICIENT in verdict.reasons


class TestFailClosed:
    def test_an_unpriceable_guest_makes_headroom_unknown(self):
        """Pricing an unreadable guest at zero is the dangerous direction."""
        host = _host(_claims((1, 2, 1024), unpriced=(99,)))
        assert "cpu_committed" in host.unknown_dimensions
        assert _slots(host).state is State.UNKNOWN

    def test_no_measurement_at_all_keeps_the_old_behaviour(self):
        host = _host(None)
        assert host.cpu_committed == 0.0
        assert host.unknown_dimensions == UNMEASURED_HOST_DIMENSIONS
        assert _slots(host).state is State.UNKNOWN

    def test_kvm_unavailable_still_refuses_even_with_good_claims(self):
        host = host_observation_from_metrics(
            _metrics(kvm_usable=False), fleet_id="f", resource_id="host:f", ttl_s=TTL,
            authority_epoch=EPOCH, guest_claims=_claims((1, 2, 1024)),
        )
        verdict = _slots(host)
        assert verdict.state is State.REFUSED
        assert Reason.KVM_UNAVAILABLE in verdict.reasons


class TestLiveMeasurementOnThisHost:
    def test_real_proc_lookup_is_readable_and_consistent(self):
        """The premise of the whole change: /proc cmdlines ARE readable.

        If this ever fails, `cpu_committed` must go back to unmeasured rather
        than silently reading zero guests on a busy machine.
        """
        from lib.qemu_claims import measure_guest_claims

        claims = measure_guest_claims()
        assert claims.scanned >= 0
        # Every guest found must either be priced or named as unpriced — never
        # silently dropped.
        assert len(claims.claimed) + len(claims.unpriced) == claims.scanned
