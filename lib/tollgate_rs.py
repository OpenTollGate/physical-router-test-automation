"""Host-venue node manager for the current OpenTollGate/tollgate-rs node.

The v2 node has no HTTP captive API. It is ``tollgated`` speaking the native
TollGate protocol over TCP, with ``mintd`` (the node's Cashu mint) and
``merchantd`` (the node's wallet) beside it — the same three daemons the
OpenWrt package installs — and a control socket that serves one JSON
snapshot of what the node believes about itself. That snapshot is the
assertion surface, exactly as tollgate-rs's own Docker topologies use it
(tollgate-rs testing/README.md, "How a test sees what happened").

Money is not real here: every mint runs ``auto_accept`` (the default), so a
buyer funds its channel by minting what it needs at the seller's mint. The
mint, the keysets, the channel funding and the spent-proof set are real;
only the payment is absent.

Two nodes fit on one host: every port and file is per-node. The state
directory (/var/lib/tollgate on most hosts) is shared, so mint databases,
seeds and wallets are pinned inside the node's workdir, as the tollgate-rs
configuration reference demands ("two nodes on one host that both leave
``file`` empty share one database"). Budget files are per-instance
(``budgets-<instance>.json``) and are meant to survive a restart — the
persistence test relies on that.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("tollgate.rust_v2")

# Test keys from tollgate-rs's own public topologies (testing/purchase/):
# checked into that repository, controlling nothing but a mint that gives
# its vouchers away. Fixed keys make the client's config — which must name
# the gateway's public key — reproducible.
GATEWAY_SECRET = "78d684aa33cd91c862ca9dd42e969b97e511bf36d4848e6a90d967bc174199c1"
GATEWAY_PUBKEY = "02f383c916faa702f91b5e836ed6f12d5f80fb1752ff4db28e6e77997036befd74"
CLIENT_SECRET = "e320cb6477ed200fa997b77fdbbeb76f26f9b4672ac390cccde71292097b2b4b"


class TollgateRsError(RuntimeError):
    pass


def state_dir() -> Path:
    """Mirror tollgate-rs's state-directory resolution (config reference):
    the first of /var/lib/tollgate and /usr/local/var/lib/tollgate that
    exists or can be created, then $XDG_DATA_HOME/tollgate, then /tmp."""
    for candidate in (Path("/var/lib/tollgate"), Path("/usr/local/var/lib/tollgate")):
        if candidate.is_dir():
            return candidate
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    xdg = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "tollgate"
    xdg.mkdir(parents=True, exist_ok=True)
    return xdg


class TollgateRsNode:
    """One tollgate-rs node: mintd + merchantd + tollgated, as real processes.

    Listens on per-node loopback ports. The data plane sits one port above
    the control plane (a peer finds it there), so the control port must be
    even-free and its neighbour free too.
    """

    def __init__(
        self,
        name: str,
        bin_dir: Path,
        workdir: Path,
        secret_key: str,
        listen_port: int,
        mint_public_port: int,
        mint_private_port: int,
        merchant_port: int,
        peers: Optional[dict[str, dict[str, Any]]] = None,
        buying: Optional[dict[str, Any]] = None,
        demand: Optional[int] = None,
        grants_extra: Optional[dict[str, Any]] = None,
        channels: Optional[dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.bin_dir = Path(bin_dir)
        self.workdir = Path(workdir)
        self.secret_key = secret_key
        self.listen_port = listen_port
        self.mint_public_port = mint_public_port
        self.mint_private_port = mint_private_port
        self.merchant_port = merchant_port
        self.peers = peers or {}
        self.buying = buying or {}
        self.demand = demand
        self.grants_extra = grants_extra or {}
        self.channels = channels or {}

        self.state = self.workdir / "state"
        self.logs = self.workdir / "logs"
        self.runtime = Path(f"/run/tollgate-{name}")
        self.control_socket = self.runtime / "control.sock"
        self.merchant_socket = self.workdir / "merchantd.sock"
        self.processes: dict[str, subprocess.Popen] = {}

    # -- config ------------------------------------------------------------

    def _mint_url(self) -> str:
        return f"http://127.0.0.1:{self.mint_public_port}"

    def _private_url(self) -> str:
        return f"http://127.0.0.1:{self.mint_private_port}"

    def write_configs(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)

        lines = [
            f"instance: \"{self.name}\"",
            "identity:",
            f"  secret_key: \"{self.secret_key}\"",
            "merchant:",
            f"  socket: \"{self.merchant_socket}\"",
            "mint:",
            f"  url: \"{self._mint_url()}\"",
            "  unit: \"byte\"",
            "vouchers:",
            "  accepted_mints:",
            f"    - \"{self._mint_url()}\"",
            "access:",
            "  minimum_flow:",
            "    enabled: true",
            "    bytes_per_second: 4096",
            "grants:",
            "  from_payer_weight: 0",
            "  window_range_ms: [200, 30000]",
            "  min_topup_gap_ms: 200",
        ]
        lines += [f"  {k}: {v}" for k, v in self.grants_extra.items()]
        if self.channels:
            lines.append("channels:")
            lines += [f"  {k}: {v}" for k, v in self.channels.items()]
        lines += [
            "network:",
            f"  listen: \"127.0.0.1:{self.listen_port}\"",
        ]
        if self.buying or self.peers:
            lines += [
                "buying:",
                f"  window_ms: {self.buying.get('window_ms', 30000)}",
                f"  renew_lead_ms: {self.buying.get('renew_lead_ms', 300)}",
            ]
        for pubkey, cfg in self.peers.items():
            lines.append("peers:")
            lines.append(f'  "{pubkey}":')
            lines.append(f'    endpoint: "{cfg["endpoint"]}"')

        (self.workdir / "tollgate.yaml").write_text("\n".join(lines) + "\n")

        (self.workdir / "mint.yaml").write_text(
            "\n".join(
                [
                    "unit: \"byte\"",
                    f"url: \"{self._mint_url()}\"",
                    f"seed_file: \"{self.state / 'mint.seed'}\"",
                    f"file: \"{self.state / 'mint.sqlite'}\"",
                    "public:",
                    f"  listen: \"127.0.0.1:{self.mint_public_port}\"",
                    "private:",
                    f"  listen: \"127.0.0.1:{self.mint_private_port}\"",
                    "auto_accept: true",
                    "issue_quotes_per_minute: 60",
                ]
            )
            + "\n"
        )

        (self.workdir / "merchant.yaml").write_text(
            "\n".join(
                [
                    f"listen: \"127.0.0.1:{self.merchant_port}\"",
                    f"socket: \"{self.merchant_socket}\"",
                    f"control: \"{self.state / 'merchantd-control.sock'}\"",
                    "mint:",
                    f"  url: \"{self._mint_url()}\"",
                    f"  private: \"{self._private_url()}\"",
                    "  unit: \"byte\"",
                    "accepts:",
                    f"  - mint: \"{self._mint_url()}\"",
                    "    unit: \"byte\"",
                    "wallet:",
                    f"  file: \"{self.state / 'wallet.sqlite'}\"",
                    f"  seed_file: \"{self.state / 'wallet.seed'}\"",
                    f"  mint: \"{self._mint_url()}\"",
                    "  unit: \"byte\"",
                ]
            )
            + "\n"
        )

    # -- lifecycle ---------------------------------------------------------

    def _spawn(self, proc_name: str, args: list[str]) -> subprocess.Popen:
        log_file = open(self.logs / f"{proc_name}.log", "ab")
        env = dict(os.environ)
        env.setdefault("RUST_LOG", "tollgate_net=debug,info")
        proc = subprocess.Popen(
            [str(a) for a in args],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        self.processes[proc_name] = proc
        return proc

    def start(self, ready_timeout: float = 60.0) -> None:
        self.write_configs()
        self.runtime.mkdir(parents=True, exist_ok=True)

        self._spawn(
            "mintd",
            [
                self.bin_dir / "mintd",
                "-c",
                self.workdir / "mint.yaml",
                # Per-node: the default /tmp/mintd.sock is shared host-wide.
                "--control-socket",
                self.workdir / "mintd.sock",
            ],
        )
        self._spawn("merchantd", [self.bin_dir / "merchantd", "-c", self.workdir / "merchant.yaml"])

        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if self.processes["mintd"].poll() is not None:
                raise TollgateRsError(
                    f"{self.name}: mintd exited at startup — see {self.logs / 'mintd.log'}"
                )
            if self.processes["merchantd"].poll() is not None:
                raise TollgateRsError(
                    f"{self.name}: merchantd exited at startup — see {self.logs / 'merchantd.log'}"
                )
            if self._mint_ready() and self.merchant_socket.exists():
                break
            time.sleep(0.5)
        else:
            log.warning("%s: mintd/merchantd not confirmed ready; starting tollgated anyway", self.name)

        tollgated_args = [self.bin_dir / "tollgated", "-c", self.workdir / "tollgate.yaml"]
        if self.demand:
            tollgated_args += ["--demand", str(self.demand)]
        self._spawn("tollgated", tollgated_args)

        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if self.processes["tollgated"].poll() is not None:
                raise TollgateRsError(
                    f"{self.name}: tollgated exited at startup — see {self.logs / 'tollgated.log'}"
                )
            if self.control_socket.exists():
                try:
                    self.snapshot()
                    return
                except (OSError, json.JSONDecodeError):
                    pass
            time.sleep(0.5)
        raise TollgateRsError(f"{self.name}: tollgated never served a snapshot on {self.control_socket}")

    def _mint_ready(self) -> bool:
        try:
            with urllib.request.urlopen(
                f"{self._private_url()}/v1/keysets", timeout=2
            ) as resp:
                return resp.status == 200
        except OSError:
            return False

    def stop(self) -> None:
        for proc_name in ("tollgated", "merchantd", "mintd"):
            proc = self.processes.pop(proc_name, None)
            if proc is None or proc.poll() is not None:
                continue
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        shutil.rmtree(self.runtime, ignore_errors=True)

    def restart(self) -> None:
        """Stop and start again, keeping every file — the persistence path."""
        self.stop()
        self.start()

    # -- the assertion surface ----------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """One JSON snapshot from the control socket, like `nc -U` does in
        tollgate-rs's own topologies."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(str(self.control_socket))
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        if not chunks:
            raise TollgateRsError(f"{self.name}: empty snapshot from control socket")
        return json.loads(b"".join(chunks).decode())

    def peer(self, pubkey: Optional[str] = None) -> dict[str, Any]:
        peers = self.snapshot().get("peers", [])
        if pubkey is None:
            return peers[0] if peers else {}
        return next((p for p in peers if p.get("pubkey") == pubkey), {})

    def peer_field(self, field: str, pubkey: Optional[str] = None) -> Any:
        return self.peer(pubkey).get(field)

    def budget_file(self) -> Path:
        return state_dir() / f"budgets-{self.name}.json"

    def logs_of(self, proc_name: str, lines: int = 40) -> str:
        path = self.logs / f"{proc_name}.log"
        if not path.exists():
            return ""
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def wait_for(label: str, check: Callable[[], Any], timeout: float = 90.0, interval: float = 0.5) -> Any:
    """Poll until check() is truthy. tollgate-rs's own rule: real nodes on
    real sockets mean nothing is instant, and a deadline beats a fixed
    sleep."""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = check()
        if last:
            return last
        time.sleep(interval)
    raise TollgateRsError(f"timed out waiting for {label} (last: {last!r})")


class TollgateRsPair:
    """A gateway that sells and a client that buys from it, on one host.

    Ports sit displaced from the tollgate-rs defaults (which are 4747/4748,
    3338/3337, 3340) so a lane never fights a packaged node — or the Cashu
    mints that already live on lab hosts — for a listener.
    """

    def __init__(
        self,
        bin_dir: Path,
        workdir: Path,
        demand: int = 2_000_000,
        gateway_grants_extra: Optional[dict[str, Any]] = None,
        client_channels: Optional[dict[str, Any]] = None,
    ) -> None:
        workdir.mkdir(parents=True, exist_ok=True)
        self.gateway = TollgateRsNode(
            name="prta-gw",
            bin_dir=bin_dir,
            workdir=workdir / "gateway",
            secret_key=GATEWAY_SECRET,
            listen_port=14747,
            mint_public_port=13338,
            mint_private_port=13337,
            merchant_port=13340,
            grants_extra=gateway_grants_extra or {},
        )
        self.client = TollgateRsNode(
            name="prta-cl",
            bin_dir=bin_dir,
            workdir=workdir / "client",
            secret_key=CLIENT_SECRET,
            listen_port=14847,
            mint_public_port=13448,
            mint_private_port=13447,
            merchant_port=13440,
            peers={GATEWAY_PUBKEY: {"endpoint": "127.0.0.1:14747"}},
            buying={"window_ms": 30000, "renew_lead_ms": 300},
            demand=demand,
            channels=client_channels,
        )
        self.workdir = workdir

    def clean_state(self) -> None:
        """Budget files survive restarts by design; a fresh run removes the
        ones this lane owns so runs are hermetic."""
        for node in (self.gateway, self.client):
            budget = node.budget_file()
            if budget.exists():
                budget.unlink()

    def start(self) -> None:
        self.clean_state()
        self.gateway.start()
        self.client.start()

    def stop(self) -> None:
        self.client.stop()
        self.gateway.stop()

    def dump_state(self) -> str:
        out = ["--- snapshots and logs on failure ---"]
        for node in (self.gateway, self.client):
            try:
                out.append(f"[{node.name} snapshot] {json.dumps(node.snapshot())}")
            except Exception as exc:  # noqa: BLE001 - diagnostic dump
                out.append(f"[{node.name} snapshot] unavailable: {exc}")
            out.append(f"[{node.name} tollgated.log tail]\n{node.logs_of('tollgated')}")
        return "\n".join(out)
