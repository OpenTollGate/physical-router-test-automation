"""Fleet throughput benchmark: serial vs parallel vs oversubscribed.

Audit gate 8, the card's headline deliverable. It answers one question with
numbers instead of adjectives: **does admitting two lanes finish the same work
sooner, and what does it cost?**

Three configurations, identical workload (``lib.qemu_lane.PROBE_COMMANDS``):

``serial``         one lane, N jobs back to back.
``parallel``       two lanes, N jobs split across them, admitted by the host
                   authority, grouped by conflict domain.
``oversubscribed`` more concurrent lanes than the authority's budget allows.
                   The authority must REFUSE the extra lanes and QUEUE them;
                   the runs that do proceed must still be one-lane-at-a-time
                   when the budget says so. This is a correctness measurement,
                   not a throughput one: oversubscription is expected to be
                   *slower* per job and must never exceed the admitted budget.

Refusal is the default. ``--max-ram-mib``/``--max-load`` are not tuning knobs to
be raised until the run passes: the admission gate reads the live host and
refuses when the box is already busy with somebody else's work (this host
routinely runs at load 20+ with foreign QEMU guests). A refused run prints the
measured numbers and exits 3 — never a smaller workload dressed up as a result.

Usage:

    python3 scripts/fleet-bench.py --config serial      --jobs 3 --out results/fleet-bench
    python3 scripts/fleet-bench.py --config parallel    --jobs 4 --out results/fleet-bench
    python3 scripts/fleet-bench.py --config oversubscribed --jobs 4 --out results/fleet-bench
    python3 scripts/fleet-bench.py --dry-run            # admission verdict only

Every configuration writes ``<out>/<config>.json`` plus a lane-level event log
whose timestamps prove (or disprove) real overlap.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.fleet_authority import Demand, FleetAuthority, LaneSpec  # noqa: E402
from lib.qemu_lane import LaneConfig, LaneError, QemuLane, lane_plan  # noqa: E402

DEFAULT_BASE_IMAGE = Path(
    os.environ.get("TOLLGATE_QEMU_BASE", os.path.expanduser("~/tollgate-virtual-lab/images/openwrt-base.qcow2"))
)
DEFAULT_WORKDIR = Path(os.environ.get("TOLLGATE_FLEET_BENCH_DIR", os.path.expanduser("~/prta-fleet-bench")))

#: Lanes registered on the benchmark authority. Oversubscription is measured by
#: asking for MORE lanes than this: the authority refuses the extra lane(s)
#: rather than quietly running them, and the admitted ones still do the work.
ADMITTED_LANES = ("qemu-1", "qemu-2")

#: Admission floor per lane. 256 MiB guest + QEMU overhead + the page cache the
#: image and overlay need; measured, not guessed (see PROGRESS-t_2d4a7a54.md).
RAM_PER_LANE_MIB = 384
#: Never spend the last gigabyte of a shared box on a benchmark.
RAM_RESERVE_MIB = 1024
DISK_PER_LANE_MIB = 1024
#: Swap already in use is the strongest signal that this host is out of room;
#: PRTA issue #239 is a log of guests freezing mid-provisioning under swap.
MAX_SWAP_PCT = 25
#: Load average above the core count means we would be measuring contention,
#: not lanes.
MAX_LOAD_FACTOR = 1.0


# --------------------------------------------------------------------------- #
# admission: measured, refusable, never silently relaxed
# --------------------------------------------------------------------------- #
def host_metrics() -> dict[str, Any]:
    info: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        try:
            info[key] = int(rest.strip().split()[0]) // 1024  # MiB
        except (ValueError, IndexError):
            continue
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used_pct = 0.0 if not swap_total else 100.0 * (swap_total - swap_free) / swap_total
    load1 = os.getloadavg()[0]
    try:
        stat = os.statvfs(str(Path.home()))
        free_mib = stat.f_bavail * stat.f_frsize // (1024 * 1024)
    except OSError:
        free_mib = 0
    return {
        "cores": os.cpu_count() or 1,
        "load1": round(load1, 2),
        "mem_total_mib": info.get("MemTotal", 0),
        "mem_available_mib": info.get("MemAvailable", 0),
        "swap_total_mib": swap_total,
        "swap_used_pct": round(swap_used_pct, 1),
        "home_free_mib": free_mib,
        "kvm": Path("/dev/kvm").exists(),
        "qemu_processes": _foreign_qemu_pids(),
    }


def _foreign_qemu_pids() -> list[int]:
    pids: list[int] = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if "qemu-system" in cmdline and int(entry.name) != os.getpid():
                pids.append(int(entry.name))
    except OSError:
        pass
    return pids


def admission(lanes: int, *, metrics: dict[str, Any] | None = None) -> tuple[bool, str, dict[str, Any]]:
    """Decide whether it is safe to run ``lanes`` QEMU lanes right now."""
    m = metrics or host_metrics()
    need_ram = lanes * RAM_PER_LANE_MIB + RAM_RESERVE_MIB
    need_disk = lanes * DISK_PER_LANE_MIB
    refusals: list[str] = []

    if not m["kvm"]:
        refusals.append("/dev/kvm missing — TCG fallback would change the measurement")
    if m["mem_available_mib"] < need_ram:
        refusals.append(
            f"MemAvailable {m['mem_available_mib']} MiB < required {need_ram} MiB "
            f"({lanes} lane(s) x {RAM_PER_LANE_MIB} + {RAM_RESERVE_MIB} reserve)"
        )
    if m["swap_used_pct"] > MAX_SWAP_PCT:
        refusals.append(f"swap already {m['swap_used_pct']}% used (limit {MAX_SWAP_PCT}%)")
    if m["load1"] > MAX_LOAD_FACTOR * m["cores"]:
        refusals.append(f"load1 {m['load1']} > {MAX_LOAD_FACTOR} x {m['cores']} cores — the box is busy")
    if m["home_free_mib"] < need_disk:
        refusals.append(f"home free {m['home_free_mib']} MiB < required {need_disk} MiB")
    if m["qemu_processes"]:
        refusals.append(f"foreign QEMU processes already running: {m['qemu_processes']}")

    verdict = {
        "lanes": lanes,
        "required_ram_mib": need_ram,
        "required_disk_mib": need_disk,
        "metrics": m,
        "refusals": refusals,
    }
    if refusals:
        return False, "; ".join(refusals), verdict
    return True, "", verdict


# --------------------------------------------------------------------------- #
# measurement
# --------------------------------------------------------------------------- #
class Bench:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.out = Path(args.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.workdir = Path(args.workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.state_dir = self.out / "authority"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # -- authority ---------------------------------------------------------
    def authority(self, config: str) -> FleetAuthority:
        """A per-configuration authority storing the HOST as one lane.

        The host is the resource that runs out: CPU and RAM are one pool, so the
        lane registered here is ``qemu-host`` with ``max_concurrent`` slots and
        the whole pool's budget. That is what makes the oversubscribed
        configuration meaningful — the third concurrent claim is refused by the
        same code path a real runner would use, not by an ``if`` in this file.
        """
        state = self.out / "authority" / config
        state.mkdir(parents=True, exist_ok=True)
        auth = FleetAuthority(state / "fleet-authority.db")
        auth.register_lane(
            LaneSpec(
                lane_id=HOST_LANE,
                fleet_id="local-qemu",
                resource_id="local-qemu/host",
                vcpus_total=max(1, self.args.cores_per_lane) * self.args.max_concurrent,
                ram_mib_total=self.args.mem_mib * self.args.max_concurrent,
                ram_mib_reserve=0,
                disk_mib_total=DISK_PER_LANE_MIB * self.args.max_concurrent,
                inodes_total=1_000_000,
                domains=(),
                max_concurrent=self.args.max_concurrent,
            )
        )
        return auth

    def lanes(self, count: int) -> list[LaneConfig]:
        ids = [f"qemu-{i + 1}" for i in range(count)]
        return lane_plan(
            workdir=self.workdir,
            base_image=Path(self.args.base_image),
            lane_ids=ids,
            mem_mib=self.args.mem_mib,
            vcpus=max(1, self.args.cores_per_lane),
        )

    # -- one configuration -------------------------------------------------
    def run(self, config: str, lanes: int, jobs: int) -> dict[str, Any]:
        auth = self.authority(config)
        auth.reconcile()
        cfgs = self.lanes(lanes)
        qemus = [QemuLane(c) for c in cfgs]
        wall0 = time.time()
        peak = {"ram_rss_mib": 0, "cpu_pct": 0.0}
        results: list[dict[str, Any]] = []
        cleanup_s: list[float] = []
        failures = 0
        boot_all = 0.0
        wall = 0.0
        jobs_done = 0
        admitted: list[tuple[Any, Any, Any]] = []
        refused: list[dict[str, Any]] = []
        try:
            for q in qemus:
                q.start()
            boot_all = time.time() - wall0

            demand = Demand(
                vcpus=max(1, self.args.cores_per_lane),
                ram_mib=self.args.mem_mib,
                disk_mib=DISK_PER_LANE_MIB,
                inodes=1,
            )
            for q, cfg in zip(qemus, cfgs):
                decision = auth.try_reserve(
                    lane_id=HOST_LANE,
                    demand=demand,
                    run_id=f"{config}-{cfg.lane_id}",
                    domains=(),
                    ttl_s=self.args.ttl_s,
                    owner_pid=os.getpid(),
                )
                if decision.granted:
                    admitted.append((q, cfg, decision))
                else:
                    refused.append(
                        {
                            "lane": cfg.lane_id,
                            "reason": decision.reason,
                            "detail": decision.detail,
                            "queue_position": decision.queue_position,
                        }
                    )
                    q.event("refused", reason=decision.reason, detail=decision.detail)

            # A guest that was not admitted MUST NOT run anything: that is the
            # assertion "no oversubscription beyond the admitted budget".
            for item in refused:
                lane = next(q for q in qemus if q.cfg.lane_id == item["lane"])
                lane.event("idle_not_admitted")
                lane.stop()

            if not admitted:
                raise LaneError(f"authority admitted no lanes: {refused}")

            # One round = at most one job per admitted lane, started together.
            # Serial has one lane, so a round is a single job; parallel has two,
            # and they must genuinely overlap or the comparison is meaningless.
            # Total work is `jobs` in every configuration.
            with ThreadPoolExecutor(max_workers=max(1, len(admitted))) as pool:
                next_job = 0
                while next_job < jobs:
                    round_lanes: list[tuple[Any, Any, int]] = []
                    used: set[str] = set()
                    while next_job < jobs:
                        q, cfg, _ = admitted[next_job % len(admitted)]
                        if cfg.lane_id in used:
                            break
                        used.add(cfg.lane_id)
                        round_lanes.append((q, cfg, next_job))
                        next_job += 1
                    futures = [
                        (cfg, job_idx, pool.submit(q.run_job, timeout=self.args.job_timeout))
                        for q, cfg, job_idx in round_lanes
                    ]
                    for cfg, job_idx, future in futures:
                        try:
                            res = future.result()
                            jobs_done += 1
                            results.append({"lane": cfg.lane_id, "job": job_idx, **res})
                        except LaneError as exc:
                            failures += 1
                            results.append({"lane": cfg.lane_id, "job": job_idx, "error": str(exc)})
                    peak = _peak_usage(qemus, peak)
            wall = time.time() - wall0
        finally:
            for q in qemus:
                cleanup_s.append(q.stop())
            wall_total = time.time() - wall0

        return {
            "config": config,
            "lanes_requested": lanes,
            "lanes_admitted": len(admitted),
            "jobs_requested": jobs,
            "jobs_done": jobs_done,
            "failures": failures,
            "boot_all_s": round(boot_all, 2),
            "wall_s": round(wall, 2),
            "wall_including_cleanup_s": round(wall_total, 2),
            "cleanup_s": [round(c, 3) for c in cleanup_s],
            "jobs_per_hour": round(jobs_done / wall * 3600, 2) if wall > 0 else 0.0,
            "peak": peak,
            "overlap": _overlap(results, admitted),
            "results": results,
            "authority_headroom": {
                cfg.lane_id: dict(auth.headroom(cfg.lane_id).free) for _, cfg, _ in admitted
            },
        }


def _peak_usage(qemus: list[QemuLane], prev: dict[str, float]) -> dict[str, float]:
    """Peak RESIDENT memory of the guest processes themselves.

    Measured from each child's ``VmHWM`` (high-water mark) in ``/proc``. The
    parent's ``ru_maxrss`` would be the Python harness's own footprint, which is
    not what a lane costs and would understate it by an order of magnitude.
    """
    total_mib = 0.0
    for q in qemus:
        pid = q.pid
        if pid is None:
            continue
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except OSError:
            continue
        for line in status.splitlines():
            if line.startswith("VmHWM:"):
                total_mib += int(line.split()[1]) / 1024.0
    return {
        "ram_rss_mib": round(max(prev["ram_rss_mib"], total_mib), 1),
        "cpu_pct": prev["cpu_pct"],
    }


def _overlap(results: list[dict[str, Any]], admitted: list) -> dict[str, Any]:
    """Prove (or disprove) that two lanes were actually running at once."""
    spans = [(r["lane"], r["start"], r["end"]) for r in results if "error" not in r]
    overlaps = 0
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            if spans[i][0] == spans[j][0]:
                continue
            a0, a1 = spans[i][1], spans[i][2]
            b0, b1 = spans[j][1], spans[j][2]
            if min(a1, b1) > max(a0, b0):
                overlaps += 1
    return {"cross_lane_overlapping_job_pairs": overlaps, "lanes": len(admitted)}


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", choices=("serial", "parallel", "oversubscribed"), default="serial")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--out", default=str(Path("results") / "fleet-bench"))
    parser.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    parser.add_argument("--base-image", default=str(DEFAULT_BASE_IMAGE))
    parser.add_argument("--mem-mib", type=int, default=256)
    parser.add_argument("--cores-per-lane", type=int, default=1)
    parser.add_argument("--job-timeout", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true", help="admission verdict only, no guest")
    args = parser.parse_args(argv)

    lanes = {"serial": 1, "parallel": 2, "oversubscribed": 2}[args.config]
    ok, why, verdict = admission(lanes)
    print(json.dumps({"admission": verdict, "granted": ok, "reason": why}, indent=2))
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / f"admission-{args.config}.json").write_text(
        json.dumps({"granted": ok, "reason": why, **verdict}, indent=2) + "\n"
    )
    if not ok:
        print(f"\nREFUSED: {why}", file=sys.stderr)
        return 3
    if args.dry_run:
        return 0

    bench = Bench(args)
    record = bench.run(args.config, lanes, args.jobs)
    record["admission"] = verdict
    dest = Path(args.out) / f"{args.config}.json"
    dest.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k != "results"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
