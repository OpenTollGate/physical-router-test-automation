"""Content-addressable build cache using Blossom + Nostr NIP-94.

Checks Blossom for cached build artifacts before compiling. Cache keys
are derived from all build inputs (git commit + target + toolchain).
Hits download directly from Blossom; misses build and upload.

Uses nak CLI for Nostr queries and blossom_publisher.py for uploads.
Does NOT require BlossomFS — works with HTTP + nak only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Callable

from lib.cloud_lab.worker.shell import _redact, log
from lib.constants import BLOSSOM_SERVERS, NOSTR_RELAYS


def compute_cache_key(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode()).hexdigest()


def _check_blossom_cache(cache_key: str, nsec_file: str, relay: str = None) -> str | None:
    if relay is None:
        relay = NOSTR_RELAYS[0] if NOSTR_RELAYS else "wss://relay.cashu.email"
    r = subprocess.run(
        ["bash", "-c", f"nak req -k 1063 -t d={shlex.quote(cache_key)} -l 1 {shlex.quote(relay)}"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    for line in reversed(r.stdout.strip().split("\n")):
        line = line.strip()
        if line.startswith("{"):
            try:
                event = json.loads(line)
                for tag in event.get("tags", []):
                    if tag[0] == "url" and len(tag) > 1:
                        return tag[1]
            except json.JSONDecodeError:
                continue
    return None


def _download_blob(url: str, dest: str) -> bool:
    r = subprocess.run(
        ["bash", "-c", f"curl -sSf -o {shlex.quote(dest)} {shlex.quote(url)}"],
        capture_output=True, text=True, timeout=120,
    )
    return r.returncode == 0


def _upload_and_publish(
    artifact_path: str,
    cache_key: str,
    nsec_file: str,
    blossom_server: str,
    relay: str,
) -> str | None:
    env = os.environ.copy()
    with open(nsec_file) as f:
        env["NOSTR_SECRET_KEY"] = f.read().strip()

    upload_cmd = (
        f"python3 -c \""
        f"from lib.blossom_publisher import compute_sha256, upload_to_blossom;"
        f"from lib.nostr_publisher import publish_nip94_event;"
        f"import json;"
        f"sha=compute_sha256({shlex.quote(artifact_path)});"
        f"r=upload_to_blossom({shlex.quote(artifact_path)}, {shlex.quote(nsec_file)}, {shlex.quote(blossom_server)});"
        f"url=r.get('url','');"
        f"publish_nip94_event({shlex.quote(nsec_file)}, {shlex.quote(Path(artifact_path).name)}, url, sha, 'application/octet-stream', "
        f"relays=[{shlex.quote(relay)}], extra_tags=[['d', {shlex.quote(cache_key)}]]);"
        f"print(url)"
        f"\""
    )
    r = subprocess.run(["bash", "-c", upload_cmd], capture_output=True, text=True, timeout=120, env=env)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split("\n")[-1].strip()
    log.warning("Cache upload failed: %s", _redact(r.stderr[:200]))
    return None


def cached_build(
    cache_key: str,
    build_fn: Callable[[], str],
    dest: str,
    nsec_file: str | None = None,
    blossom_server: str = None,
    relay: str = None,
) -> str:
    """Build an artifact with Blossom content-addressable cache.

    Args:
        cache_key: SHA256 hash of all build inputs (use compute_cache_key()).
        build_fn: Callable that builds the artifact and returns its path.
        dest: Where to place the final artifact.
        nsec_file: Path to nsec for Blossom auth + Nostr publishing.
                   If None, cache is disabled (always builds).
        blossom_server: Blossom server URL.
        relay: Nostr relay for cache queries.

    Returns:
        Path to the artifact at dest.
    """
    if blossom_server is None:
        blossom_server = BLOSSOM_SERVERS[0] if BLOSSOM_SERVERS else "https://blossom.psbt.me"
    if relay is None:
        relay = NOSTR_RELAYS[0] if NOSTR_RELAYS else "wss://relay.cashu.email"
    if not nsec_file or not Path(nsec_file).exists():
        log.info("Cache disabled (no nsec) — building %s", Path(dest).name)
        result = build_fn()
        _copy_artifact(result, dest)
        return dest

    log.info("Checking cache for %s (key=%s...)", Path(dest).name, cache_key[:12])
    cached_url = _check_blossom_cache(cache_key, nsec_file, relay)
    if cached_url:
        log.info("Cache HIT — downloading from %s", cached_url[:60])
        if _download_blob(cached_url, dest):
            log.info("Cache hit successful")
            return dest
        log.warning("Cache hit download failed — falling back to build")

    log.info("Cache MISS — building %s", Path(dest).name)
    result = build_fn()
    _copy_artifact(result, dest)

    log.info("Uploading to cache (key=%s...)", cache_key[:12])
    _upload_and_publish(dest, cache_key, nsec_file, blossom_server, relay)

    return dest


def _copy_artifact(src: str, dest: str) -> None:
    if os.path.abspath(src) == os.path.abspath(dest):
        return
    subprocess.run(["cp", src, dest], check=True, timeout=30)
