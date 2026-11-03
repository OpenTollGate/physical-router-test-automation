"""Spilman sidecar acceptance lane — the sidecar segment on a real router.

This is the PRTA half of the router proof. The in-repo half
(`tollgate-module-basic-go` `tests/contract/spilman-router-lane.sh`) proves the
artifact behaves on a target while the artifact is still described by the repo
that builds it. This one exists so the same claim is checkable from the
repository that owns **router-visible acceptance**, bound to the exact source
SHA and artifact sha256 — per engineering-policy, "unit tests passing does not
mean a router-visible change works", and the evidence belongs where the routers
are.

HONEST SCOPE, because this is the easiest place in the repo to overclaim:

* This proves the SIDECAR SEGMENT: the statically linked aarch64-musl binary
  starts on OpenWrt, binds its authenticated socket, parses nested params with
  the pinned protocol crate, derives the golden channel id, and refuses each
  adversarial registration with its own code.
* It does **not** prove a purchase, a swap, a mint call, settlement, or that any
  channel can be closed. `validate_registration.v1` is offline and
  value-incapable by construction and the binary is asserted to carry no
  settlement capability string. A green run here means "registration-time
  parameter validation works on this target", nothing more.
* It does not install the package or exercise the portal. Package-level
  acceptance stays with the existing wizards/installer lanes.

Data source is the SAME generated fixture the Rust and Go contract tests use
(`sidecar/spilman/tests/fixtures/registration_golden.json`), fetched from the
pinned source SHA rather than re-derived here. Generating an independent vector
in this repo would be a second implementation of channel-id derivation, which is
exactly the drift the backend repo fences.

Runs only where a reachable place exists; skips loudly otherwise. The
`required` marker turns that skip into a failure when the lane is declared
release-relevant (`SPILMAN_SIDECAR_REQUIRED=1`), so a missing router can never
masquerade as a pass in a release gate.

Usage:
    SPILMAN_SIDECAR_HOST=192.168.50.205 \\
      ./.venv/bin/python -m pytest tests/bench/test_spilman_sidecar_place.py -v

    # or let the labgrid registry resolve the place:
    ./.venv/bin/python -m pytest tests/bench/test_spilman_sidecar_place.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

# The backend repo that builds the artifact. Overridable so a PR lane can point
# at the contributor's worktree instead of a fresh clone.
BACKEND_REPO = os.environ.get("SPILMAN_BACKEND_REPO", "")
BACKEND_REF = os.environ.get(
    "SPILMAN_BACKEND_REF", "pr/spilman-sidecar-contract"
)
TARGET = os.environ.get("SPILMAN_TARGET", "aarch64-unknown-linux-musl")
REMOTE_DIR = "/tmp/spilman-acceptance"
HELPER = Path("tests/contract/spilman-router-lane.sh")

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.api,
]


def _resolve_place() -> tuple[str, str]:
    """Return (place_name, lan_ip) from the labgrid registry.

    The address is a device fact owned by the registry. Reading it keeps one
    declaration; a literal in a test would be the drift engineering-policy
    forbids.
    """
    explicit = os.environ.get("SPILMAN_SIDECAR_HOST", "").strip()
    place_name = os.environ.get("LABGRID_PLACE", "gl-mt3000")
    if explicit:
        return place_name, explicit
    registry = Path(
        os.environ.get(
            "LABGRID_REGISTRY",
            str(Path.home() / "hermes-orchestration/state/fleet/labgrid-places.json"),
        )
    )
    if not registry.is_file():
        return place_name, ""
    data = json.loads(registry.read_text())
    for router in data.get("routers", []):
        if router.get("name") == place_name:
            return place_name, router.get("lan_ip", "")
    return place_name, ""


def _require(name: str):
    path = shutil.which(name)
    if path is None:
        if os.environ.get("SPILMAN_SIDECAR_REQUIRED") == "1":
            pytest.fail(f"NOT VERIFIED: {name} is required for this lane and is absent")
        pytest.skip(f"{name} not available on this host")
    return path


def _ssh(host: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=yes",
            f"root@{host}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def place() -> tuple[str, str]:
    place_name, host = _resolve_place()
    if not host:
        if os.environ.get("SPILMAN_SIDECAR_REQUIRED") == "1":
            pytest.fail(
                f"NOT VERIFIED: no labgrid place {place_name!r} resolved "
                "(set SPILMAN_SIDECAR_HOST or LABGRID_REGISTRY)"
            )
        pytest.skip(f"no LAN address for labgrid place {place_name!r}")
    return place_name, host


@pytest.fixture(scope="module")
def backend_repo() -> Path:
    """The checkout that built the artifact, at the ref under test.

    A PR lane points SPILMAN_BACKEND_REPO at the contributor's worktree so the
    evidence is bound to their head, not to whatever main happens to contain.
    """
    if BACKEND_REPO:
        path = Path(BACKEND_REPO).expanduser()
        if not (path / "sidecar/spilman/Cargo.toml").is_file():
            pytest.fail(f"SPILMAN_BACKEND_REPO={path} has no sidecar/spilman crate")
        return path
    _require("git")
    if os.environ.get("SPILMAN_SIDECAR_REQUIRED") == "1":
        pytest.fail(
            "NOT VERIFIED: set SPILMAN_BACKEND_REPO so the evidence is bound to a "
            "specific source checkout rather than an unstated tree"
        )
    pytest.skip("set SPILMAN_BACKEND_REPO to the tollgate-module-basic-go checkout")


@pytest.fixture(scope="module")
def source_sha(backend_repo: Path) -> str:
    _require("git")
    result = subprocess.run(
        ["git", "-C", str(backend_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"git rev-parse failed in {backend_repo}: {result.stderr.strip()}")
    return result.stdout.strip()


@pytest.fixture(scope="module")
def artifact(backend_repo: Path, place: tuple[str, str]) -> dict:
    """Build for the target and deploy, returning the binding facts.

    Building here rather than trusting a prebuilt path is deliberate: an
    acceptance lane that runs an artifact it did not build reports on a binary
    nobody can reproduce.
    """
    place_name, host = place
    crate = backend_repo / "sidecar" / "spilman"
    binary = crate / "target" / TARGET / "release" / "tollgate-spilman-sidecar"

    # SPILMAN_SIDECAR_PREBUILT lets a repeated run reuse an artifact already
    # built from THIS checkout. It does not weaken the binding: the source SHA
    # and the artifact sha256 are both captured below and printed in every
    # assertion message, and the build is skipped only for an existing file in
    # the checkout's own target dir. A cold runner still builds.
    prebuilt_ok = os.environ.get("SPILMAN_SIDECAR_PREBUILT") == "1" and binary.is_file()
    if prebuilt_ok:
        pass
    else:
        _require("cargo")
        build = subprocess.run(
            ["cargo", "build", "--release", "--target", TARGET,
             "--manifest-path", str(crate / "Cargo.toml")],
            capture_output=True, text=True, timeout=1800,
        )
        assert build.returncode == 0, f"cargo build failed:\n{build.stderr[-3000:]}"

    assert binary.is_file(), f"no artifact at {binary}"
    digest = subprocess.run(
        ["sha256sum", str(binary)], capture_output=True, text=True, timeout=60,
    ).stdout.split()[0]

    assert _ssh(host, f"mkdir -p {REMOTE_DIR} && chmod 700 {REMOTE_DIR}").returncode == 0

    # Stop any previous instance BEFORE overwriting: a running binary holds its
    # text image, so the copy fails "Text file busy". BusyBox has pgrep but no
    # pkill, and the binary runs as ./<name>, so match the basename and kill by
    # pid.
    _ssh(
        host,
        "for p in $(pgrep -f '[t]ollgate-spilman-sidecar'); do kill $p 2>/dev/null; done; "
        "sleep 0.5; true",
    )

    # Dropbear has no sftp-server, so modern scp (SFTP) fails; -O forces the
    # legacy protocol it speaks.
    copy = subprocess.run(
        ["scp", "-O", "-q", "-o", "BatchMode=yes", str(binary),
         f"root@{host}:{REMOTE_DIR}/tollgate-spilman-sidecar"],
        capture_output=True, text=True, timeout=300,
    )
    assert copy.returncode == 0, f"scp failed: {copy.stderr}"

    remote_digest = _ssh(host, f"sha256sum {REMOTE_DIR}/tollgate-spilman-sidecar").stdout.split()[0]
    assert remote_digest == digest, "deployed artifact does not match the built one"

    return {
        "place": place_name,
        "host": host,
        "sha256": digest,
        "remote_sha256": remote_digest,
        "path": binary,
    }


@pytest.fixture(scope="module")
def golden(backend_repo: Path) -> dict:
    fixture = backend_repo / "sidecar/spilman/tests/fixtures/registration_golden.json"
    assert fixture.is_file(), f"golden fixture missing at {fixture}"
    return json.loads(fixture.read_text())


def _frames(golden: dict) -> tuple[dict[str, dict], dict]:
    """Build request frames and the expectation table.

    Everything here mutates the SAME fixture the Rust and Go tests use, so a
    divergence between this lane and the backend's own tests would be visible as
    a disagreement rather than hidden behind an independently invented vector.
    """
    reg = golden["registration"]
    nested = json.loads(golden["nested_params_json"])
    now = 1_700_000_000  # well before the fixture's expiry: the happy path is not testing it

    def params(**over):
        payload = {
            "receiver_secret_hex": golden["receiver_secret_hex"],
            "trusted_mint_url": golden["trusted_mint_url"],
            "trusted_keyset_info": golden["trusted_keyset_info"],
            "registration": json.loads(json.dumps(reg)),
            "params_json": json.dumps(nested, sort_keys=True, separators=(",", ":")),
            "now_unix": now,
        }
        for path, value in over.items():
            if path in payload:
                payload[path] = value
                continue
            target, _, field = path.partition("__")
            assert field and target in payload, f"unknown override {path!r}"
            payload[target][field] = value
        return payload

    def nested_with(**changes):
        return json.dumps({**nested, **changes}, sort_keys=True, separators=(",", ":"))

    cases = {
        "golden": (params(), None),
        # (a) the receiver pubkey is not the one derived from the payee's secret
        "receiver_mismatch": (
            params(registration__receiver_pubkey="03" + reg["receiver_pubkey"][2:]),
            "receiver_pubkey_mismatch",
        ),
        # (b) NESTED params name another mint while the outer record and the
        # trusted URL both stay correct — only parsing the blob catches it
        "nested_mint": (params(params_json=nested_with(mint="https://evil.example")), "mint_not_trusted"),
        # (b) nested capacity disagrees with the outer capacity
        "nested_capacity": (params(params_json=nested_with(capacity=1)), "nested_params_mismatch"),
        # (b) the outer record names a mint this daemon does not trust
        "untrusted_outer_mint": (params(trusted_mint_url="https://evil.example"), "mint_not_trusted"),
        # (c) expired — the clock is supplied, so this is judgeable offline
        "expired": (params(now_unix=4_102_444_801), "registration_expired"),
        # (c) zero capacity is not a channel
        "zero_capacity": (params(registration__capacity=0), "zero_capacity"),
        # (c) an unsupported unit
        "unsupported_unit": (params(registration__unit="usd"), "unsupported_unit"),
        # (c) a channel id that cannot be derived
        "bad_sender": (params(registration__sender_pubkey="not-a-pubkey"), "channel_id_not_derivable"),
    }
    frames = {
        name: {
            "version": 1,
            "request_id": f"accept-{name}",
            "operation_id": f"accept-{name}",
            "method": "validate_registration",
            "params": payload,
        }
        for name, (payload, _) in cases.items()
    }
    expect = {name: want for name, (_, want) in cases.items()}
    return frames, {"cases": expect, "channel_id": golden["expected"]["channel_id"],
                    "canonical_params_json": golden["expected"]["canonical_params_json"]}


@pytest.fixture(scope="module")
def replies(place: tuple[str, str], artifact: dict, golden: dict) -> dict:
    """Send each frame from the router and return the decoded replies.

    Frames are built here (python3 exists) and shipped; stock OpenWrt has
    neither python3 nor jq, so nothing is parsed on the device and no assertion
    is computed by the machine that built the binary.
    """
    _place_name, host = place
    frames, _expect = _frames(golden)

    assert _ssh(host, "command -v socat >/dev/null").returncode == 0, (
        "the router has no socat to drive an AF_UNIX socket"
    )

    work = Path(tempfile.mkdtemp(prefix="spilman-accept-"))
    try:
        for name, frame in frames.items():
            (work / f"{name}.json").write_text(json.dumps(frame) + "\n")
        for f in sorted(work.glob("*.json")):
            cp = subprocess.run(
                ["scp", "-O", "-q", "-o", "BatchMode=yes", str(f), f"root@{host}:{REMOTE_DIR}/"],
                capture_output=True, text=True, timeout=120,
            )
            assert cp.returncode == 0, f"could not ship {f.name}: {cp.stderr}"

        start = _ssh(
            host,
            f"cd {REMOTE_DIR} && setsid ./tollgate-spilman-sidecar --socket {REMOTE_DIR}/s.sock "
            f">{REMOTE_DIR}/sidecar.log 2>&1 & "
            "for i in 1 2 3 4 5 6 7 8 9 10; do [ -S " + REMOTE_DIR + "/s.sock ] && break; sleep 0.2; done; "
            f"test -S {REMOTE_DIR}/s.sock && echo BOUND || {{ echo NOBOUND; cat {REMOTE_DIR}/sidecar.log; }}",
            timeout=60,
        )
        assert "BOUND" in start.stdout, f"sidecar did not bind on {host}: {start.stdout}{start.stderr}"

        out: dict[str, dict] = {}
        for name in frames:
            _ssh(
                host,
                f"socat -t 10 - UNIX-CONNECT:{REMOTE_DIR}/s.sock < {REMOTE_DIR}/{name}.json "
                f"> {REMOTE_DIR}/{name}.reply 2>/dev/null",
                timeout=60,
            )
            fetch = subprocess.run(
                ["scp", "-O", "-q", "-o", "BatchMode=yes",
                 f"root@{host}:{REMOTE_DIR}/{name}.reply", str(work / f"{name}.reply")],
                capture_output=True, text=True, timeout=120,
            )
            reply_file = work / f"{name}.reply"
            if fetch.returncode != 0 or not reply_file.exists():
                out[name] = {"__missing__": True}
                continue
            text = reply_file.read_text().strip()
            out[name] = json.loads(text.splitlines()[0]) if text else {"__missing__": True}

        # Capabilities, for the "cannot move value" assertions.
        _ssh(
            host,
            "printf '{\"version\":1,\"request_id\":\"accept-caps\",\"operation_id\":\"accept-caps\","
            f"\"method\":\"capabilities\"}}\\n' | socat -t 10 - UNIX-CONNECT:{REMOTE_DIR}/s.sock "
            f"> {REMOTE_DIR}/caps.reply 2>/dev/null",
            timeout=60,
        )
        subprocess.run(
            ["scp", "-O", "-q", "-o", "BatchMode=yes",
             f"root@{host}:{REMOTE_DIR}/caps.reply", str(work / "caps.reply")],
            capture_output=True, text=True, timeout=120,
        )
        caps_file = work / "caps.reply"
        out["__caps__"] = (
            json.loads(caps_file.read_text().strip().splitlines()[0])
            if caps_file.exists() and caps_file.read_text().strip()
            else {}
        )
        return out
    finally:
        _ssh(host, "for p in $(pgrep -f '[t]ollgate-spilman-sidecar'); do kill $p 2>/dev/null; done; true")
        shutil.rmtree(work, ignore_errors=True)


@pytest.mark.pr(1183)
@pytest.mark.happy
@pytest.mark.required
def test_sidecar_derives_the_golden_channel_id_on_the_router(
    artifact: dict, replies: dict, golden: dict, source_sha: str
) -> None:
    """The target must derive the SAME channel id the pinned crate generates.

    This is the whole point of the lane: channel id derivation is what every
    stored channel is keyed by, and a cross-compiled build that derives a
    different id would make every channel unfindable at close time.
    """
    reply = replies.get("golden", {})
    assert not reply.get("__missing__"), "no reply to the golden registration"
    validated = reply.get("validated", {})
    assert validated, f"golden registration was refused: {reply}"
    assert validated.get("channel_id") == golden["expected"]["channel_id"], (
        f"channel id on {artifact['host']} ({artifact['place']}) differs from the "
        f"pinned crate's vector — source {source_sha}, artifact {artifact['sha256']}"
    )
    assert validated.get("canonical_params_json") == golden["expected"]["canonical_params_json"], (
        "canonical params differ from the pinned crate's own serializer"
    )
    # The honesty marker is part of the contract, not decoration.
    assert validated.get("mint_funds_asserted") is False, (
        "a successful validation must never assert anything about the mint's funds"
    )


@pytest.mark.pr(1183)
@pytest.mark.failure
@pytest.mark.required
@pytest.mark.parametrize(
    "case,code",
    [
        ("receiver_mismatch", "receiver_pubkey_mismatch"),
        ("nested_mint", "mint_not_trusted"),
        ("nested_capacity", "nested_params_mismatch"),
        ("untrusted_outer_mint", "mint_not_trusted"),
        ("expired", "registration_expired"),
        ("zero_capacity", "zero_capacity"),
        ("unsupported_unit", "unsupported_unit"),
        ("bad_sender", "channel_id_not_derivable"),
    ],
)
def test_router_refuses_each_adversarial_registration(
    replies: dict, case: str, code: str, artifact: dict
) -> None:
    """Every refusal must cross the wire intact ON THE TARGET, not just on x86_64.

    A code the client cannot read is a refusal an operator cannot act on, and
    this is the layer where a musl build could plausibly differ from the host
    build.
    """
    reply = replies.get(case, {})
    assert not reply.get("__missing__"), f"{case}: no reply over the socket on {artifact['host']}"
    assert reply.get("error") == code, (
        f"{case}: expected {code!r}, got {reply.get('error')!r} (reply: {reply})"
    )
    assert "validated" not in reply or not reply.get("validated"), (
        f"{case}: a refusal must not carry validated values"
    )


@pytest.mark.pr(1183)
@pytest.mark.failure
@pytest.mark.required
def test_router_artifact_cannot_move_value(artifact: dict, replies: dict, place: tuple[str, str]) -> None:
    """The milestone adds parameter checking, never a money-moving surface.

    Two independent checks, because either alone is weak: the capability list
    says what the peer is TOLD it can do, and the binary itself must not carry
    the settlement surface at all.
    """
    _place_name, host = place
    caps = replies.get("__caps__", {}).get("capabilities") or []
    assert "validate_registration.v1" in caps, f"capabilities do not advertise the new capability: {caps}"
    money_moving = [c for c in caps if "settlement" in c or "swap" in c or c.startswith("mint.")]
    assert not money_moving, f"a money-moving capability is advertised: {money_moving}"

    found = _ssh(
        host,
        f"grep -qaE 'settlement\\.v1|prepare_close|verify_prepared' {REMOTE_DIR}/tollgate-spilman-sidecar "
        "&& echo YES || echo NO",
    )
    assert found.stdout.strip() == "NO", (
        f"the on-router artifact carries a settlement/signing capability string "
        f"(source {artifact['path']}, sha256 {artifact['sha256']})"
    )
