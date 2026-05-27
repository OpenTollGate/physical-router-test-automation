#!/usr/bin/env python3
"""
Publish an HTML dashboard directory to nsite via Blossom server.
No nsyte/deno required — uses only Python + nostr-sdk + requests.

Usage:
    python3 scripts/publish-nsite.py \
        --dashboard-dir /tmp/e2e-dashboard \
        --nsec <hex_nsec> \
        --blossom-server https://blossom.orangesync.tech \
        --relay wss://ngit.orangesync.tech

Setup (one-time):
    pip install nostr-sdk requests
    # Install deno + nsyte (alternative):
    #   curl -fsSL https://deno.land/install.sh | sh
    #   ~/.deno/bin/deno compile --output ~/.local/bin/nsyte https://github.com/sandwichfarm/nsyte.git/src/cli.ts
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("requests not installed: pip install requests")

try:
    from nostr_sdk import Keys, SecretKey, EventBuilder, Client, NostrSigner, Tag, TagKind, Kind
    HAS_NOSTR_SDK = True
except ImportError:
    HAS_NOSTR_SDK = False

def _keys_from_hex(nsec_hex: str) -> Keys:
    sk = SecretKey.from_bytes(bytes.fromhex(nsec_hex))
    return Keys(sk)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upload_to_blossom(file_path: Path, blossom_server: str, nsec_hex: str, npub_hex: str) -> str:
    with open(file_path, "rb") as f:
        data = f.read()

    x_hash = sha256_bytes(data)
    size = len(data)
    rel_path = file_path.name

    auth_event = _create_auth_event(x_hash, "upload", nsec_hex, npub_hex)

    url = f"{blossom_server}/upload"
    headers = {
        "Authorization": f"Nostr {auth_event}",
        "Content-Type": "application/octet-stream",
    }

    resp = requests.put(url, data=data, headers=headers, timeout=120)
    if resp.status_code not in (200, 201):
        print(f"  WARN: upload {rel_path} returned {resp.status_code}: {resp.text[:200]}")
        return ""

    result = resp.json()
    return result.get("url", f"{blossom_server}/{x_hash}")


def _create_auth_event(x_hash: str, action: str, nsec_hex: str, npub_hex: str) -> str:
    if not HAS_NOSTR_SDK:
        return _manual_auth_event(x_hash, action, nsec_hex, npub_hex)

    keys = _keys_from_hex(nsec_hex)
    signer = NostrSigner.keys(keys)
    client = Client(signer)

    event = (
        EventBuilder(Kind(24242), f"Upload {x_hash}")
        .tags([
            ["t", action],
            ["x", x_hash],
            ["expiration", str(int(time.time()) + 86400)],
        ])
    )

    output = client.sign_event_builder(event)
    return json.dumps([
        0,
        keys.public_key().to_hex(),
        output.created_at(),
        24242,
        [["t", action], ["x", x_hash], ["expiration", str(int(time.time()) + 86400)]],
        f"Upload {x_hash}",
    ])


def _manual_auth_event(x_hash: str, action: str, nsec_hex: str, npub_hex: str) -> str:
    tags = [["t", action], ["x", x_hash], ["expiration", str(int(time.time()) + 86400)]]
    event = [0, npub_hex, int(time.time()), 24242, tags, f"Upload {x_hash}"]
    event_id = sha256_bytes(json.dumps(event, separators=(",", ":")).encode())
    import base64
    return base64.b64encode(json.dumps(event).encode()).decode()


def publish_nsite_event(nsec_hex: str, file_map: dict, blossom_server: str, relay: str, dashboard_dir: str):
    if not HAS_NOSTR_SDK:
        print("  Skipping nsite event (nostr-sdk not available)")
        return

    keys = _keys_from_hex(nsec_hex)
    signer = NostrSigner.keys(keys)
    client = Client(signer)
    client.add_relay(relay)
    client.connect()

    npub = keys.public_key().to_hex()

    tags = [["d", npub]]
    for path, url in sorted(file_map.items()):
        tags.append(["x", sha256_bytes(path.encode())])
        tags.append(["url", url])

    event = EventBuilder(Kind(34128), json.dumps(file_map)).tags(tags)
    output = client.send_event_builder(event)
    print(f"  Published nsite Kind 34128 event: {output.id}")
    return output.id


def main():
    parser = argparse.ArgumentParser(description="Publish dashboard to nsite via Blossom")
    parser.add_argument("--dashboard-dir", required=True, help="Directory with index.html + assets")
    parser.add_argument("--nsec", required=True, help="Hex nsec for signing")
    parser.add_argument("--blossom-server", default="https://blossom.orangesync.tech")
    parser.add_argument("--relay", default="wss://ngit.orangesync.tech")
    args = parser.parse_args()

    dashboard = Path(args.dashboard_dir)
    if not dashboard.is_dir():
        sys.exit(f"Dashboard dir not found: {dashboard}")

    nsec_hex = args.nsec.replace("nsec1", "")

    if HAS_NOSTR_SDK:
        keys = _keys_from_hex(nsec_hex)
        npub_hex = keys.public_key().to_hex()
    else:
        npub_hex = hashlib.sha256(bytes.fromhex(nsec_hex)).hexdigest()

    print(f"Publishing to nsite...")
    print(f"  npub: {npub_hex[:16]}...")
    print(f"  blossom: {args.blossom_server}")
    print(f"  relay: {args.relay}")

    file_map = {}
    for f in sorted(dashboard.rglob("*")):
        if f.is_file():
            rel = str(f.relative_to(dashboard))
            print(f"  Uploading: {rel} ({f.stat().st_size} bytes)")
            url = upload_to_blossom(f, args.blossom_server, nsec_hex, npub_hex)
            if url:
                file_map["/" + rel] = url
                print(f"    -> {url[:80]}")

    if file_map:
        publish_nsite_event(nsec_hex, file_map, args.blossom_server, args.relay, args.dashboard_dir)

    nsite_url = f"https://nsite.orangesync.tech/{npub_hex}/"
    print(f"\n  Dashboard: {nsite_url}")
    print(f"  Files uploaded: {len(file_map)}")

    return nsite_url


if __name__ == "__main__":
    main()
