#!/usr/bin/env python3
"""Build a clean OpenWrt firmware image with test credentials via the ASU API.

Usage:
    scripts/build-firmware.py --router lab-router-a           # Build and download
    scripts/build-firmware.py --router lab-router-a --flash   # Build, download, flash
    scripts/build-firmware.py --router lab-router-a --key ~/.ssh/id_ed25519.pub
    scripts/build-firmware.py --router lab-router-a --yes     # Non-interactive (CI)

Reads router config (target, profile, version) from config/routers.json.
Generates a random root password, embeds your SSH public key, and opens
WAN SSH for testing. Credentials are logged to credentials/<router-id>.txt.
"""

import base64
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
import tempfile

ASU_URL = "https://sysupgrade.openwrt.org/api/v1/build"
SSH_KEY_CANDIDATES = [
    "~/.ssh/id_ed25519.pub",
    "~/.ssh/id_rsa.pub",
    "~/.ssh/id_ecdsa.pub",
]


def find_ssh_key(explicit_path):
    if explicit_path:
        path = os.path.expanduser(explicit_path)
        if not os.path.isfile(path):
            bail(f"SSH key not found: {path}")
        return path

    env_path = os.environ.get("TOLLGATE_SSH_KEY")
    if env_path:
        path = os.path.expanduser(env_path)
        if not os.path.isfile(path):
            bail(f"TOLLGATE_SSH_KEY not found: {path}")
        return path

    for candidate in SSH_KEY_CANDIDATES:
        path = os.path.expanduser(candidate)
        if os.path.isfile(path):
            return path

    bail(
        "No SSH public key found. Tried:\n"
        + "\n".join(f"  {c}" for c in SSH_KEY_CANDIDATES)
        + "\nUse --key <path> or set TOLLGATE_SSH_KEY\n"
        + "Generate one with: ssh-keygen -t ed25519"
    )


def read_and_strip_key(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            return f"{parts[0]} {parts[1]}"
    bail(f"No valid SSH key found in {path}")


def generate_password():
    env_pw = os.environ.get("TOLLGATE_FIRMWARE_PASSWORD")
    if env_pw:
        return env_pw
    return ''.join(secrets.choice('abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(24))


def build_defaults_script(ssh_key, password):
    return f"""#!/bin/sh
mkdir -p /etc/dropbear
cat > /etc/dropbear/authorized_keys <<'EOFKEY'
{ssh_key}
EOFKEY
chmod 600 /etc/dropbear/authorized_keys
printf '%s\\n%s\\n' '{password}' '{password}' | passwd root
uci add firewall rule
uci set firewall.@rule[-1].name='Allow-SSH-WAN'
uci set firewall.@rule[-1].src='wan'
uci set firewall.@rule[-1].dest_port='22'
uci set firewall.@rule[-1].proto='tcp'
uci set firewall.@rule[-1].target='ACCEPT'
uci commit firewall
exit 0"""


def load_router_config(router_id):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    config_path = os.environ.get(
        "TOLLGATE_ROUTER_INVENTORY",
        os.path.join(repo_root, "config", "routers.json"),
    )

    if not os.path.isfile(config_path):
        bail(f"Router config not found: {config_path}\nCopy config/routers.example.json to config/routers.json and edit it.")

    with open(config_path) as f:
        inventory = json.load(f)

    router = inventory.get("routers", {}).get(router_id)
    if not router:
        available = ", ".join(inventory.get("routers", {}).keys()) or "none"
        bail(f"Router '{router_id}' not found in config. Available: {available}")

    target = router.get("openwrtTarget")
    profile = router.get("openwrtProfile")
    version = router.get("openwrtVersion", "24.10.1")

    if not target or not profile:
        bail(f"Router '{router_id}' is missing openwrtTarget or openwrtProfile in config. See docs/firmware-build-flash.md.")

    return {
        "id": router_id,
        "model": router.get("model", profile),
        "sshHost": router.get("sshHost", ""),
        "sshUser": router.get("sshUser", "root"),
        "openwrtVersion": version,
        "openwrtTarget": target,
        "openwrtProfile": profile,
    }


def submit_build(version, target, profile, defaults):
    body = json.dumps({
        "version": version,
        "target": target,
        "profile": profile,
        "packages": [],
        "diff_packages": False,
        "defaults": defaults,
    })

    req = urllib.request.Request(
        ASU_URL,
        data=body.encode(),
        headers={"Content-Type": "application/json", "User-Agent": "tollgate-build-firmware/1.0"},
    )
    resp = urllib.request.urlopen(req, timeout=120)
    result = json.loads(resp.read().decode())
    request_hash = result.get("request_hash", "")
    return request_hash


def poll_build(request_hash):
    url = f"{ASU_URL}/{request_hash}"
    for _ in range(60):
        time.sleep(10)
        req = urllib.request.Request(url, headers={"User-Agent": "tollgate-build-firmware/1.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode())
        status = result.get("status")

        if status == 200:
            return result
        elif status in (400, 422, 500):
            bail(f"Build FAILED (status={status}):\n{json.dumps(result, indent=2)}")
        else:
            queue = result.get("queue_position", "?")
            print(f"  Waiting... (status={status}, queue={queue})")

    bail("Build timed out after ~10 minutes.")


def download_image(build_result, output_dir):
    bin_dir = build_result.get("bin_dir", "")
    images = build_result.get("images", [])
    if not images:
        bail("No images in build result.")

    image = None
    for img in images:
        if img.get("type") == "sysupgrade":
            image = img
            break
    if not image:
        image = images[0]

    name = image["name"]
    url = f"https://sysupgrade.openwrt.org/store/{bin_dir}/{name}"
    local_path = os.path.join(output_dir, name)

    print(f"Downloading {name}...")
    req = urllib.request.Request(url, headers={"User-Agent": "tollgate-build-firmware/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        with open(local_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)

    size_mb = os.path.getsize(local_path) / (1024 * 1024)
    print(f"Saved: {local_path} ({size_mb:.1f} MB)")
    return local_path


def save_credentials(router_id, password, ssh_key_path, image_path, output_dir):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    creds_dir = os.path.join(repo_root, "credentials")
    os.makedirs(creds_dir, exist_ok=True)

    key_type = ssh_key_path.split("/")[-1].replace(".pub", "")
    creds = {
        "router": router_id,
        "password": password,
        "sshKey": key_type,
        "image": os.path.basename(image_path),
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    creds_path = os.path.join(creds_dir, f"{router_id}.json")
    with open(creds_path, "w") as f:
        json.dump(creds, f, indent=2)
    os.chmod(creds_path, 0o600)

    pw_path = os.path.join(creds_dir, f"{router_id}.txt")
    with open(pw_path, "w") as f:
        f.write(f"{password}\n")
    os.chmod(pw_path, 0o600)

    print(f"Credentials saved to {creds_dir}/")
    return creds_path


def flash_router(image_path, host, user="root"):
    remote_path = f"/tmp/{os.path.basename(image_path)}"
    print(f"Copying to {user}@{host}:{remote_path}...")
    subprocess.run(["scp", "-O", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                     image_path, f"{user}@{host}:{remote_path}"], check=True)
    print(f"Flashing {host}... (connection will drop, router reboots)")
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
         f"{user}@{host}", f"sysupgrade -n '{remote_path}'"],
        check=False,
    )
    print("Router is rebooting into clean OpenWrt.")
    print("After ~2 min, connect to LAN and: ssh root@192.168.1.1")


def confirm(prompt):
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")


def bail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build OpenWrt firmware with test credentials")
    parser.add_argument("--router", required=True, help="Router ID from config/routers.json")
    parser.add_argument("--key", default=None, help="SSH public key file (auto-detected by default)")
    parser.add_argument("--yes", action="store_true", help="Skip confirmations (non-interactive)")
    parser.add_argument("--flash", action="store_true", help="Flash image after downloading")
    parser.add_argument("--output", default=None, help="Output directory for firmware image")
    args = parser.parse_args()

    router = load_router_config(args.router)

    key_path = find_ssh_key(args.key)
    ssh_key = read_and_strip_key(key_path)
    key_type = ssh_key.split()[0]
    key_tail = ssh_key[-8:] if len(ssh_key) > 8 else ssh_key

    password = generate_password()

    print(f"Router:  {router['model']} ({router['id']})")
    print(f"Target:  {router['openwrtVersion']} / {router['openwrtTarget']} / {router['openwrtProfile']}")
    print(f"SSH key: {key_type} ...{key_tail}")
    print(f"Password: {password}")
    print()

    if not args.yes and not confirm("Build firmware with these settings?"):
        print("Aborted.")
        sys.exit(0)

    defaults = build_defaults_script(ssh_key, password)

    print("Submitting build to ASU...")
    request_hash = submit_build(
        router["openwrtVersion"],
        router["openwrtTarget"],
        router["openwrtProfile"],
        defaults,
    )
    print(f"Build queued: {request_hash}")

    build_result = poll_build(request_hash)

    output_dir = args.output or tempfile.gettempdir()
    image_path = download_image(build_result, output_dir)

    save_credentials(args.router, password, key_path, image_path, output_dir)

    if args.flash:
        if not router["sshHost"]:
            bail("Cannot flash: no sshHost configured for this router.")
        if not args.yes and not confirm(f"Flash to {router['sshHost']} now?"):
            print("Image saved. Flash manually with:")
            print(f"  scp {image_path} root@{router['sshHost']}:/tmp/")
            print(f"  ssh root@{router['sshHost']} 'sysupgrade -n /tmp/{os.path.basename(image_path)}'")
            sys.exit(0)
        flash_router(image_path, router["sshHost"], router["sshUser"])
    else:
        print()
        print("Flash manually:")
        print(f"  scp {image_path} root@<router-ip>:/tmp/")
        print(f"  ssh root@<router-ip> 'sysupgrade -n /tmp/{os.path.basename(image_path)}'")


if __name__ == "__main__":
    main()
