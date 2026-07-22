"""Parity test: tollgate-module-basic-rust vs tollgate-module-basic-go.

Validates the Rust binary is a drop-in replacement for the Go binary by
running identical HTTP requests against both and comparing the responses.

SKIPPED unless both binaries are available and runnable locally.

Run with::

    pytest -m parity tests/api/test_go_rust_basic_parity.py -v

Environment overrides::

    TOLLGATE_GO_BINARY       Path to Go binary (default: auto-detect)
    TOLLGATE_BINARY_PATH     Path to Rust binary (default: target/release)
    TOLLGATE_HTTP_PORT       HTTP port (default: 2121)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any

import pytest
import requests

pytestmark = [pytest.mark.parity, pytest.mark.api, pytest.mark.extended]

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

DEFAULT_RUST_BINARY = (
    "/home/ubuntu/src/tollgate-module-basic-rust/"
    "target/release/tollgate-module-basic-rust"
)

GO_BINARY_CANDIDATES = [
    os.environ.get("TOLLGATE_GO_BINARY"),
    "/tmp/tollgate-go-binary",
    "/home/ubuntu/src/tollgate-module-basic-go/src/tollgate-module-basic-go",
    shutil.which("tollgate-module-basic-go"),
]

DEFAULT_HTTP_PORT = 2121

# Minimal config.json accepted by BOTH backends.
# Matches the existing rust_basic_server fixture config for consistency.
PARITY_CONFIG: dict[str, Any] = {
    "config_version": "v0.0.7",
    "log_level": "warn",
    "metric": "milliseconds",
    "step_size": 5000,
    "margin": 0.1,
    "accepted_mints": [
        {
            "url": "https://testnut.cashu.exchange",
            "min_balance": 0,
            "balance_tolerance_percent": 0,
            "payout_interval_seconds": 60,
            "min_payout_amount": 0,
            "price_per_step": 1,
            "price_unit": "sats",
            "purchase_min_steps": 0,
        }
    ],
    "profit_share": [{"factor": 1.0, "identity": "owner"}],
}

# Endpoints to probe — each test function compares the pre-collected pair.
ENDPOINTS = ["GET /", "GET /balance", "GET /usage", "GET /whoami", "POST /"]


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


def _find_go_binary() -> str | None:
    """Return the first executable Go binary path, or None."""
    for path in GO_BINARY_CANDIDATES:
        if path and os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return None


def _find_rust_binary() -> str | None:
    """Return the Rust binary path if it exists and is executable."""
    path = os.environ.get("TOLLGATE_BINARY_PATH", DEFAULT_RUST_BINARY)
    if os.path.exists(path) and os.access(path, os.X_OK):
        return path
    return None


# ---------------------------------------------------------------------------
# Environment setup helpers
# ---------------------------------------------------------------------------


def _is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if the TCP port is currently unbound.

    Uses SO_REUSEADDR to avoid false negatives from TIME_WAIT sockets
    left by a recently-terminated binary (matches conftest.py pattern).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return True
    except OSError:
        return False


def _ensure_etc_tollgate_writable() -> None:
    """Ensure /etc/tollgate exists and is writable.

    The Rust binary hard-codes /etc/tollgate for wallet_seed.bin and
    wallet.sqlite (main.rs:43) instead of honoring TOLLGATE_TEST_CONFIG_DIR
    for wallet state. We must sudo-create it and chown to the current user.
    """
    if os.path.isdir("/etc/tollgate") and os.access("/etc/tollgate", os.W_OK):
        return  # already writable

    cmd: list[str]
    if os.geteuid() == 0:
        cmd = ["mkdir", "-p", "/etc/tollgate"]
    else:
        cmd = ["sudo", "-n", "mkdir", "-p", "/etc/tollgate"]
    subprocess.run(cmd, capture_output=True, check=False)

    if os.geteuid() != 0:
        subprocess.run(
            ["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", "/etc/tollgate"],
            capture_output=True,
            check=False,
        )


def _clean_etc_tollgate_wallet() -> None:
    """Remove wallet state files from /etc/tollgate to avoid cross-run leaks."""
    for fname in ("wallet_seed.bin", "wallet.sqlite", "wallet.sqlite-shm",
                  "wallet.sqlite-wal", "wallet.db"):
        try:
            os.unlink(f"/etc/tollgate/{fname}")
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _inject_dhcp_leases() -> bytes | None:
    """Back up /tmp/dhcp.leases and inject a fake 127.0.0.1 entry.

    The Go binary resolves client MAC from /tmp/dhcp.leases (then /proc/net/arp).
    Without a lease entry, /whoami, /usage, and POST / all fail with HTTP 500
    ("mac-address-lookup-failed"), which obscures real parity comparisons.

    Returns the original file content (or None if no file existed) for restore.
    """
    original: bytes | None
    try:
        with open("/tmp/dhcp.leases", "rb") as f:
            original = f.read()
    except FileNotFoundError:
        original = None

    # Inject a fake lease: 127.0.0.1 -> 00:11:22:33:44:55
    fake_entry = (
        f"{int(time.time())} 00:11:22:33:44:55 127.0.0.1 "
        f"parity-test *\n"
    )
    try:
        with open("/tmp/dhcp.leases", "w") as f:
            f.write(fake_entry)
    except OSError:
        pass  # non-fatal — some tests may still work

    return original


def _restore_dhcp_leases(original: bytes | None) -> None:
    """Restore /tmp/dhcp.leases to its original state."""
    try:
        if original is None:
            os.unlink("/tmp/dhcp.leases")
        else:
            with open("/tmp/dhcp.leases", "wb") as f:
                f.write(original)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Binary spawn + response collection
# ---------------------------------------------------------------------------


def _spawn_and_wait(
    binary_path: str,
    config_dir: str,
    http_port: int,
    label: str,
    timeout_seconds: float = 15.0,
) -> subprocess.Popen[bytes] | None:
    """Spawn a tollgate binary and wait for it to bind http_port.

    Returns the Popen object on success, or None if the binary exited
    early or failed to bind within the timeout.
    """
    env = {**os.environ, "TOLLGATE_TEST_CONFIG_DIR": config_dir}
    proc = subprocess.Popen(
        [binary_path],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            # Binary exited early
            output = ""
            if proc.stdout:
                output = proc.stdout.read().decode(errors="replace")
            print(f"\n[{label}] Binary exited early (code={proc.returncode}):\n{output[:2000]}")
            return None
        try:
            with socket.create_connection(("127.0.0.1", http_port), timeout=0.2):
                return proc  # bound successfully
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)

    # Timeout
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print(f"\n[{label}] Binary did not bind 127.0.0.1:{http_port} within {timeout_seconds}s")
    return None


def _stop_binary(proc: subprocess.Popen[bytes]) -> None:
    """Terminate a spawned binary, escalating to SIGKILL if needed."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _collect_responses(
    binary_path: str,
    config_dir: str,
    http_port: int,
    label: str,
) -> dict[str, tuple[int, str, str]] | None:
    """Spawn binary, hit every endpoint, stop binary, return response map.

    Returns a dict mapping endpoint names to (status_code, body, content_type).
    Returns None if the binary could not be started.
    """
    proc = _spawn_and_wait(binary_path, config_dir, http_port, label)
    if proc is None:
        return None

    base_url = f"http://127.0.0.1:{http_port}"
    responses: dict[str, tuple[int, str, str]] = {}

    try:
        request_specs = [
            ("GET /", lambda: requests.get(f"{base_url}/", timeout=5)),
            ("GET /balance", lambda: requests.get(f"{base_url}/balance", timeout=5)),
            ("GET /usage", lambda: requests.get(f"{base_url}/usage", timeout=5)),
            ("GET /whoami", lambda: requests.get(f"{base_url}/whoami", timeout=5)),
            (
                "POST /",
                lambda: requests.post(
                    f"{base_url}/",
                    data="garbage-invalid-token",
                    headers={"Content-Type": "text/plain"},
                    timeout=5,
                ),
            ),
        ]

        for name, fetch in request_specs:
            try:
                resp = fetch()
                responses[name] = (
                    resp.status_code,
                    resp.text.strip(),
                    resp.headers.get("content-type", ""),
                )
            except requests.RequestException as exc:
                responses[name] = (-1, f"<request error: {exc}>", "")
    finally:
        _stop_binary(proc)

    return responses


# ---------------------------------------------------------------------------
# Diff table helper
# ---------------------------------------------------------------------------


def _format_diff_table(endpoint: str, go: tuple[int, str, str], rust: tuple[int, str, str]) -> str:
    """Build a human-readable side-by-side diff table for assertion failures."""
    go_status, go_body, go_ct = go
    rust_status, rust_body, rust_ct = rust

    lines = [
        "",
        "=" * 90,
        f"  PARITY DIVERGENCE: {endpoint}",
        "=" * 90,
        f"  {'Field':<22} {'Go':<45} {'Rust':<45}",
        f"  {'-' * 22} {'-' * 45} {'-' * 45}",
        f"  {'status_code':<22} {go_status:<45} {rust_status:<45}",
        f"  {'content_type':<22} {go_ct:<45} {rust_ct:<45}",
    ]

    # Try to parse both bodies as JSON for field-level comparison
    try:
        go_json = json.loads(go_body)
        rust_json = json.loads(rust_body)
        go_keys = set(go_json.keys()) if isinstance(go_json, dict) else set()
        rust_keys = set(rust_json.keys()) if isinstance(rust_json, dict) else set()

        if go_keys or rust_keys:
            all_keys = sorted(go_keys | rust_keys)
            lines.append(f"  {'-' * 22} {'-' * 45} {'-' * 45}")
            lines.append(f"  {'json_fields':<22} {str(sorted(go_keys)):<45} {str(sorted(rust_keys)):<45}")
            lines.append(f"  {'-' * 22} {'-' * 45} {'-' * 45}")
            for key in all_keys:
                go_val = _truncate(json.dumps(go_json.get(key, "<MISSING>")), 43)
                rust_val = _truncate(json.dumps(rust_json.get(key, "<MISSING>")), 43)
                marker = " " if key in go_keys and key in rust_keys else ("+" if key in rust_keys else "-")
                lines.append(f"  {marker} {key:<20} {go_val:<45} {rust_val:<45}")

            # If both have 'tags', compare tag names
            if isinstance(go_json.get("tags"), list) and isinstance(rust_json.get("tags"), list):
                go_tag_names = {t[0] for t in go_json["tags"] if isinstance(t, list) and t}
                rust_tag_names = {t[0] for t in rust_json["tags"] if isinstance(t, list) and t}
                lines.append(f"  {'-' * 22} {'-' * 45} {'-' * 45}")
                lines.append(f"  {'tag_names':<22} {str(sorted(go_tag_names)):<45} {str(sorted(rust_tag_names)):<45}")
                only_go = go_tag_names - rust_tag_names
                only_rust = rust_tag_names - go_tag_names
                if only_go:
                    lines.append(f"  {'  only in Go':<22} {str(sorted(only_go)):<45}")
                if only_rust:
                    lines.append(f"  {'  only in Rust':<68} {str(sorted(only_rust)):<45}")
    except (json.JSONDecodeError, TypeError):
        # Not JSON — show raw bodies (truncated)
        lines.append(f"  {'-' * 22} {'-' * 45} {'-' * 45}")
        lines.append(f"  {'body':<22} {_truncate(go_body, 43):<45} {_truncate(rust_body, 43):<45}")

    lines.append("=" * 90)
    return "\n".join(lines)


def _truncate(s: str, maxlen: int) -> str:
    """Truncate string to maxlen, adding ellipsis if cut."""
    s = s.replace("\n", "\\n")
    return s if len(s) <= maxlen else s[: max(0, maxlen - 3)] + "..."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def go_responses():
    """Start Go binary, collect all endpoint responses, stop.

    Skips if no Go binary is found or if it fails to bind port 2121.
    """
    go_bin = _find_go_binary()
    if not go_bin:
        pytest.skip(
            "No Go binary available — build with: "
            "cd /home/ubuntu/src/tollgate-module-basic-go && "
            "go build -o /tmp/tollgate-go-binary ./src/"
        )

    http_port = int(os.environ.get("TOLLGATE_HTTP_PORT", DEFAULT_HTTP_PORT))
    if not _is_port_free(http_port):
        pytest.skip(f"Port {http_port} in use — cannot start Go binary")

    # Inject fake DHCP lease so Go binary can resolve 127.0.0.1 MAC
    dhcp_backup = _inject_dhcp_leases()

    config_dir = tempfile.mkdtemp(prefix="tollgate-parity-go-")
    with open(os.path.join(config_dir, "config.json"), "w") as f:
        json.dump(PARITY_CONFIG, f)

    try:
        responses = _collect_responses(go_bin, config_dir, http_port, "Go")
    finally:
        _restore_dhcp_leases(dhcp_backup)
        shutil.rmtree(config_dir, ignore_errors=True)

    if responses is None:
        pytest.skip("Go binary failed to start or bind port 2121")

    print(f"\n[Go] Collected responses from {go_bin}")
    for ep, (code, body, _) in responses.items():
        print(f"  {ep:<18} → {code}  {_truncate(body, 60)}")

    return responses


@pytest.fixture(scope="module")
def rust_responses():
    """Start Rust binary, collect all endpoint responses, stop.

    Skips if no Rust binary is found or if it fails to bind port 2121.
    """
    _ensure_etc_tollgate_writable()

    rust_bin = _find_rust_binary()
    if not rust_bin:
        pytest.skip(
            f"Rust binary not found at {DEFAULT_RUST_BINARY}. "
            "Build with: cd /home/ubuntu/src/tollgate-module-basic-rust && cargo build --release"
        )

    http_port = int(os.environ.get("TOLLGATE_HTTP_PORT", DEFAULT_HTTP_PORT))

    # Wait for port to be freed (previous Go binary may leave TIME_WAIT socket)
    for _ in range(10):
        if _is_port_free(http_port):
            break
        time.sleep(0.5)
    else:
        pytest.skip(f"Port {http_port} in use — cannot start Rust binary")

    dhcp_backup = _inject_dhcp_leases()

    config_dir = tempfile.mkdtemp(prefix="tollgate-parity-rust-")
    with open(os.path.join(config_dir, "config.json"), "w") as f:
        json.dump(PARITY_CONFIG, f)

    try:
        responses = _collect_responses(rust_bin, config_dir, http_port, "Rust")
    finally:
        _restore_dhcp_leases(dhcp_backup)
        shutil.rmtree(config_dir, ignore_errors=True)
        _clean_etc_tollgate_wallet()

    if responses is None:
        pytest.skip("Rust binary failed to start or bind port 2121")

    print(f"\n[Rust] Collected responses from {rust_bin}")
    for ep, (code, body, _) in responses.items():
        print(f"  {ep:<18} → {code}  {_truncate(body, 60)}")

    return responses


# ---------------------------------------------------------------------------
# Parity assertion helper
# ---------------------------------------------------------------------------


def _assert_parity(
    endpoint: str,
    go_responses: dict,
    rust_responses: dict,
    check: str,
    go_val: Any,
    rust_val: Any,
) -> None:
    """Assert two values match, printing a diff table on failure."""
    assert go_val == rust_val, (
        f"\nPARITY CHECK FAILED [{check}] for {endpoint}:\n"
        f"  Go   = {go_val!r}\n"
        f"  Rust = {rust_val!r}\n"
        + _format_diff_table(endpoint, go_responses[endpoint], rust_responses[endpoint])
    )


# ---------------------------------------------------------------------------
# Tests: Discovery (GET /)
# ---------------------------------------------------------------------------


def test_parity_discovery_status(go_responses, rust_responses):
    """GET / returns HTTP 200 on both backends."""
    go_code = go_responses["GET /"][0]
    rust_code = rust_responses["GET /"][0]
    _assert_parity("GET /", go_responses, rust_responses, "status_code", go_code, rust_code)
    assert go_code == 200


def test_parity_discovery_kind(go_responses, rust_responses):
    """GET / returns Nostr event kind 10021 on both backends."""
    go_data = json.loads(go_responses["GET /"][1])
    rust_data = json.loads(rust_responses["GET /"][1])
    _assert_parity(
        "GET /", go_responses, rust_responses, "kind",
        go_data.get("kind"), rust_data.get("kind"),
    )
    assert go_data["kind"] == 10021


def test_parity_discovery_field_set(go_responses, rust_responses):
    """GET / returns the same JSON field set on both backends."""
    go_data = json.loads(go_responses["GET /"][1])
    rust_data = json.loads(rust_responses["GET /"][1])
    go_keys = set(go_data.keys())
    rust_keys = set(rust_data.keys())
    _assert_parity("GET /", go_responses, rust_responses, "field_set", go_keys, rust_keys)


def test_parity_discovery_tag_names(go_responses, rust_responses):
    """GET / includes the same tag names (first element of each tag)."""
    go_data = json.loads(go_responses["GET /"][1])
    rust_data = json.loads(rust_responses["GET /"][1])
    go_tags = {t[0] for t in go_data.get("tags", []) if isinstance(t, list) and t}
    rust_tags = {t[0] for t in rust_data.get("tags", []) if isinstance(t, list) and t}
    _assert_parity("GET /", go_responses, rust_responses, "tag_names", go_tags, rust_tags)


# ---------------------------------------------------------------------------
# Tests: Balance (GET /balance)
# ---------------------------------------------------------------------------


def test_parity_balance_status(go_responses, rust_responses):
    """GET /balance returns the same HTTP status on both backends."""
    go_code = go_responses["GET /balance"][0]
    rust_code = rust_responses["GET /balance"][0]
    _assert_parity("GET /balance", go_responses, rust_responses, "status_code", go_code, rust_code)


def test_parity_balance_field_set(go_responses, rust_responses):
    """GET /balance returns the same JSON field set on both backends."""
    go_body = go_responses["GET /balance"][1]
    rust_body = rust_responses["GET /balance"][1]
    go_data = json.loads(go_body)
    rust_data = json.loads(rust_body)
    go_keys = set(go_data.keys()) if isinstance(go_data, dict) else set()
    rust_keys = set(rust_data.keys()) if isinstance(rust_data, dict) else set()
    _assert_parity(
        "GET /balance", go_responses, rust_responses, "field_set", go_keys, rust_keys
    )


# ---------------------------------------------------------------------------
# Tests: Usage (GET /usage)
# ---------------------------------------------------------------------------


def test_parity_usage_status(go_responses, rust_responses):
    """GET /usage returns the same HTTP status on both backends."""
    go_code = go_responses["GET /usage"][0]
    rust_code = rust_responses["GET /usage"][0]
    _assert_parity("GET /usage", go_responses, rust_responses, "status_code", go_code, rust_code)


def test_parity_usage_format(go_responses, rust_responses):
    """GET /usage body matches X/Y integer format on both backends."""
    pattern = re.compile(r"^-?\d+/-?\d+$")
    go_body = go_responses["GET /usage"][1]
    rust_body = rust_responses["GET /usage"][1]
    assert pattern.match(go_body), f"Go /usage body doesn't match X/Y: {go_body!r}"
    assert pattern.match(rust_body), f"Rust /usage body doesn't match X/Y: {rust_body!r}"


# ---------------------------------------------------------------------------
# Tests: Whoami (GET /whoami)
# ---------------------------------------------------------------------------


def test_parity_whoami_status(go_responses, rust_responses):
    """GET /whoami returns the same HTTP status on both backends."""
    go_code = go_responses["GET /whoami"][0]
    rust_code = rust_responses["GET /whoami"][0]
    _assert_parity("GET /whoami", go_responses, rust_responses, "status_code", go_code, rust_code)


def test_parity_whoami_format(go_responses, rust_responses):
    """GET /whoami body contains 'mac=' prefix on both backends."""
    go_body = go_responses["GET /whoami"][1]
    rust_body = rust_responses["GET /whoami"][1]
    assert "mac=" in go_body, f"Go /whoami missing 'mac=' prefix: {go_body!r}"
    assert "mac=" in rust_body, f"Rust /whoami missing 'mac=' prefix: {rust_body!r}"


# ---------------------------------------------------------------------------
# Tests: Invalid token rejection (POST /)
# ---------------------------------------------------------------------------


def test_parity_invalid_token_status(go_responses, rust_responses):
    """POST / with invalid token returns the same HTTP status on both backends."""
    go_code = go_responses["POST /"][0]
    rust_code = rust_responses["POST /"][0]
    _assert_parity("POST /", go_responses, rust_responses, "status_code", go_code, rust_code)


def test_parity_invalid_token_kind(go_responses, rust_responses):
    """POST / with invalid token returns kind 21023 on both backends."""
    go_body = go_responses["POST /"][1]
    rust_body = rust_responses["POST /"][1]
    # Both should return JSON with kind=21023
    try:
        go_data = json.loads(go_body)
        go_kind = go_data.get("kind")
    except json.JSONDecodeError:
        go_kind = None
    try:
        rust_data = json.loads(rust_body)
        rust_kind = rust_data.get("kind")
    except json.JSONDecodeError:
        rust_kind = None
    _assert_parity("POST /", go_responses, rust_responses, "kind_21023", go_kind, rust_kind)
    assert go_kind == 21023, f"Go POST / did not return kind 21023: {go_body[:200]}"
    assert rust_kind == 21023, f"Rust POST / did not return kind 21023: {rust_body[:200]}"


# ---------------------------------------------------------------------------
# Summary test: print full diff table for manual review
# ---------------------------------------------------------------------------


def test_parity_summary_report(go_responses, rust_responses):
    """Print a comprehensive diff table of all endpoints for manual review.

    This test always PASSES — it exists to surface the full comparison
    output in the test log for human inspection.
    """
    print("\n")
    print("#" * 90)
    print("#  PARITY SUMMARY: tollgate-module-basic-go vs tollgate-module-basic-rust")
    print("#" * 90)
    for endpoint in ENDPOINTS:
        go = go_responses.get(endpoint, (0, "<not collected>", ""))
        rust = rust_responses.get(endpoint, (0, "<not collected>", ""))
        print(_format_diff_table(endpoint, go, rust))
    print("\n" + "#" * 90)
    print("#  END PARITY SUMMARY")
    print("#" * 90)
