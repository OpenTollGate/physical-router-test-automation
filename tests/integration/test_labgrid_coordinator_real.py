"""Real (not mocked) labgrid coordinator/exporter integration — audit gate 7.

The unit suite drives ``lib.labgrid_venue`` against a ``FakeCoordinator`` that
*models* the pinned client. This module removes the model: it starts the actual
``labgrid-coordinator`` and ``labgrid-exporter`` binaries, creates real places on
them, and asserts the behaviour our wrapper depends on. Anything the fake got
wrong must show up here.

What it proves (and what it does not):

* resource availability, place creation, acquire/release lifecycle — real RPCs;
* conditional release: a non-owner ``release-from`` prints success and releases
  nothing, so ``release()`` must re-read ownership (it does);
* a real two-process race for one place: exactly one process acquires, the
  loser never touches the winner's claim;
* a lease OUTLIVES the client that took it, so crash cleanup cannot be inferred
  from disconnect;
* native reservations exist in the pinned version (``reserve``,
  ``reservations``, ``cancel-reservation``, ``wait``) and are owner-scoped:
  another identity cannot acquire a place reserved for someone else;
* a coordinator restart reloads places but drops ownership — the lease is lost,
  which is exactly why the authority epoch has to be invalidated on restart
  rather than trusting a TTL.

It does NOT prove anything about physical DUTs, real routers, or the runner
wiring: no QEMU guest and no hardware is involved here.

Missing labgrid is NOT a skip. These tests carry the ``required`` marker, and
the suite-wide gate in ``tests/conftest.py`` turns a skipped required test into
FAILED "NOT VERIFIED" — a lane that never ran cannot report green.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.required,
    pytest.mark.integration,
]

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Names are consistent per test module but unique per test run of this host,
#: so a leftover coordinator from a killed run cannot be mistaken for ours.
PLACE_A = "prta-lane-a"
PLACE_B = "prta-lane-b"
GROUP = "prta-it"


# --------------------------------------------------------------------------- #
# toolchain discovery -- absence is a hard failure, never a skip
# --------------------------------------------------------------------------- #
def _binary(name: str) -> str:
    override = os.environ.get("TOLLGATE_LABGRID_CLIENT", "").strip() if name == "labgrid-client" else ""
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        pytest.fail(
            f"TOLLGATE_LABGRID_CLIENT points at {override!r}, which is not an executable — "
            "this gate requires the real labgrid client and is NOT allowed to pass by skipping",
            pytrace=False,
        )
    found = shutil.which(name)
    if found:
        return found
    sibling = Path(sys.executable).parent / name
    if sibling.is_file():
        return str(sibling)
    pytest.fail(
        f"{name} is not installed — this gate requires the real labgrid package "
        "(pip install labgrid) and is NOT allowed to pass by skipping",
        pytrace=False,
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


from lib.labgrid_venue import ClientIdentity  # noqa: E402  (after sys.path fix-up)


def _wait_for(what: str, check, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        ok, detail = check()
        if ok:
            return
        last = detail
        time.sleep(0.2)
    pytest.fail(f"timed out after {timeout}s waiting for {what}: {last}")


class Coordinator:
    """One real coordinator + one real exporter, in a private directory."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.port = _free_port()
        self.address = f"127.0.0.1:{self.port}"
        self.coordinator: subprocess.Popen | None = None
        self.exporter: subprocess.Popen | None = None
        self._exporter_cfg = workdir / "exporter.yaml"

    # -- process control ---------------------------------------------------
    def start(self) -> None:
        self._exporter_cfg.write_text(
            "prta-lane-a:\n"
            "  NetworkService:\n"
            '    address: "127.0.0.1"\n'
            "    username: root\n"
            "    port: 2222\n"
            "prta-lane-b:\n"
            "  NetworkService:\n"
            '    address: "127.0.0.1"\n'
            "    username: root\n"
            "    port: 2223\n"
        )
        log = open(self.workdir / "coordinator.log", "ab")
        self.coordinator = subprocess.Popen(
            [_binary("labgrid-coordinator"), "-l", self.address],
            cwd=self.workdir,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        _wait_for("coordinator to answer", self._coordinator_up)

        self.exporter = subprocess.Popen(
            [
                _binary("labgrid-exporter"),
                "-c", self.address,
                "-n", GROUP,
                "--hostname", GROUP,
                str(self._exporter_cfg),
            ],
            cwd=self.workdir,
            stdout=open(self.workdir / "exporter.log", "ab"),
            stderr=subprocess.STDOUT,
        )
        _wait_for("exporter to publish its resources", self._exporter_up)

    def stop(self, *, kill: bool = False) -> None:
        for proc in (self.exporter, self.coordinator):
            if proc is None or proc.poll() is not None:
                continue
            proc.send_signal(9 if kill else 15)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        self.exporter = None
        self.coordinator = None

    # -- client plumbing ---------------------------------------------------
    def client(self, *args: str, place: str | None = None, user: str = "probe", host: str = "it-host"):
        cmd = [_binary("labgrid-client"), "-x", self.address]
        if place:
            cmd += ["-p", place]
        cmd += list(args)
        env = {**os.environ, "LG_HOSTNAME": host, "LG_USERNAME": user}
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env, cwd=self.workdir)

    def _coordinator_up(self) -> tuple[bool, str]:
        if self.coordinator is not None and self.coordinator.poll() is not None:
            return False, f"coordinator exited rc={self.coordinator.returncode}"
        proc = self.client("version")
        return proc.returncode == 0, (proc.stderr or proc.stdout).strip()

    def _exporter_up(self) -> tuple[bool, str]:
        if self.exporter is not None and self.exporter.poll() is not None:
            return False, f"exporter exited rc={self.exporter.returncode}"
        proc = self.client("resources")
        return (GROUP in proc.stdout), proc.stdout.strip()

    # -- fixtures the tests need -------------------------------------------
    def create_place(self, name: str, group: str) -> None:
        proc = self.client("create", place=name)
        assert proc.returncode == 0, proc.stderr
        proc = self.client("add-match", f"{GROUP}/{group}/NetworkService", place=name)
        assert proc.returncode == 0, proc.stderr

    def owner(self, place: str) -> str:
        out = self.client("show", place=place).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("acquired:"):
                value = line.split(":", 1)[1].strip()
                return "" if value.lower() in {"none", "null"} else value
        raise AssertionError(f"no owner line in 'show' output:\n{out}")

    def reservation(self, place: str) -> str:
        out = self.client("show", place=place).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("reservation:"):
                value = line.split(":", 1)[1].strip()
                return "" if value.lower() in {"none", "null"} else value
        return ""


@pytest.fixture()
def coord(tmp_path: Path):
    c = Coordinator(tmp_path)
    try:
        c.start()
        c.create_place(PLACE_A, "prta-lane-a")
        c.create_place(PLACE_B, "prta-lane-b")
        yield c
    finally:
        c.stop()


@pytest.fixture()
def bench_factory(coord: Coordinator):
    from lib.labgrid_venue import ClientIdentity, LabgridBench

    def make(place: str, run_id: str) -> LabgridBench:
        return LabgridBench(coord.address, place, identity=ClientIdentity.for_run(run_id, host="it-host"))

    return make


# --------------------------------------------------------------------------- #
# 1. resource availability
# --------------------------------------------------------------------------- #
def test_exporter_publishes_available_resources(coord: Coordinator):
    proc = coord.client("resources")
    assert proc.returncode == 0, proc.stderr
    assert f"{GROUP}/prta-lane-a/NetworkService" in proc.stdout
    assert f"{GROUP}/prta-lane-b/NetworkService" in proc.stdout

    places = coord.client("places")
    assert PLACE_A in places.stdout and PLACE_B in places.stdout

    show = coord.client("show", place=PLACE_A)
    assert show.returncode == 0, show.stderr
    assert "acquired: None" in show.stdout
    assert "NetworkService" in show.stdout


# --------------------------------------------------------------------------- #
# 2. acquire / release lifecycle through OUR wrapper
# --------------------------------------------------------------------------- #
def test_wrapper_acquire_and_release_round_trip(coord: Coordinator, bench_factory):
    bench = bench_factory(PLACE_A, "run-round-trip")
    assert bench.owner() == ""

    bench.acquire()
    assert bench.acquired is True
    assert coord.owner(PLACE_A) == bench.identity.owner
    assert bench.owns() is True

    bench.release()
    assert bench.acquired is False
    assert bench.cleanup_pending is False
    assert coord.owner(PLACE_A) == ""


def test_second_identity_cannot_take_a_held_place(coord: Coordinator, bench_factory):
    from lib.labgrid_venue import LabgridBenchError

    first = bench_factory(PLACE_A, "run-first")
    second = bench_factory(PLACE_A, "run-second")
    first.acquire()
    try:
        with pytest.raises(LabgridBenchError):
            second.acquire()
        # ...and the loser did not release the winner's place.
        assert coord.owner(PLACE_A) == first.identity.owner
    finally:
        first.release()
    assert coord.owner(PLACE_A) == ""


# --------------------------------------------------------------------------- #
# 3. conditional release: the semantics our release() re-read exists for
# --------------------------------------------------------------------------- #
def test_non_owner_release_from_reports_success_and_releases_nothing(coord: Coordinator):
    """Pinned coordinator: ``release-from`` returns rc=0 for a non-owner.

    This is measured, not assumed: the test fails loudly if a future labgrid
    makes the RPC honest, because our wrapper's re-read would then be
    unnecessary (and this module's docstring would be wrong).
    """
    lease = coord.client("acquire", place=PLACE_A, user="owner-run")
    assert lease.returncode == 0, lease.stderr
    owner_before = coord.owner(PLACE_A)
    assert owner_before == "it-host/owner-run"

    proc = coord.client("release-from", "it-host/some-other-run", place=PLACE_A, user="some-other-run")
    assert proc.returncode == 0
    assert "has released" in (proc.stdout + proc.stderr)  # it *claims* success
    assert coord.owner(PLACE_A) == owner_before  # and it changed nothing

    coord.client("release-from", owner_before, place=PLACE_A, user="owner-run")
    assert coord.owner(PLACE_A) == ""


def test_wrapper_release_does_not_clear_state_it_could_not_release(coord: Coordinator, bench_factory):
    """If our lease is stolen, release() must not pretend it cleaned up."""
    bench = bench_factory(PLACE_A, "run-stolen")
    bench.acquire()
    # Simulate fencing: another identity takes the place over.
    coord.client("release-from", bench.identity.owner, place=PLACE_A, user=bench.identity.user)
    coord.client("acquire", place=PLACE_A, user="fenced-by")

    bench.release()  # must notice it is not ours and touch nothing
    assert coord.owner(PLACE_A) == "it-host/fenced-by"


# --------------------------------------------------------------------------- #
# 4. two real processes, one place: exactly one wins
# --------------------------------------------------------------------------- #
_RACE_CHILD = '''
import os, sys, json, time
sys.path.insert(0, {root!r})
from lib.labgrid_venue import ClientIdentity, LabgridBench, LabgridBenchError

coordinator, place, run_id, go_file, out_file = sys.argv[1:6]
bench = LabgridBench(coordinator, place, identity=ClientIdentity.for_run(run_id, host="it-host"))

deadline = time.monotonic() + 30
while not os.path.exists(go_file):
    if time.monotonic() > deadline:
        print("barrier timeout", file=sys.stderr); sys.exit(9)
    time.sleep(0.01)

result = {{"run_id": run_id, "acquired": False, "error": "", "owner": ""}}
try:
    bench.acquire()
    result["acquired"] = True
except LabgridBenchError as e:
    result["error"] = str(e)[:200]
try:
    result["owner"] = bench.owner()
except LabgridBenchError as e:
    result["owner_error"] = str(e)[:200]
result["started"] = time.time()
with open(out_file, "w") as f:
    json.dump(result, f)
# hold briefly so the two processes genuinely overlap
time.sleep(1.0)
'''


def test_two_process_race_for_one_place(coord: Coordinator, tmp_path: Path):
    """Two independent OS processes, one released place: exactly one execution."""
    go = tmp_path / "go"
    script = tmp_path / "race_child.py"
    script.write_text(_RACE_CHILD.format(root=str(REPO_ROOT)))

    outs = []
    procs = []
    for run_id in ("race-alpha", "race-bravo"):
        out = tmp_path / f"{run_id}.json"
        outs.append(out)
        procs.append(
            subprocess.Popen(
                [sys.executable, str(script), coord.address, PLACE_A, run_id, str(go), str(out)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    time.sleep(1.0)  # both children are now blocked on the barrier
    go.write_text("go")

    results = []
    for proc, out in zip(procs, outs):
        stdout, stderr = proc.communicate(timeout=90)
        assert proc.returncode == 0, f"child failed rc={proc.returncode}: {stderr}\n{stdout}"
        results.append(__import__("json").loads(out.read_text()))

    winners = [r for r in results if r["acquired"]]
    losers = [r for r in results if not r["acquired"]]
    assert len(winners) == 1, f"expected exactly one winner, got {results}"
    assert len(losers) == 1, f"expected exactly one loser, got {results}"
    assert losers[0]["error"], "the loser must report why it lost"

    winner_owner = winners[0]["owner"]
    assert winner_owner == ClientIdentity.for_run(winners[0]["run_id"], host="it-host").owner
    assert winner_owner.startswith(f"it-host/prta-{winners[0]['run_id']}-")
    assert coord.owner(PLACE_A) == winner_owner, "the losing process must not have released the winner"

    coord.client("release-from", winner_owner, place=PLACE_A, user=winners[0]["run_id"])
    assert coord.owner(PLACE_A) == ""


# --------------------------------------------------------------------------- #
# 5. disconnect: the lease outlives the client that took it
# --------------------------------------------------------------------------- #
def test_lease_survives_client_disconnect(coord: Coordinator):
    """A crashed client does NOT free the place — cleanup cannot infer it."""
    proc = coord.client("acquire", place=PLACE_A, user="crashy-run")
    assert proc.returncode == 0, proc.stderr
    # The client process has exited (that IS the disconnect); the lease remains.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if coord.owner(PLACE_A) == "it-host/crashy-run":
            break
        time.sleep(0.1)
    assert coord.owner(PLACE_A) == "it-host/crashy-run", "pinned coordinator freed the place on disconnect"

    # Only an explicit conditional release of the owner clears it.
    coord.client("release-from", "it-host/crashy-run", place=PLACE_A, user="crashy-run")
    assert coord.owner(PLACE_A) == ""


# --------------------------------------------------------------------------- #
# 6. native reservations exist and are owner-scoped (pinned version)
# --------------------------------------------------------------------------- #
def test_native_reservation_is_owner_scoped(coord: Coordinator):
    """Gate 7's explicit requirement: confirm the API before depending on it.

    Measured on the pinned coordinator: ``reserve KEY=VALUE`` allocates the
    matching place to the reserving owner; a different identity is REFUSED with
    "was not reserved for", and the refusal leaves the place free.
    """
    set_tags = coord.client("set-tags", "prta-it=true", place=PLACE_B)
    assert set_tags.returncode == 0, set_tags.stderr

    reserved = coord.client("reserve", "prta-it=true", place=PLACE_B, user="alice")
    assert reserved.returncode == 0, reserved.stderr + reserved.stdout
    assert "state: allocated" in reserved.stdout
    token = coord.reservation(PLACE_B)
    assert token, "coordinator did not record a reservation for the place"

    intruder = coord.client("acquire", place=PLACE_B, user="bob")
    assert intruder.returncode != 0
    assert "not reserved for" in (intruder.stdout + intruder.stderr)
    assert coord.owner(PLACE_B) == ""

    assert coord.client("acquire", place=PLACE_B, user="alice").returncode == 0
    assert coord.owner(PLACE_B) == "it-host/alice"
    coord.client("release-from", "it-host/alice", place=PLACE_B, user="alice")

    cancel = coord.client("cancel-reservation", token, place=PLACE_B, user="alice")
    assert cancel.returncode == 0, cancel.stderr
    assert coord.reservation(PLACE_B) == ""


# --------------------------------------------------------------------------- #
# 7. restart: places survive, ownership does not
# --------------------------------------------------------------------------- #
def test_restart_keeps_places_but_drops_ownership(coord: Coordinator):
    """Restart semantics that justify invalidating the authority epoch.

    Measured on the pinned coordinator:

    * persistence is a 15-second poll (``poll`` → ``_poll_step_save``), so a
      place created just before SIGTERM may not be on disk at all;
    * once flushed, ``load()`` keeps the place but DELETES ``acquired`` /
      ``acquired_resources`` — a lease is LOST on restart.

    Both matter to a scheduler: a TTL that survived a coordinator restart would
    make it believe it holds a place it no longer holds.
    """
    assert coord.client("acquire", place=PLACE_A, user="before-restart").returncode == 0
    assert coord.owner(PLACE_A) == "it-host/before-restart"

    # Wait for the coordinator's own save poll to flush places.yaml, so this
    # test measures reload semantics rather than the flush interval.
    places_file = coord.workdir / "places.yaml"
    _wait_for(
        "coordinator to persist places.yaml",
        lambda: (places_file.exists() and PLACE_A in places_file.read_text(), str(places_file)),
        timeout=40,
    )

    coord.stop()
    coord.start()  # same workdir: places.yaml + resources.yaml reloaded

    places = coord.client("places")
    assert PLACE_A in places.stdout, "place did not survive restart"
    assert coord.owner(PLACE_A) == "", "ownership unexpectedly survived restart"

    # And the place is genuinely usable again.
    assert coord.client("acquire", place=PLACE_A, user="after-restart").returncode == 0
    coord.client("release-from", "it-host/after-restart", place=PLACE_A, user="after-restart")
