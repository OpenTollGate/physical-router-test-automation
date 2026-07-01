"""Fips-specific build cache helpers.

Extends blossom_cache.py with fips-aware cache functions:
- get_fips_binary(): cache compiled fips/fipsctl/fipstop binaries
- get_openwrt_image(): cache CDN URL via NIP-94 metadata
- get_debian_image(): cache CDN URL via NIP-94 metadata
- ensure_rust(): skip install if already present
- provision_shc_vm(): one-call setup for a SHC VM

Uses the same cached_build() pattern as mints.py.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path

from lib.cloud_lab.worker.blossom_cache import (
    cached_build,
    compute_cache_key,
    _check_blossom_cache,
    _download_blob,
    _upload_and_publish,
)
from lib.cloud_lab.worker.shell import _run, log
from lib.constants import BLOSSOM_SERVERS, NOSTR_RELAYS

FIPS_REPO = "https://github.com/Amperstrand/fips.git"
FIPS_DEFAULT_REF = "ai-experiments"

OPENWRT_VERSION = "24.10.0"
OPENWRT_TARGET = "x86/64"
DEBIAN_RELEASE = "bookworm"


def _resolve_commit(ref: str) -> str:
    r = subprocess.run(
        ["git", "ls-remote", FIPS_REPO, ref],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split()[0]
    return ref


def get_fips_binary(
    ref: str = FIPS_DEFAULT_REF,
    dest_dir: str = "/usr/local/bin",
    nsec_file: str | None = None,
    blossom_server: str | None = None,
    relay: str | None = None,
) -> str:
    """Get fips + fipsctl binaries with Blossom cache.

    Cache key: sha256("fips-binary", repo, commit, arch, profile)
    Hit: download tarball from Blossom (~5 sec)
    Miss: clone, build, upload tarball (~10 min)

    Returns path to the fips binary.
    """
    if blossom_server is None:
        blossom_server = BLOSSOM_SERVERS[0] if BLOSSOM_SERVERS else "https://blossom.psbt.me"
    if relay is None:
        relay = NOSTR_RELAYS[0] if NOSTR_RELAYS else "wss://relay.cashu.email"

    commit = _resolve_commit(ref)
    arch = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    key = compute_cache_key("fips-binary", FIPS_REPO, commit, arch, "release")
    dest = os.path.join(dest_dir, "fips")

    def build():
        log.info("Building fips from %s@%s...", ref, commit[:12])
        ensure_rust()
        _run(f"git clone --depth 1 --branch {shlex.quote(ref)} {FIPS_REPO} /tmp/fips-build", timeout=60)
        _run("cd /tmp/fips-build && cargo build --release", timeout=600)
        result = "/tmp/fips-build/target/release"
        _run(f"mkdir -p {dest_dir}")
        for binary in ("fips", "fipsctl", "fipstop"):
            src = f"{result}/{binary}"
            if os.path.exists(src):
                _run(f"cp {src} {dest_dir}/{binary}")
                _run(f"chmod +x {dest_dir}/{binary}")
        _run(f"tar czf /tmp/fips-binaries.tar.gz -C {result} fips fipsctl fipstop 2>/dev/null || true")
        return "/tmp/fips-binaries.tar.gz"

    def extract_after_download():
        if os.path.exists(dest) and os.access(dest, os.X_OK):
            return dest
        tarball = "/tmp/fips-binaries.tar.gz"
        if os.path.exists(tarball):
            _run(f"mkdir -p {dest_dir}")
            _run(f"tar xzf {tarball} -C {dest_dir}/")
            _run(f"chmod +x {dest_dir}/fips {dest_dir}/fipsctl {dest_dir}/fipstop 2>/dev/null || true")
        return dest

    cached_path = cached_build(key, build, "/tmp/fips-binaries.tar.gz", nsec_file, blossom_server, relay)
    extract_after_download()
    log.info("fips binary ready at %s", dest)
    return dest


def get_openwrt_image(
    version: str = OPENWRT_VERSION,
    dest: str = "/opt/images/openwrt.img",
    nsec_file: str | None = None,
    relay: str | None = None,
) -> str:
    """Get OpenWrt combined image. Caches CDN URL via NIP-94.

    Hit: download from cached CDN URL (~30 sec for 100 MB)
    Miss: discover URL, download, cache the URL
    """
    if relay is None:
        relay = NOSTR_RELAYS[0] if NOSTR_RELAYS else "wss://relay.cashu.email"

    key = compute_cache_key("openwrt-url", version, OPENWRT_TARGET)

    cached_url = None
    if nsec_file and Path(nsec_file).exists():
        cached_url = _check_blossom_cache(key, nsec_file, relay)

    if not cached_url:
        cached_url = (
            f"https://downloads.openwrt.org/releases/{version}/targets/{OPENWRT_TARGET}/"
            f"openwrt-{version}-x86-64-generic-ext4-combined.img.gz"
        )

    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading OpenWrt %s from %s", version, cached_url[:60])
    _run(f"wget -q -O {dest}.gz {shlex.quote(cached_url)}", timeout=120)
    _run(f"gunzip -f {dest}.gz", timeout=30)
    _run(f"qemu-img resize {dest} 1G 2>/dev/null || true")

    if nsec_file and Path(nsec_file).exists():
        _publish_url_metadata(key, cached_url, "openwrt-combined.img", nsec_file, relay)

    return dest


def get_debian_image(
    release: str = DEBIAN_RELEASE,
    dest: str = "/opt/images/debian.qcow2",
    nsec_file: str | None = None,
    relay: str | None = None,
) -> str:
    """Get Debian cloud image. Caches CDN URL via NIP-94."""
    if relay is None:
        relay = NOSTR_RELAYS[0] if NOSTR_RELAYS else "wss://relay.cashu.email"

    key = compute_cache_key("debian-url", release, "amd64")

    cached_url = None
    if nsec_file and Path(nsec_file).exists():
        cached_url = _check_blossom_cache(key, nsec_file, relay)

    if not cached_url:
        cached_url = f"https://cloud.debian.org/images/cloud/{release}/latest/debian-12-nocloud-amd64.qcow2"

    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading Debian %s from %s", release, cached_url[:60])
    _run(f"wget -q -O {dest} {shlex.quote(cached_url)}", timeout=120)

    if nsec_file and Path(nsec_file).exists():
        _publish_url_metadata(key, cached_url, "debian.qcow2", nsec_file, relay)

    return dest


def ensure_rust(toolchain: str = "stable") -> bool:
    """Install Rust if not present. Returns True if already installed."""
    r = subprocess.run(["bash", "-c", "source ~/.cargo/env 2>/dev/null && rustc --version"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0 and r.stdout.strip():
        log.info("Rust already installed: %s", r.stdout.strip())
        return True
    log.info("Installing Rust %s...", toolchain)
    _run(f"curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain {toolchain}",
         timeout=120)
    return False


def ensure_system_deps() -> None:
    """Install QEMU + build deps. Skip already-installed packages."""
    pkgs = [
        "qemu-system-x86", "qemu-utils", "genisoimage",
        "bridge-utils", "iproute2", "iptables",
        "build-essential", "pkg-config", "libssl-dev",
        "libdbus-1-dev", "libclang-dev",
        "curl", "wget", "git", "jq", "tcpdump",
    ]
    r = subprocess.run(["dpkg", "-l"] + [f for p in pkgs], capture_output=True, text=True)
    missing = [p for p in pkgs if p not in r.stdout]
    if missing:
        log.info("Installing %d packages...", len(missing))
        _run(f"sudo apt-get update -qq && sudo apt-get install -y -qq {' '.join(missing)}", timeout=180)
    else:
        log.info("All system deps already installed")


def ensure_nak_cli() -> None:
    """Install nak CLI if not present."""
    r = subprocess.run(["which", "nak"], capture_output=True, text=True)
    if r.returncode == 0:
        return
    log.info("Installing nak CLI...")
    _run("curl -sL https://github.com/fiatjaf/nak/releases/download/v0.16.2/nak-v0.16.2-linux-amd64 -o /tmp/nak && "
          "sudo chmod +x /tmp/nak && sudo mv /tmp/nak /usr/local/bin/nak", timeout=30)


def provision_shc_vm(
    nsec_file: str | None = None,
    fips_ref: str = FIPS_DEFAULT_REF,
    blossom_server: str | None = None,
) -> dict:
    """Full provisioning of a SHC VM for fips testing.

    Uses Blossom cache for all expensive steps.
    Returns dict with paths to all artifacts.
    """
    log.info("=== Provisioning SHC VM for fips testing ===")

    ensure_system_deps()
    ensure_rust()
    ensure_nak_cli()

    fips_path = get_fips_binary(
        ref=fips_ref,
        nsec_file=nsec_file,
        blossom_server=blossom_server,
    )
    openwrt_path = get_openwrt_image(nsec_file=nsec_file)
    debian_path = get_debian_image(nsec_file=nsec_file)

    log.info("=== Provisioning complete ===")
    return {
        "fips": fips_path,
        "openwrt": openwrt_path,
        "debian": debian_path,
        "rust": "installed",
    }


def _publish_url_metadata(
    cache_key: str,
    url: str,
    filename: str,
    nsec_file: str,
    relay: str,
) -> None:
    """Publish a CDN URL as NIP-94 metadata (for URL caching, not file caching)."""
    env = os.environ.copy()
    with open(nsec_file) as f:
        env["NOSTR_SECRET_KEY"] = f.read().strip()

    cmd = (
        f"nak event -k 1063 -c '{shlex.quote(url)}' "
        f"-t d={shlex.quote(cache_key)} "
        f"-t url={shlex.quote(url)} "
        f"-t m=application/octet-stream "
        f"-t filename={shlex.quote(filename)} "
        f"{shlex.quote(relay)} 2>/dev/null"
    )
    subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=15, env=env)
