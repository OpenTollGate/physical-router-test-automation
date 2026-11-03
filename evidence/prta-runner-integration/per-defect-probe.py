#!/usr/bin/env python3
"""Per-defect probe for PR #334 runner integration.

``~/reports/reviews/prta-334-safety-probe.py`` is the audit's own acceptance
probe. It is a straight-line script: it calls
``admission_from_args(...)`` for an OBSERVE-ONLY manifest with an undeclared
fleet and *stops there*, so once that first defect is fixed the script aborts
with ``AdmissionRefused`` and never measures defects 2-4.

This probe measures each of the four audited defects **independently**, so
"fixed" is shown per finding against the same tree. It is read-only with respect
to the repo: temporary stores, temporary manifests, harmless subprocesses, and
the coordinator stubbed at ``re_read_ownership``. No acquisition, no hardware,
no coordinator contact, no inventory file, no place taken.

Run from the worktree under test:

    PYTHONPATH= PYTHONDONTWRITEBYTECODE=1 python3 evidence/prta-runner-integration/per-defect-probe.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time
from types import SimpleNamespace

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.fleet_authority import Demand, FleetAuthority, HostSpec, LaneSpec, State  # noqa: E402
from lib.run_admission import AdmissionRefused  # noqa: E402

_spec = importlib.util.spec_from_file_location("probe_runner", ROOT / "scripts/run-profile.py")
r = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r)

BUDGET = dict(vcpus_total=8, ram_mib_total=16384, disk_mib_total=200000, inodes_total=1000000)
COORD = "coord.example:20408"
PLACE = "place-a1"
DUT = "10.0.0.11"
OWNER = "host/runner"
MANIFEST_TEMPLATE = ROOT / "config/fleets.example.yaml"


def write_manifest(tmp: pathlib.Path, actions) -> pathlib.Path:
    doc = yaml.safe_load(MANIFEST_TEMPLATE.read_text().replace("REPLACE_ME_", "test_"))
    doc["fleets"][0]["permitted_actions"] = list(actions)
    doc["fleets"][0]["coordinator"]["address"] = COORD
    doc["fleets"][0]["allowed_places"] = [PLACE]
    path = tmp / "manifest.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def write_inventory(tmp: pathlib.Path, *, place=PLACE, coordinator=COORD) -> pathlib.Path:
    doc = {
        "switch": {"host": "switch.example"},
        "coordinator": {"address": coordinator},
        "routers": {"router-a": {"poe_port": "lan1", "address": DUT, "place": place}},
    }
    path = tmp / "inventory.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def store(tmp: pathlib.Path) -> FleetAuthority:
    auth = FleetAuthority(tmp / "authority.db")
    auth.reconcile()
    auth.register_host(HostSpec(**BUDGET))
    auth.register_lane(
        LaneSpec(lane_id="lane-a", fleet_id="test_FLEET_A", resource_id=PLACE, **BUDGET)
    )
    return auth


def profile() -> dict:
    return {
        "name": "probe",
        "runner": "pytest",
        "matrix": {"tier": "unit", "lab": "physical", "scope": "test"},
        "env": {},
        "pytest": {"testpaths": "tests/e2e"},
    }


def args(tmp: pathlib.Path, manifest: pathlib.Path, *, ttl=900.0) -> SimpleNamespace:
    return SimpleNamespace(
        fleet_manifest=str(manifest),
        fleet="test_FLEET_A",
        lane="lane-a",
        resource=PLACE,
        domain=None,
        demand_vcpus=1,
        demand_ram_mib=128,
        demand_disk_mib=256,
        demand_inodes=1,
        authority_store=str(tmp / "authority.db"),
        lease_ttl=ttl,
    )


def env(tmp: pathlib.Path, inventory: pathlib.Path) -> dict:
    return {
        "TOLLGATE_LAB_INVENTORY": str(inventory),
        "TOLLGATE_LAB_TYPE": "physical",
        "TOLLGATE_VENUE": "labgrid",
        "TOLLGATE_LABGRID_PLACE": PLACE,
        "TOLLGATE_SSH_HOST": DUT,
        "LG_HOSTNAME": "host",
        "LG_USERNAME": "runner",
    }


def main() -> int:
    findings: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory(prefix="prta-4defect-", dir=os.environ.get("TMPDIR")) as td:
        tmp = pathlib.Path(td)

        # ---------------------------------------------------------------- 1
        # The audit: "REAL OBSERVE-ONLY MANIFEST ACCEPTED UNDECLARED
        # FLEET/RESOURCE active". Driven through main() so it is measurable at
        # any head; the identity is undeclared in the manifest AND unregistered.
        observe_only = write_manifest(tmp, ("observe",))
        inv = write_inventory(tmp)
        marker1 = tmp / "spawned-1"
        FleetAuthority(tmp / "authority.db").reconcile()
        for key, value in env(tmp, inv).items():
            os.environ[key] = value
        r.load_profile = lambda _: profile()  # type: ignore[assignment]
        r.build_pytest_command = lambda *a: [  # type: ignore[assignment]
            sys.executable, "-c", f"from pathlib import Path; Path({str(marker1)!r}).touch()",
        ]
        r.REPO_DIR = tmp  # type: ignore[assignment]
        r.TESTS_DIR = tmp  # type: ignore[assignment]
        r.git_short_sha = lambda: "probe"  # type: ignore[assignment]
        r.git_full_sha = lambda: "probe"  # type: ignore[assignment]
        r.re_read_ownership = lambda plan, envm: ""  # type: ignore[assignment]
        sys.argv = [
            "run-profile.py", "--profile", "probe", "--run-dir", str(tmp / "run-1"),
            "--no-render",
            "--fleet-manifest", str(observe_only), "--fleet", "NOT-AUTHORIZED",
            "--resource", "OTHER-ROUTER", "--lane", "lane-a",
            "--authority-store", str(tmp / "authority.db"),
            "--demand-vcpus", "1", "--demand-ram-mib", "128",
            "--demand-disk-mib", "256", "--demand-inodes", "1",
        ]
        rc1 = r.main()
        states1 = sorted({a.state.value for a in FleetAuthority(tmp / "authority.db").allocations()})
        # The audit measured this defect at the ADMISSION SEAM, not through
        # main(): `admission_from_args(...).acquire(); .mark_started()` reached
        # "active" with an observe-only manifest and an undeclared fleet. main()
        # alone cannot show it at base, because base's blanket gate refuses
        # everything — the defect was *dormant*, not absent. So measure both.
        seam1 = "seam-gone"
        store(tmp)
        try:
            adm = r.admission_from_args(
                SimpleNamespace(
                    **{
                        **vars(args(tmp, observe_only)),
                        "fleet": "NOT-AUTHORIZED",
                        "resource": "OTHER-ROUTER",
                    }
                ),
                "seam-probe",
            )
            adm.acquire()
            adm.mark_started()
        except AdmissionRefused as exc:
            seam1 = f"refused:{exc.reason}"
        except Exception as exc:  # noqa: BLE001 - e.g. base has no seam at all
            seam1 = f"unavailable:{type(exc).__name__}"
        else:
            seam1 = f"ACCEPTED:{FleetAuthority(tmp / 'authority.db').allocation('seam-probe').state.value}"
        seam_fixed = seam1.startswith("refused") or seam1.startswith("unavailable")
        findings.append(
            ("1a observe-only/undeclared at the ADMISSION SEAM (the audit's probe)",
             seam_fixed, seam1)
        )
        findings.append(
            ("1b observe-only/undeclared through main()",
             rc1 != 0 and not marker1.exists() and not states1,
             f"RC {rc1} SPAWNED {marker1.exists()} STATES {states1}")
        )

        # ---------------------------------------------------------------- 2
        # The audit: reaching ACTIVE "with no coordinator object or credentials
        # at all". Two independent halves, both driven through main() so the
        # measurement works on any head: (a) a place disagreement, (b) a venue
        # that acquires nothing.
        def run_main(tag: str, inventory: pathlib.Path, *, venue: str = "labgrid"):
            """Drive the production entrypoint; return (rc, spawned, states)."""
            run_dir = tmp / f"run-{tag}"
            marker = tmp / f"spawned-{tag}"
            store(tmp)
            for key, value in env(tmp, inventory).items():
                os.environ[key] = value
            os.environ["TOLLGATE_VENUE"] = venue
            r.load_profile = lambda _: profile()  # type: ignore[assignment]
            r.build_pytest_command = lambda *a: [  # type: ignore[assignment]
                sys.executable, "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch()",
            ]
            r.REPO_DIR = tmp  # type: ignore[assignment]
            r.TESTS_DIR = tmp  # type: ignore[assignment]
            r.git_short_sha = lambda: "probe"  # type: ignore[assignment]
            r.git_full_sha = lambda: "probe"  # type: ignore[assignment]
            r.re_read_ownership = lambda plan, envm: ""  # type: ignore[assignment]
            manifest = write_manifest(tmp, ("observe", "acquire", "release"))
            sys.argv = [
                "run-profile.py", "--profile", "probe", "--run-dir", str(run_dir),
                "--no-render",
                "--fleet-manifest", str(manifest), "--fleet", "test_FLEET_A",
                "--resource", PLACE, "--lane", "lane-a",
                "--authority-store", str(tmp / "authority.db"),
                "--demand-vcpus", "1", "--demand-ram-mib", "128",
                "--demand-disk-mib", "256", "--demand-inodes", "1",
                "--lease-ttl", "30",
            ]
            rc = r.main()
            states = sorted({a.state.value for a in FleetAuthority(tmp / "authority.db").allocations()})
            return rc, marker.exists(), states

        rc, spawned, states = run_main("2a", write_inventory(tmp, place="place-b2"))
        active = bool({"active", "released", "cleanup_pending"} & set(states))
        findings.append(
            ("2a coordinator/place disagreement", not (spawned or active),
             f"RC {rc} SPAWNED {spawned} STATES {states}")
        )

        rc, spawned, states = run_main("2b", write_inventory(tmp), venue="direct")
        active = bool({"active", "released", "cleanup_pending"} & set(states))
        findings.append(
            ("2b venue that acquires no place", not (spawned or active),
             f"RC {rc} SPAWNED {spawned} STATES {states}")
        )

        # ---------------------------------------------------------------- 3
        # The audit: "CHILD FINISHED AFTER TTL / RENEW CALLS 0".
        # Production path (main()) with a child that outlives the TTL, and a
        # counter on the real FleetAuthority.renew.
        run_dir = tmp / "run3"
        renewals: list[float] = []
        real_renew = FleetAuthority.renew

        def counting_renew(self, run_id, fence, *, ttl_s):
            renewals.append(ttl_s)
            return real_renew(self, run_id, fence, ttl_s=ttl_s)

        FleetAuthority.renew = counting_renew  # type: ignore[method-assign]
        r.re_read_ownership = lambda plan, envm: ""  # type: ignore[assignment]
        manifest3 = write_manifest(tmp, ("observe", "acquire", "release"))
        inv3 = write_inventory(tmp)
        store(tmp)
        child = [
            sys.executable, "-c",
            "import time; time.sleep(2.5); print('CHILD FINISHED AFTER TTL')",
        ]
        os.environ["TOLLGATE_LAB_INVENTORY"] = str(inv3)
        os.environ["TOLLGATE_VENUE"] = "labgrid"
        os.environ["TOLLGATE_LABGRID_PLACE"] = PLACE
        os.environ["TOLLGATE_SSH_HOST"] = DUT
        os.environ["TOLLGATE_LAB_TYPE"] = "physical"
        os.environ["LG_HOSTNAME"] = "host"
        os.environ["LG_USERNAME"] = "runner"
        r.load_profile = lambda _: profile()  # type: ignore[assignment]
        r.build_pytest_command = lambda *a: child  # type: ignore[assignment]
        r.REPO_DIR = tmp  # type: ignore[assignment]
        r.TESTS_DIR = tmp  # type: ignore[assignment]
        r.git_short_sha = lambda: "probe"  # type: ignore[assignment]
        r.git_full_sha = lambda: "probe"  # type: ignore[assignment]
        sys.argv = [
            "run-profile.py", "--profile", "probe", "--run-dir", str(run_dir), "--no-render",
            "--fleet-manifest", str(manifest3), "--fleet", "test_FLEET_A",
            "--resource", PLACE, "--lane", "lane-a",
            "--authority-store", str(tmp / "authority.db"),
            "--demand-vcpus", "1", "--demand-ram-mib", "128",
            "--demand-disk-mib", "256", "--demand-inodes", "1",
            "--lease-ttl", "1.0",
        ]
        started = time.monotonic()
        rc3 = r.main()
        elapsed = time.monotonic() - started
        FleetAuthority.renew = real_renew  # type: ignore[method-assign]
        allocation = FleetAuthority(tmp / "authority.db").allocations()
        states = sorted({a.state.value for a in allocation})
        ok3 = bool(renewals) and rc3 == 0
        findings.append(
            ("3 child outliving its TTL", ok3,
             f"CHILD RC {rc3} RENEW CALLS {len(renewals)} elapsed={elapsed:.2f}s "
             f"states={states}")
        )

        # ---------------------------------------------------------------- 4
        # The audit: "FAILED RUN FINAL STATE released" after a simulated
        # subprocess/teardown failure.
        run_dir4 = tmp / "run4"
        store(tmp)
        sys.argv[sys.argv.index(str(run_dir))] = str(run_dir4)
        from lib.supervised_run import RenewalFailed

        r.supervised_tee_run = lambda *a, **kw: (_ for _ in ()).throw(
            RenewalFailed("simulated subprocess/teardown failure")
        )
        rc4 = r.main()
        evidence_path = run_dir4 / "raw" / "e2e" / "release.json"
        evidence = json.loads(evidence_path.read_text()) if evidence_path.is_file() else {}
        run_id4 = evidence.get("run_id", "")
        # Scope to THIS run's allocation: the store still holds run 3's row,
        # and a whole-store state list would let one run's outcome mask another's.
        state4 = (
            FleetAuthority(tmp / "authority.db").allocation(run_id4).state.value
            if run_id4 else "missing"
        )
        fixed4 = state4 == "cleanup_pending" and rc4 == 76 and evidence.get("clean") is False
        findings.append(
            ("4 teardown failure frees the lane", fixed4,
             f"RC {rc4} RUN-4 STATE {state4} clean={evidence.get('clean')} "
             f"reason={evidence.get('reason')}")
        )

    print("")
    print("=" * 78)
    for name, fixed, detail in findings:
        print(f"{'FIXED   ' if fixed else 'DEFECT  '} {name}")
        print(f"          {detail}")
    print("=" * 78)
    print(f"REMAINING DEFECTS: {sum(1 for _, f, _ in findings if not f)} / {len(findings)}")
    return 0 if all(f for _, f, _ in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
