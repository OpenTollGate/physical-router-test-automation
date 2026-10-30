"""Host-pool regression tests. No guests, adapters or physical access."""
from dataclasses import replace
import multiprocessing

import pytest

from lib.fleet_authority import Demand, FleetAuthority, LaneSpec

DEMAND = Demand(1, 256, 1024, 10)


def _authority(tmp_path, **overrides):
    from lib.fleet_authority import HostSpec
    auth = FleetAuthority(tmp_path / 'authority.db')
    auth.register_host(HostSpec(**dict(dict(vcpus_total=2, ram_mib_total=512,
                                          disk_mib_total=2048, inodes_total=20), **overrides)))
    auth.reconcile()
    for i in range(3):
        auth.register_lane(LaneSpec(f'lane-{i}', f'fleet-{i}', f'resource-{i}',
                                    vcpus_total=10, ram_mib_total=4096,
                                    disk_mib_total=10000, inodes_total=1000))
    return auth


def _admit(auth, i):
    return auth.try_reserve(lane_id=f'lane-{i}', run_id=f'run-{i}', demand=DEMAND)


def test_unknown_host_fails_closed(tmp_path):
    auth = FleetAuthority(tmp_path / 'authority.db')
    auth.register_lane(LaneSpec('lane-0', 'fleet', 'resource'))
    auth.reconcile()
    assert _admit(auth, 0).reason == 'host_unknown'


def test_two_fit_third_refused_and_host_headroom(tmp_path):
    auth = _authority(tmp_path)
    assert _admit(auth, 0).granted
    assert _admit(auth, 1).granted
    assert _admit(auth, 2).reason == 'host_resource_vector_insufficient'
    assert auth.host_headroom().used == {k: 2*v for k, v in DEMAND.as_dict().items()}
    assert all(v == 0 for v in auth.host_headroom().free.values())
    assert auth.headroom('lane-2').free['ram_mib'] == 4096
    assert len(auth.queue()) == 1


@pytest.mark.parametrize('dim', ['vcpus', 'ram_mib', 'disk_mib', 'inodes'])
def test_each_host_dimension_is_binding(tmp_path, dim):
    auth = _authority(tmp_path, **{dim + '_total': getattr(DEMAND, dim)})
    assert _admit(auth, 0).granted
    assert _admit(auth, 1).reason == 'host_resource_vector_insufficient'
    assert len(auth.allocations()) == 1


def test_baseline_reserve_and_refresh_do_not_erase_allocations(tmp_path):
    from lib.fleet_authority import HostSpec
    auth = _authority(tmp_path, ram_mib_total=1024, ram_mib_reserve=256, baseline_ram_mib=256)
    a = _admit(auth, 0).allocation
    auth.activate(a.run_id, a.fence)
    assert auth.host_headroom().free['ram_mib'] == 256
    auth.register_host(HostSpec(1, 256, 1024, 10))
    assert auth.host_headroom().free['ram_mib'] == 0
    assert not _admit(auth, 1).granted


@pytest.mark.parametrize('transition', ['pending', 'active', 'cleanup', 'expire', 'epoch', 'fence'])
def test_all_unclean_states_keep_host_debit(tmp_path, transition):
    auth = _authority(tmp_path)
    a = _admit(auth, 0).allocation
    assert _admit(auth, 1).granted
    if transition == 'active':
        auth.activate(a.run_id, a.fence)
    elif transition == 'cleanup':
        auth.complete(a.run_id, a.fence, clean=False)
        auth.clear_quarantine(a.lane_id)
    elif transition == 'expire':
        auth.expire(now=a.expires_at + 1)
    elif transition == 'epoch':
        auth.invalidate_epoch()
        auth.reconcile()
    elif transition == 'fence':
        auth.fence(a.run_id)
    assert _admit(auth, 2).reason == 'host_resource_vector_insufficient'
    assert auth.host_headroom().used['ram_mib'] == 512


def test_replay_and_clean_release_do_not_double_charge_or_free(tmp_path):
    auth = _authority(tmp_path)
    a = _admit(auth, 0).allocation
    assert _admit(auth, 0).allocation == a
    assert auth.host_headroom().used['ram_mib'] == 256
    auth.complete(a.run_id, a.fence, clean=False)
    auth.clear_cleanup_pending(a.run_id)
    auth.complete(a.run_id, a.fence, clean=True)
    assert auth.host_headroom().used['ram_mib'] == 0
    # Delayed failure/re-fence must not resurrect a released allocation.
    auth.complete(a.run_id, a.fence, clean=False)
    auth.fence(a.run_id)
    assert auth.host_headroom().used['ram_mib'] == 0
    assert not _admit(auth, 0).granted  # run id cannot be reused
    b = _admit(auth, 1).allocation
    auth.abort(b.run_id, b.fence)
    assert auth.host_headroom().used['ram_mib'] == 0


def test_conflict_refusal_does_not_debit_host(tmp_path):
    auth = _authority(tmp_path)
    for i in (0, 1):
        auth.register_lane(LaneSpec(f'lane-{i}', 'fleet', f'resource-{i}', domains=('power',)))
    assert _admit(auth, 0).granted
    assert _admit(auth, 1).reason == 'domain_conflict'
    assert auth.host_headroom().used['ram_mib'] == 256


def _racer(path, i, barrier, output):
    auth = FleetAuthority(path, owner_host=f'contributor-{i}')
    barrier.wait(timeout=15)
    d = _admit(auth, i)
    output.put((d.granted, d.reason))


def test_three_separate_processes_two_host_slots(tmp_path):
    auth = _authority(tmp_path)
    ctx = multiprocessing.get_context('spawn')
    barrier, output = ctx.Barrier(3), ctx.Queue()
    processes = [ctx.Process(target=_racer, args=(auth.path, i, barrier, output)) for i in range(3)]
    try:
        for p in processes:
            p.start()
        results = [output.get(timeout=25) for _ in processes]
        for p in processes:
            p.join(timeout=10)
            assert p.exitcode == 0
    finally:
        for p in processes:
            if p.is_alive():
                p.kill()
                p.join()
        output.close()
    assert sum(granted for granted, _ in results) == 2
    assert [reason for granted, reason in results if not granted] == ['host_resource_vector_insufficient']
    assert len(auth.allocations()) == 2
    assert auth.host_headroom().used['ram_mib'] == 512
