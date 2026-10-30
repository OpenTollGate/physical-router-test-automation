#!/usr/bin/env python3
"""fleet-capacity — read-only operator view of authorized fleet capacity.

This is the CLI half of slice 4 (``lib/fleet_observe.py``). It observes the
fleets an operator's manifest authorizes, then reports four *separate* numbers
per fleet and in total:

  1. reachable fleets (control plane only — proves nothing about hardware),
  2. idle healthy physical routers (needs owner-authorized health telemetry),
  3. idle existing QEMU guests (needs identity + health + topology + lease),
  4. additional VM slots (host admission — reported raw, marked NOT_QUALIFIED).

Number 4 is never a promise. There is no host-wide reservation accounting yet,
so per-lane budgets cannot be summed; the CLI therefore prints a raw headroom
estimate and the word NOT_QUALIFIED next to it, and refuses to say "you may
start N VMs". Nothing here starts a VM, acquires a place, flashes hardware or
enables parallel execution. ``--from-snapshot`` re-decides saved evidence, which
is how an operator watches a stale snapshot decay into refusals without any
probe running at all.

Exit codes:
  0  the report was produced (including "no capacity" — that is a result),
  2  usage/manifest error, 3  evidence refused (e.g. snapshot schema mismatch).

Usage:
    python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml
    python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml --json
    python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml \\
        --guest qemu-lane-a=$HOME/tollgate-virtual-lab/run/tollgate.pid
    python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml \\
        --save-evidence /tmp/evidence.json
    python3 scripts/fleet-capacity.py --manifest ~/fleets.yaml \\
        --from-snapshot /tmp/evidence.json      # no probes at all
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.fleet_capacity import (  # noqa: E402
    CapacityError,
    ManifestError,
    load_manifest,
    redact,
)
from lib.fleet_authority import (  # noqa: E402
    ENV_AUTHORITY_PATH,
    FleetAuthority,
    AuthorityError,
)
from lib.fleet_observe import (  # noqa: E402
    ADMISSION_DETAIL,
    ADMISSION_STATUS,
    Evidence,
    GuestSpec,
    LabgridReadOnlyClient,
    ObservationRefused,
    ProbeLimits,
    build_report,
    collect,
    decide,
    resolve_host_demand,
)


def _parse_guest(spec: str) -> GuestSpec:
    """``NAME=PIDFILE[:START_TICKS]`` — the operator names each guest explicitly."""
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            f"guest {spec!r} must be NAME=PIDFILE (optionally NAME=PIDFILE:START_TICKS)"
        )
    name, _, rest = spec.partition("=")
    name = name.strip()
    rest = rest.strip()
    if not name or not rest:
        raise argparse.ArgumentTypeError(f"guest {spec!r} needs both a place id and a pidfile path")
    start_ticks: int | None = None
    path = rest
    if ":" in rest:
        possible_path, _, tail = rest.rpartition(":")
        if tail.isdigit():
            path = possible_path
            start_ticks = int(tail)
    if not path:
        raise argparse.ArgumentTypeError(f"guest {spec!r} has an empty pidfile path")
    return GuestSpec(
        resource_id=name,
        pidfile=Path(path).expanduser(),
        expected_pid_start_ticks=start_ticks,
    )


def _authority_epoch(path: Path | str | None, explicit: int | None) -> int:
    """The authority's own epoch, never a guess.

    A fresh read of the store's ``meta.epoch`` is what makes old evidence
    refusable. When no store is configured the CLI requires an explicit epoch
    rather than defaulting to 1, because a made-up epoch silently "matches"
    everything it should not.
    """
    if path:
        try:
            return FleetAuthority(path, busy_timeout_ms=5000).epoch
        except (AuthorityError, OSError, ValueError):
            pass
    if explicit is None:
        raise ObservationRefused(
            "no authority epoch available — pass --epoch or configure the host authority store; "
            "old evidence must be refusable, so there is no default epoch"
        )
    return int(explicit)


def _now_iso(now: float) -> str:
    return dt.datetime.fromtimestamp(now, tz=dt.timezone.utc).isoformat(timespec="seconds")


def _render_text(report: dict) -> str:
    out: list[str] = []
    out.append(f"generated: {report['generated_at']}  (evidence age {report['age_s']:.0f}s, ttl {report['ttl_s']:.0f}s)")
    out.append(f"manifest:  {report['manifest']}")
    out.append(f"epoch:     {report['authority_epoch']}  admission: {report['admission']['status']}")
    out.append("")
    out.append("fleet                 coord   exporters  routers  guests  vm-slots(raw)")
    out.append("--------------------  ------  ---------  -------  ------  -------------")
    for fleet in report["fleets"]:
        coord = "up" if fleet["coordinator_reachable"] else "down"
        slots = fleet["new_vm_slots_raw"]
        out.append(
            f"{fleet['fleet_id'][:20]:20}  {coord:6}  "
            f"{fleet['exported_available']:9}  {fleet['idle_physical_routers']:7}  "
            f"{fleet['idle_existing_guests']:6}  {slots:13}"
        )
    totals = report["totals"]
    out.append("")
    out.append(
        f"totals: {totals['reachable_fleets']}/{totals['configured_fleets']} fleets reachable, "
        f"{totals['idle_physical_routers']} idle physical router(s), "
        f"{totals['idle_existing_guests']} idle existing guest(s), "
        f"{totals['exported_available']} exported-but-not-proven-idle"
    )
    out.append(
        f"new VM slots: {totals['new_vm_slots_raw']} raw / {ADMISSION_STATUS} "
        "— not allocatable (see below)"
    )
    out.append("")
    out.append(f"admission {report['admission']['status']}: {report['admission']['detail']}")
    demand = report["declared_lane_demand"]
    out.append(
        "declared lane demand (a request, not a measurement): "
        f"{demand['vcpus']} vCPU, {demand['ram_mib']} MiB RAM, "
        f"{demand['disk_mib']} MiB disk, {demand['disk_inodes']} inode(s)"
    )

    for fleet in report["fleets"]:
        out.append("")
        out.append(f"[{fleet['fleet_id']}]")
        for line in fleet["coordinator_status"]:
            out.append(f"  coordinator: {line}")
        for entry in fleet["host_admission"]:
            headroom = ", ".join(f"{k}={v:.0f}" for k, v in sorted(entry["raw_headroom"].items()))
            reasons = ",".join(entry["reasons"]) or "-"
            out.append(f"  host admission: {entry['state']} slots={entry['slots']} ({reasons})")
            if headroom:
                out.append(f"    raw headroom: {headroom}")
        if fleet["host"]:
            host = fleet["host"]
            out.append(
                "  host metrics (this machine, measured now): "
                f"cpu={host['cpu_total']} load1={host['load1']:.2f} "
                f"ram_free={host['ram_available_mib']}MiB/{host['ram_total_mib']}MiB "
                f"swap_used={host['swap_used_mib']}MiB "
                f"disk_free={host['disk_free_mib']}MiB inodes_free={host['inode_free']} "
                f"kvm={host['kvm_usable']} arch={host['architecture'] or 'unknown'}"
            )
            if host["detail"]:
                out.append(f"    note: {host['detail']}")
        elif fleet["host_admission"]:
            out.append("  host metrics: not measured (host controller is not this host)")
        excluded = fleet["excluded"]
        if excluded:
            out.append(f"  excluded ({len(excluded)}):")
            for entry in excluded:
                reasons = ",".join(entry["reasons"]) or "-"
                detail = f" — {entry['detail']}" if entry["detail"] else ""
                out.append(f"    {entry['resource_id']}: {entry['state']} ({reasons}){detail}")

    reservations = report["reservations"]
    out.append("")
    if reservations["outstanding"]:
        out.append(f"outstanding reservations ({reservations['source']}):")
        for item in reservations["outstanding"]:
            demand = item["demand"]
            state = "started" if item["started"] else "pending"
            out.append(
                f"  {item['run_id']} {state} "
                f"{demand['vcpus']}vCPU/{demand['ram_mib']}MiB/{demand['disk_mib']}MiB "
                f"expires={_now_iso(item['expires_at'])}"
            )
    else:
        out.append(f"outstanding reservations: none readable ({reservations['detail'] or 'empty store'})")

    if report["refusals"]:
        out.append("")
        out.append(f"refusals ({len(report['refusals'])}):")
        for item in report["refusals"]:
            detail = f" — {item['detail']}" if item["detail"] else ""
            out.append(f"  {item['subject']}: {item['reason']}{detail}")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fleet-capacity",
        description=(
            "Read-only capacity view over explicitly authorized fleets. Never acquires, powers, "
            "flashes, starts a VM or enables parallel execution."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=ADMISSION_DETAIL,
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="explicit fleet manifest path (see config/fleets.example.yaml) — there is no default",
    )
    parser.add_argument("--fleet", action="append", default=None, metavar="ID", help="limit to these fleets")
    parser.add_argument("--json", action="store_true", help="machine-readable report on stdout")
    parser.add_argument("--save-evidence", metavar="PATH", help="write the evidence snapshot (redacted)")
    parser.add_argument(
        "--from-snapshot",
        metavar="PATH",
        help="re-decide a saved evidence snapshot instead of probing (no coordinator or host contact)",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="per-probe timeout in seconds (default 10)")
    parser.add_argument("--ttl", type=float, default=60.0, help="evidence freshness budget in seconds (default 60)")
    parser.add_argument(
        "--epoch",
        type=int,
        default=None,
        help="authority epoch (read from the authority store when one is configured)",
    )
    parser.add_argument(
        "--authority",
        default=os.environ.get(ENV_AUTHORITY_PATH, ""),
        help=f"host authority store path (env {ENV_AUTHORITY_PATH})",
    )
    parser.add_argument("--disk-path", default="/", help="filesystem to measure for disk/inodes (default /)")
    parser.add_argument(
        "--ram-reserve-mib",
        type=int,
        default=0,
        help="operator-declared RAM safety reserve, subtracted from headroom (default 0)",
    )
    parser.add_argument(
        "--guest",
        action="append",
        type=_parse_guest,
        default=None,
        metavar="NAME=PIDFILE[:TICKS]",
        help="declare an existing QEMU guest lane by pidfile (repeatable; never started here)",
    )
    parser.add_argument("--demand-vcpus", type=int, default=2, help="declared lane vCPU demand (default 2)")
    parser.add_argument("--demand-ram-mib", type=int, default=1024, help="declared lane RAM MiB (default 1024)")
    parser.add_argument("--demand-disk-mib", type=int, default=4096, help="declared lane disk MiB (default 4096)")
    parser.add_argument("--demand-inodes", type=int, default=1, help="declared lane inode demand (default 1)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"fleet-capacity: manifest refused: {redact(str(exc))}", file=sys.stderr)
        return 2

    demand = resolve_host_demand(
        vcpus=args.demand_vcpus,
        ram_mib=args.demand_ram_mib,
        disk_mib=args.demand_disk_mib,
        disk_inodes=args.demand_inodes,
    )
    authority_path = args.authority.strip() or None
    try:
        if args.from_snapshot:
            doc = json.loads(Path(args.from_snapshot).expanduser().read_text(encoding="utf-8"))
            evidence = Evidence.from_dict(doc)
            now = time.time()
            if args.epoch is not None and int(args.epoch) != evidence.authority_epoch:
                raise ObservationRefused(
                    f"snapshot epoch {evidence.authority_epoch} does not match --epoch {args.epoch} — "
                    "refusing to reinterpret old evidence under a new authority epoch"
                )
        else:
            limits = ProbeLimits(timeout_s=args.timeout, ttl_s=args.ttl)
            epoch = _authority_epoch(authority_path, args.epoch)
            now = time.time()
            evidence = collect(
                manifest,
                limits=limits,
                authority_epoch=epoch,
                now=now,
                client=LabgridReadOnlyClient(limits),
                fleets=args.fleet,
                disk_path=args.disk_path,
                ram_reserve_mib=args.ram_reserve_mib,
                guests=args.guest or (),
                authority_path=authority_path,
            )
    except ObservationRefused as exc:
        print(f"fleet-capacity: refused: {redact(str(exc))}", file=sys.stderr)
        return 3
    except (CapacityError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"fleet-capacity: cannot produce a report: {redact(str(exc))}", file=sys.stderr)
        return 3

    if args.save_evidence:
        target = Path(args.save_evidence).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(evidence.to_dict(), indent=2, sort_keys=True)
        target.write_text(redact(payload) + "\n", encoding="utf-8")
        if not args.json:
            print(f"evidence saved: {target}")

    verdicts = decide(manifest, evidence, now=now, demand=demand)
    report = build_report(
        manifest,
        evidence,
        verdicts,
        generated_at=_now_iso(now),
        now=now,
        demand=demand,
        admission_status=ADMISSION_STATUS,
    )
    if args.json:
        print(redact(json.dumps(report, indent=2, sort_keys=True)))
    else:
        print(_render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
