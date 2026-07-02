#!/usr/bin/env python3
"""E2E test: Cashu pay -> JWT -> WG peer -> tunnel.

Tests the full payment-gated VPN flow:
1. Mint a test Cashu token from testnut.cashu.exchange
2. Pay at tollgate-auth (nodns.shop) with server_id
3. Receive JWT + endpoint config
4. POST JWT to VPN server's wg-jwt-peer
5. Verify WG peer was added
6. Connect WireGuard and ping through tunnel
7. Publish results to Nostr/Blossom

Usage:
    python3 conwrt/test_vpn_e2e.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOLLGATE_AUTH = "https://nodns.shop"
VPN_SERVER_ID = "vpn-shc-860"
VPN_SERVER_IP = "66.92.204.236"
VPN_PEER_API = f"http://{VPN_SERVER_IP}:8082"
TESTNUT_MINT = "https://testnut.cashu.exchange"
NSEC_FILE = os.environ.get("NSEC_FILE", str(Path.home() / ".config" / "prta" / "nsec"))

SUITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE_ROOT))


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def curl(url: str, method: str = "GET", data: dict | None = None, headers: dict | None = None, timeout: int = 30) -> tuple[int, dict | str]:
    cmd = ["curl", "-sf", "-X", method, "--connect-timeout", "10", "--max-time", str(timeout)]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    if data:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(data)]
    cmd.append(url)
    rc, out, err = run(cmd, timeout=timeout + 5)
    if rc != 0:
        return rc, err or out
    try:
        return 0, json.loads(out)
    except json.JSONDecodeError:
        return 0, out


def mint_testnuts(amount_sats: int = 10) -> str | None:
    """Mint test Cashu tokens from testnut.cashu.exchange."""
    rc, out, err = run([
        "curl", "-sf", "-X", "POST",
        f"{TESTNUT_MINT}/v1/mint/quote/bolt11",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"unit": "sat", "amount": amount_sats}),
    ])
    if rc != 0:
        print(f"  mint quote failed: {err}")
        return None
    quote = json.loads(out)
    invoice = quote.get("request", "")
    quote_id = quote.get("quote", "")
    if not invoice or not quote_id:
        print(f"  no invoice in quote: {out}")
        return None

    # FakeWallet auto-pays the invoice
    print(f"  invoice: {invoice[:50]}...")
    time.sleep(3)

    # Mint tokens
    rc, out, err = run([
        "curl", "-sf", "-X", "POST",
        f"{TESTNUT_MINT}/v1/mint/bolt11",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"quote": quote_id, "outputs": []}),
    ])
    if rc != 0:
        print(f"  mint failed: {err}")
        return None
    signatures = json.loads(out).get("signatures", [])
    if not signatures:
        print(f"  no signatures: {out}")
        return None

    token_obj = {
        "token": [{"mint": TESTNUT_MINT, "proofs": [{"amount": s["amount"], "id": s["id"], "secret": s["secret"], "C": s["C"]} for s in signatures]}],
        "unit": "sat",
        "memo": "VPN E2E test",
    }
    token_json = json.dumps(token_obj)
    import base64
    return "cashuA" + base64.urlsafe_b64encode(token_json.encode()).decode().rstrip("=")


def test_vpn_e2e() -> dict:
    results = {"tests": {}, "passed": 0, "failed": 0}

    # 1. Mint testnuts
    print("1. Minting testnuts...")
    token = mint_testnuts(10)
    if not token:
        results["tests"]["mint"] = False
        results["failed"] += 1
        return results
    print(f"   token: {token[:40]}...")
    results["tests"]["mint"] = True
    results["passed"] += 1

    # 2. Generate client WG keypair
    print("2. Generating client WG keypair...")
    rc, priv, err = run(["wg", "genkey"])
    if rc != 0:
        print(f"   wg genkey failed: {err}")
        results["tests"]["wg_keygen"] = False
        results["failed"] += 1
        return results
    rc, pub, err = run(["bash", "-c", f"echo '{priv}' | wg pubkey"])
    if rc != 0:
        print(f"   wg pubkey failed: {err}")
        results["tests"]["wg_keygen"] = False
        results["failed"] += 1
        return results
    print(f"   client pubkey: {pub}")
    results["tests"]["wg_keygen"] = True
    results["passed"] += 1

    # 3. Pay at tollgate-auth
    print("3. Paying at tollgate-auth (nodns.shop)...")
    rc, resp = curl(
        f"{TOLLGATE_AUTH}/v1/wg/connect",
        method="POST",
        data={"token": token, "pubkey": pub, "server_id": VPN_SERVER_ID},
        timeout=60,
    )
    if rc != 0 or not isinstance(resp, dict) or "jwt" not in resp:
        print(f"   payment failed: rc={rc} resp={resp}")
        results["tests"]["payment"] = False
        results["failed"] += 1
        return results
    jwt_token = resp["jwt"]
    endpoint = resp.get("endpoint", "")
    server_pubkey = resp.get("server_pubkey", "")
    client_ip = resp.get("client_ip", "")
    expires_at = resp.get("expires_at", 0)
    print(f"   JWT received: {jwt_token[:40]}...")
    print(f"   endpoint: {endpoint}")
    print(f"   client_ip: {client_ip}")
    results["tests"]["payment"] = True
    results["passed"] += 1

    # 4. POST JWT to VPN server
    print("4. Submitting JWT to VPN server (wg-jwt-peer)...")
    rc, resp2 = curl(
        f"{VPN_PEER_API}/peer",
        method="POST",
        data={"jwt": jwt_token},
        timeout=15,
    )
    if rc != 0 or not isinstance(resp2, dict) or resp2.get("status") != "connected":
        print(f"   peer add failed: rc={rc} resp={resp2}")
        results["tests"]["jwt_peer"] = False
        results["failed"] += 1
        return results
    print(f"   peer added: {resp2}")
    results["tests"]["jwt_peer"] = True
    results["passed"] += 1

    # 5. Verify peer on VPN server
    print("5. Verifying WG peer...")
    rc, resp3 = curl(f"{VPN_PEER_API}/peers", headers={"Authorization": "Bearer e2e-test-secret"})
    peer_found = False
    if isinstance(resp3, dict):
        for p in resp3.get("peers", []):
            if p.get("pubkey") == pub:
                peer_found = True
                print(f"   peer confirmed: ip={p['allowed_ip']}")
    if not peer_found:
        print("   peer NOT found in peer list")
        results["tests"]["peer_verify"] = False
        results["failed"] += 1
    else:
        results["tests"]["peer_verify"] = True
        results["passed"] += 1

    # 6. Connect WireGuard and ping
    print("6. Connecting WireGuard tunnel...")
    wg_host = endpoint.split(":")[0] if ":" in endpoint else endpoint
    wg_port = endpoint.split(":")[1] if ":" in endpoint else "51820"
    run(["sudo", "ip", "link", "del", "wgtest"], timeout=5)
    run(["sudo", "ip", "link", "add", "dev", "wgtest", "type", "wireguard"], timeout=5)
    run(["sudo", "ip", "addr", "add", f"{client_ip}/32", "dev", "wgtest"], timeout=5)
    run(["sudo", "ip", "route", "add", "10.66.42.0/24", "dev", "wgtest"], timeout=5)
    run(["bash", "-c", f"echo '{priv}' | sudo wg set wgtest peer '{server_pubkey}' endpoint {wg_host}:{wg_port} allowed-ips 10.66.42.0/24 persistent-keepalive 1 private-key /dev/stdin"], timeout=5)
    run(["sudo", "ip", "link", "set", "wgtest", "up"], timeout=5)
    time.sleep(3)

    rc, ping_out, _ = run(["ping", "-c", "3", "-W", "2", "10.66.42.1"], timeout=10)
    if "0% packet loss" in ping_out or "3 received" in ping_out:
        print(f"   tunnel works! {ping_out.splitlines()[-1]}")
        results["tests"]["tunnel"] = True
        results["passed"] += 1
    else:
        print(f"   tunnel failed: {ping_out}")
        results["tests"]["tunnel"] = False
        results["failed"] += 1

    # Cleanup
    run(["sudo", "ip", "link", "del", "wgtest"], timeout=5)

    return results


def main():
    print("=" * 60)
    print("VPN Payment E2E Test")
    print("=" * 60)
    print()

    results = test_vpn_e2e()

    print()
    print("=" * 60)
    total = results["passed"] + results["failed"]
    print(f"Results: {results['passed']}/{total} passed, {results['failed']} failed")
    for name, passed in results["tests"].items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
    print("=" * 60)

    # Write results for publishing
    results_dir = Path("/tmp/vpn-e2e-results")
    results_dir.mkdir(exist_ok=True)
    (results_dir / "summary.md").write_text(
        f"# VPN Payment E2E Test\n\n"
        f"**Date:** {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n"
        f"## Results: {results['passed']}/{total} passed\n\n"
        f"| Test | Status |\n|------|--------|\n"
        + "\n".join(f"| {n} | {'PASS' if p else 'FAIL'} |" for n, p in results["tests"].items())
    )
    (results_dir / "comparison.json").write_text(json.dumps(results, indent=2))

    # Publish to Nostr if nsec available
    nsec_path = Path(NSEC_FILE)
    if nsec_path.exists():
        print("\nPublishing to Nostr...")
        os.environ["PROJECT_TAG"] = "conwrt"
        sys.path.insert(0, str(SUITE_ROOT))
        try:
            from lib.result_publisher import publish_results
            publish_results(
                results_dir=str(results_dir),
                nsec_file=str(nsec_path),
                run_id=f"conwrt-vpn-e2e-{int(time.time())}",
                blossom_server="https://blossom.psbt.me",
                relays=["wss://relay.cashu.email"],
                metadata={
                    "project": "conwrt",
                    "summary": f"VPN payment e2e: {results['passed']}/{total} passed",
                    "passed": results["passed"],
                    "failed": results["failed"],
                },
            )
            print("Published to tests.tollgate.me")
        except Exception as e:
            print(f"Publish failed: {e}")

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
