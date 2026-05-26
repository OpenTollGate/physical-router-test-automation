"""Hetzner snapshot baker — builds a ready-to-run TollGate test VM image.

Provisions a temporary Hetzner server, installs QEMU + deps, downloads
OpenWrt/Debian images, commits the server to a snapshot, then deletes it.

Usage:
    python3 -m lib.cloud_lab.hetzner_snapshot --bake
    python3 -m lib.cloud_lab.hetzner_snapshot --list
    python3 -m lib.cloud_lab.hetzner_snapshot --delete SNAPSHOT_NAME
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

from lib.cloud_lab.constants import (
    HETZNER_API_TOKEN,
    HETZNER_API_URL,
    HETZNER_LOCATION,
    HETZNER_SERVER_TYPE,
    HETZNER_SNAPSHOT_NAME,
    HETZNER_SSH_KEY,
    HETZNER_SSH_KEY_ID,
)
from lib.cloud_lab.hetzner import _run_hcloud, _scp_to_server, _ssh_to_server, _wait_for_ssh


def _ssh(ip: str, cmd: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return _ssh_to_server(ip, cmd, timeout=timeout, check=True)


def _bake(snapshot_name: str, location: str, server_type: str) -> None:
    if not HETZNER_API_TOKEN:
        print("ERROR: HETZNER_API_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)

    tmp_name = f"tollgate-baker-{int(time.time())}"

    print(f"Creating temporary server '{tmp_name}' ({server_type}, {location})...")
    payload: dict[str, object] = {
        "name": tmp_name,
        "server_type": server_type,
        "image": "debian-12",
        "location": location,
        "labels": {"tollgate_baker": "true"},
        "public_net": {"enable_ipv4": True, "enable_ipv6": False},
    }
    if HETZNER_SSH_KEY_ID:
        payload["ssh_keys"] = [int(HETZNER_SSH_KEY_ID)]

    r = _run_hcloud("POST", "/servers", data=payload, timeout=120)
    server = r.get("server", {})
    server_id = server.get("id")
    ipv4 = server.get("public_net", {}).get("ipv4", {})
    server_ip = ipv4.get("ip", "") if isinstance(ipv4, dict) else ""

    if not server_ip:
        for _ in range(30):
            time.sleep(2)
            r2 = _run_hcloud("GET", f"/servers/{server_id}", timeout=15)
            ipv4_info = r2.get("server", {}).get("public_net", {}).get("ipv4", {})
            if isinstance(ipv4_info, dict) and ipv4_info.get("ip"):
                server_ip = ipv4_info["ip"]
                break

    if not server_ip:
        print("ERROR: Server did not get an IP", file=sys.stderr)
        sys.exit(1)

    print(f"Server {server_id} at {server_ip}. Waiting for SSH...")
    if not _wait_for_ssh(server_ip, timeout=300):
        print("ERROR: SSH never came up", file=sys.stderr)
        _run_hcloud("DELETE", f"/servers/{server_id}", timeout=60)
        sys.exit(1)

    print("Installing packages...")
    _ssh(server_ip, "apt-get update && apt-get install -y qemu-system-x86 python3-venv curl git jq", timeout=600)

    print("Creating venv and installing deps...")
    _ssh(
        server_ip,
        "python3 -m venv /opt/tollgate-venv && "
        "/opt/tollgate-venv/bin/pip install --upgrade pip && "
        "mkdir -p /opt/tollgate-test && "
        "cd /opt/tollgate-test && "
        "git clone https://github.com/OpenTollGate/physical-router-test-automation.git . 2>/dev/null || true && "
        "/opt/tollgate-venv/bin/pip install -q -r requirements.txt",
        timeout=600,
    )

    print("Downloading OpenWrt x86_64 image...")
    _ssh(
        server_ip,
        "mkdir -p /opt/tollgate-test/images && "
        "cd /opt/tollgate-test/images && "
        "curl -sL -o openwrt-x86-64-generic-ext4-rootfs.img '"
        "https://downloads.openwrt.org/snapshots/targets/x86/64/openwrt-x86-64-generic-ext4-rootfs.img' && "
        "ls -lh openwrt-x86-64-generic-ext4-rootfs.img",
        timeout=300,
    )

    print("Creating QEMU disk images from base images...")
    _ssh(
        server_ip,
        "cd /opt/tollgate-test/images && "
        "cp openwrt-x86-64-generic-ext4-rootfs.img openwrt-x86-64.img && "
        "qemu-img resize openwrt-x86-64.img 256M && "
        "echo 'Images ready' && ls -lh *.img",
        timeout=120,
    )

    print("Creating working directories...")
    _ssh(
        server_ip,
        "mkdir -p ~/tollgate-virtual-lab /tmp/tollgate-results /var/log",
        timeout=30,
    )

    print("Shutting down server for snapshot...")
    _ssh(server_ip, "poweroff", timeout=30, check=False)
    time.sleep(15)

    print(f"Waiting for server to be off...")
    for _ in range(60):
        r3 = _run_hcloud("GET", f"/servers/{server_id}", timeout=15)
        if r3.get("server", {}).get("status") == "off":
            break
        time.sleep(5)

    print(f"Creating snapshot '{snapshot_name}' from server {server_id}...")
    snap_payload = {
        "description": snapshot_name,
        "type": "snapshot",
    }
    _run_hcloud("POST", f"/servers/{server_id}/actions/create_image", data=snap_payload, timeout=120)

    print("Waiting for image to be created...")
    for _ in range(120):
        img_id = None
        r4 = _run_hcloud("GET", "/images?type=snapshot", timeout=30)
        for img in r4.get("images", []):
            if img.get("description") == snapshot_name or img.get("name") == snapshot_name:
                img_id = img["id"]
                img_status = img.get("status", "unknown")
                if img_status == "available":
                    print(f"Snapshot created: id={img_id} name={img.get('name')} description={snapshot_name}")
                    break
        else:
            time.sleep(10)
            continue
        break

    print(f"Deleting temporary server {server_id}...")
    _run_hcloud("DELETE", f"/servers/{server_id}", timeout=60)

    print("Bake complete!")


def _list_snapshots() -> None:
    r = _run_hcloud("GET", "/images?type=snapshot", timeout=30)
    images = r.get("images", [])
    if not images:
        print("No snapshots found.")
        return
    for img in images:
        print(f"  id={img['id']}  name={img.get('name', 'N/A')}  "
              f"desc={img.get('description', 'N/A')}  "
              f"status={img.get('status', 'N/A')}  "
              f"size={img.get('image_size_gb', '?')}GB  "
              f"created={img.get('created', 'N/A')}")


def _delete_snapshot(name: str) -> None:
    r = _run_hcloud("GET", "/images?type=snapshot", timeout=30)
    for img in r.get("images", []):
        if img.get("name") == name or img.get("description") == name:
            img_id = img["id"]
            print(f"Deleting snapshot '{name}' (id={img_id})...")
            _run_hcloud("DELETE", f"/images/{img_id}", timeout=60)
            print("Deleted.")
            return
    print(f"Snapshot '{name}' not found.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Hetzner snapshot baker for TollGate cloud lab")
    parser.add_argument("--bake", action="store_true", help="Bake a new snapshot")
    parser.add_argument("--list", action="store_true", help="List existing snapshots")
    parser.add_argument("--delete", metavar="NAME", help="Delete a snapshot by name")
    parser.add_argument("--snapshot-name", default=HETZNER_SNAPSHOT_NAME, help="Snapshot name (default from env)")
    parser.add_argument("--location", default=HETZNER_LOCATION, help="Hetzner location (default from env)")
    parser.add_argument("--server-type", default=HETZNER_SERVER_TYPE, help="Server type (default from env)")
    args = parser.parse_args()

    if args.bake:
        _bake(args.snapshot_name, args.location, args.server_type)
    elif args.list:
        _list_snapshots()
    elif args.delete:
        _delete_snapshot(args.delete)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
