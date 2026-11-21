"""Each fleet's coordinator state must live in its own directory.

``labgrid-coordinator`` persists to ``places.yaml`` / ``resources.yaml`` using
**relative** names resolved against the process's working directory, and there
is no option to point them elsewhere. Two coordinators in one directory
therefore share one persistence file — not by design, but by accident.

The failure mode is more specific than "they share places", and stating it
wrongly would have shipped a false warning, so it is pinned here exactly as
measured:

* **Live processes are isolated.** Each coordinator holds its own in-memory
  place dictionary; a place created on one is not visible on the other while
  both are running.
* **Both write the SAME file.** ``_poll_step_save`` runs every 15 s and
  ``save()`` writes that coordinator's *entire* snapshot via
  ``atomic_replace``. Two writers, one filename: last writer wins, so the
  persisted set alternates and only ever reflects one fleet.
* **A restart adopts the other fleet's places.** ``load()`` reads that file at
  startup, so a coordinator restarted in a shared directory comes up owning
  another contributor's places — the operator-visible defect that makes this
  worth a test.

So the rule is not "isolate live coordinators" (they already are), it is
"never let two fleets share a directory, because the survivor adopts the dead
one's hardware on restart".
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

COORDINATOR = shutil.which("labgrid-coordinator")
CLIENT = shutil.which("labgrid-client")

pytestmark = pytest.mark.skipif(
    not (COORDINATOR and CLIENT),
    reason="labgrid coordinator/client binaries not installed",
)

START_TIMEOUT_S = 25.0
#: The coordinator saves on a 15 s poll; allow two cycles plus slack.
SAVE_WAIT_S = 35.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # A proxy in the environment makes the client attempt an SSH jump to the
    # coordinator address and fail with "Could not resolve hostname".
    env.pop("LG_PROXY", None)
    env["LG_HOSTNAME"] = "prta-test-host"
    env["LG_USERNAME"] = "prta-test-user"
    return env


def _start(directory: Path) -> tuple[int, subprocess.Popen]:
    port = _free_port()
    proc = subprocess.Popen(
        [COORDINATOR, "-l", f"127.0.0.1:{port}"],
        cwd=directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"coordinator exited early rc={proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return port, proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise AssertionError(f"coordinator on {port} never listened within {START_TIMEOUT_S}s")


def _cli(port: int, *args: str, place: str | None = None) -> subprocess.CompletedProcess:
    cmd = [CLIENT, "-x", f"127.0.0.1:{port}"]
    if place:
        cmd += ["-p", place]
    return subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=30, env=_env())


def _places(port: int) -> list[str]:
    return sorted(name for name in _cli(port, "places").stdout.split() if name)


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_for(path: Path) -> bool:
    deadline = time.monotonic() + SAVE_WAIT_S
    while time.monotonic() < deadline:
        if path.is_file():
            return True
        time.sleep(0.5)
    return False


@pytest.fixture
def fleets(tmp_path):
    """Two coordinators, each with its own state directory (the supported shape)."""
    procs: list[subprocess.Popen] = []
    dirs: dict[str, Path] = {}
    ports: dict[str, int] = {}
    for name in ("alice", "bob"):
        directory = tmp_path / name
        directory.mkdir()
        port, proc = _start(directory)
        procs.append(proc)
        dirs[name] = directory
        ports[name] = port
    try:
        yield dirs, ports
    finally:
        for proc in procs:
            _stop(proc)


def test_each_fleet_lists_only_its_own_places(fleets):
    """The property an operator depends on: your places are yours."""
    _, ports = fleets
    assert _cli(ports["alice"], "create", place="router-a1").returncode == 0
    assert _cli(ports["bob"], "create", place="router-b1").returncode == 0

    alice, bob = _places(ports["alice"]), _places(ports["bob"])
    assert "router-a1" in alice
    assert "router-b1" in bob
    assert "router-b1" not in alice, "alice's coordinator sees bob's place — fleets are not isolated"
    assert "router-a1" not in bob, "bob's coordinator sees alice's place — fleets are not isolated"


def test_a_place_on_one_fleet_is_unknown_to_the_other(fleets):
    """Isolation is not just listing: the other fleet must not be able to show it."""
    _, ports = fleets
    assert _cli(ports["alice"], "create", place="only-on-alice").returncode == 0

    shown = _cli(ports["bob"], "show", place="only-on-alice")
    assert shown.returncode != 0, "bob's fleet could read a place that belongs to alice"


def test_isolated_fleets_write_their_own_state_files(fleets):
    """One directory per fleet means one persistence file per fleet."""
    dirs, ports = fleets
    assert _cli(ports["alice"], "create", place="router-a1").returncode == 0

    assert _wait_for(dirs["alice"] / "places.yaml"), (
        "coordinator did not persist to its own directory"
    )
    assert not (dirs["bob"] / "places.yaml").is_file(), (
        "an idle fleet acquired a state file it never wrote"
    )


def test_a_restarted_coordinator_can_adopt_another_fleets_places(tmp_path):
    """Why the one-directory-per-fleet rule exists — demonstrated, not asserted.

    Two coordinators in one directory stay isolated *while both run*, but they
    write the SAME ``places.yaml`` every 15 s, each writing its whole snapshot.
    Last writer wins, so the file holds one fleet's view at any moment. A
    coordinator restarted there ``load()``s that file, so it can come up owning
    the other fleet's places.

    Which fleet wins is a race, so the assertion is the hazard rather than a
    specific name: across restarts the instance must eventually surface a place
    that only the OTHER coordinator created. If labgrid ever gains a
    per-instance state directory this fails, and the warning in
    ``docs/fleet-capacity-observation.md`` can be retired.
    """
    shared = tmp_path / "shared"
    shared.mkdir()

    port_a, proc_a = _start(shared)
    proc_b = None
    try:
        assert _cli(port_a, "create", place="alpha").returncode == 0
        assert _wait_for(shared / "places.yaml"), "A never persisted"

        port_b, proc_b = _start(shared)
        assert "alpha" in _places(port_a), "A lost its own place"
        assert _cli(port_b, "create", place="beta").returncode == 0

        # The shared file alternates between the two fleets' snapshots, so give
        # each restart a chance to catch B's view before giving up.
        adopted_any: list[str] = []
        for _ in range(4):
            _stop(proc_a)
            port_a2, proc_a = _start(shared)
            adopted_any.extend(_places(port_a2))
            if "beta" in adopted_any:
                break
            time.sleep(16)

        assert "beta" in adopted_any, (
            "a restart in a shared directory never adopted the other fleet's place — "
            "labgrid may now isolate per instance; re-check "
            "docs/fleet-capacity-observation.md"
        )
    finally:
        _stop(proc_a)
        if proc_b is not None:
            _stop(proc_b)


def test_two_coordinators_in_one_directory_share_one_state_file(tmp_path):
    """The mechanism behind the rule: one filename, two writers."""
    shared = tmp_path / "shared"
    shared.mkdir()
    port_a, proc_a = _start(shared)
    port_b, proc_b = _start(shared)
    try:
        _cli(port_a, "create", place="from-a")
        _cli(port_b, "create", place="from-b")

        assert _wait_for(shared / "places.yaml"), "neither coordinator persisted"
        assert sorted(p.name for p in shared.glob("places*.yaml")) == ["places.yaml"], (
            "coordinator gained a per-instance state file — the one-directory rule may be obsolete"
        )
        assert not sorted(shared.glob("resources*.yaml")) or sorted(
            p.name for p in shared.glob("resources*.yaml")
        ) == ["resources.yaml"], "coordinator gained a per-instance resources file"
    finally:
        _stop(proc_a)
        _stop(proc_b)
