import os, sys, time, sqlite3, importlib
sys.path.insert(0, ".")
from lib.fleet_authority import FleetAuthority, HostSpec, LaneSpec, Demand, State
import lib.supervised_run as m

d = os.environ["PROBE_DIR"]

DB = {"n": 0}
def auth():
    DB["n"] += 1
    a = FleetAuthority(f"{d}/a{DB['n']}.db"); a.reconcile()
    a.register_host(HostSpec(vcpus_total=4, ram_mib_total=8192, disk_mib_total=65536, inodes_total=100_000))
    a.register_lane(LaneSpec(lane_id="lane-a", fleet_id="fleet-a", resource_id="router-a",
                             vcpus_total=4, ram_mib_total=8192, disk_mib_total=65536, max_concurrent=1))
    return a

# R3/R4: heartbeat + fatal renewal failure + fencing, measured
a = auth()
dec = a.try_reserve(lane_id="lane-a", demand=Demand(2,1024,4096,1), run_id="r1", ttl_s=0.3)
a.activate("r1", dec.allocation.fence)
t0 = time.monotonic()
out = m.run_supervised([sys.executable,"-c","import time; time.sleep(1.0)"], cwd=".", env=dict(os.environ),
                       log_path=__import__("pathlib").Path(d+"/o.log"), dry_run=False,
                       authority=a, run_id="r1", fence=dec.allocation.fence, ttl_s=0.3, heartbeat_s=0.05)
print(f"R3 HEARTBEAT: child 1.0s, ttl 0.3s -> renewals={out.renewals} fence_checks={out.fence_checks} "
      f"elapsed={time.monotonic()-t0:.2f}s state={a.allocation('r1').state.value} expired={len(a.expire())}")

a2 = auth(); dec = a2.try_reserve(lane_id="lane-a", demand=Demand(2,1024,4096,1), run_id="r2", ttl_s=0.3)
a2.activate("r2", dec.allocation.fence)
class Bad:
    def renew(self,*a_,**k): raise sqlite3.OperationalError("disk I/O error")
    def allocation(self, r): return a2.allocation(r)
try:
    m.run_supervised([sys.executable,"-c","import time; time.sleep(10)"], cwd=".", env=dict(os.environ),
                     log_path=__import__("pathlib").Path(d+"/o2.log"), dry_run=False,
                     authority=Bad(), run_id="r2", fence=dec.allocation.fence, ttl_s=0.3, heartbeat_s=0.05,
                     terminate_grace_s=1.0)
    print("R4 FAIL: no raise")
except m.RenewalFailed as e:
    print(f"R4 RENEWAL FATAL: {type(e).__name__} exit={e.exit_code} cleanup={e.outcome.cleanup.value} "
          f"terminated={e.outcome.terminated}")

a3 = auth(); dec = a3.try_reserve(lane_id="lane-a", demand=Demand(2,1024,4096,1), run_id="r3", ttl_s=0.6)
a3.activate("r3", dec.allocation.fence)
import threading
threading.Timer(0.15, lambda: a3.fence("r3", note="stolen")).start()
try:
    m.run_supervised([sys.executable,"-c","import time; time.sleep(10)"], cwd=".", env=dict(os.environ),
                     log_path=__import__("pathlib").Path(d+"/o3.log"), dry_run=False,
                     authority=a3, run_id="r3", fence=dec.allocation.fence, ttl_s=0.6, heartbeat_s=0.05,
                     terminate_grace_s=1.0)
    print("FENCE FAIL: no raise")
except m.AuthorityLost as e:
    print(f"R3 FENCE: {type(e).__name__} exit={e.exit_code} cleanup={e.outcome.cleanup.value} "
          f"store_state={a3.allocation('r3').state.value}")

# R5: unconfirmed teardown is reported, not claimed
a4 = auth(); dec = a4.try_reserve(lane_id="lane-a", demand=Demand(2,1024,4096,1), run_id="r4", ttl_s=30)
a4.activate("r4", dec.allocation.fence)
out = m.run_supervised([sys.executable,"-c","print('x')"], cwd=".", env=dict(os.environ),
                       log_path=__import__("pathlib").Path(d+"/o4.log"), dry_run=False,
                       authority=a4, run_id="r4", fence=dec.allocation.fence, ttl_s=30, heartbeat_s=1.0,
                       terminate_grace_s=0.2, group_alive=lambda p: True, kill_group=lambda p,s: None)
print(f"R5 CLEANUP REPORTING: cleanup={out.cleanup.value} confirmed={out.cleanup_confirmed} "
      f"detail={out.cleanup_detail!r}"[:160])
