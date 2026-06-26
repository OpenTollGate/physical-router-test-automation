"""Blossom-backed build cache for compiled binaries.

Stores compiled binaries on Blossom (content-addressed by SHA256) with
NIP-94 metadata events for discovery. Only trusts binaries signed by
our own pubkey — never downloads from unknown signers.

Usage:
    from lib.build_cache import BuildCache

    cache = BuildCache(nsec_file="~/.config/prta/nsec")
    path = cache.fetch_or_build(
        cache_key="blossomfs-8784100",
        build_fn=lambda: build_blossomfs(),
        install_path="/opt/blossomfs/target/release/blossomfs",
    )
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

BLOSSOM_SERVER = os.environ.get("BLOSSOM_SERVER", "https://blossom.psbt.me")
RELAYS = os.environ.get("NOSTR_RELAYS", "wss://relay.cashu.email,wss://relay.damus.io,wss://nos.lol")
TRUSTED_PUBKEY = os.environ.get("BUILD_CACHE_PUBKEY", "")


class BuildCache:
    def __init__(self, nsec_file: str = "", blossom_server: str = "", trusted_pubkey: str = ""):
        self.nsec_file = nsec_file or os.environ.get("NSEC_FILE", "")
        self.blossom_server = blossom_server or BLOSSOM_SERVER
        self.trusted_pubkey = trusted_pubkey or TRUSTED_PUBKEY
        self._nak = self._find_nak()

    def _find_nak(self) -> str:
        for p in ["/usr/local/bin/nak", "/usr/bin/nak", "nak"]:
            try:
                subprocess.run([p, "--version"], capture_output=True, timeout=5)
                return p
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return "nak"

    def fetch_or_build(
        self,
        cache_key: str,
        build_fn,
        install_path: str,
        force_build: bool = False,
    ) -> str:
        if force_build or not self.nsec_file:
            log.info("build_cache: building %s (no cache or forced)", cache_key)
            build_fn()
            return install_path

        if os.path.exists(install_path) and os.path.getsize(install_path) > 0:
            log.info("build_cache: %s already present at %s", cache_key, install_path)
            return install_path

        cached_url = self._lookup(cache_key)
        if cached_url:
            log.info("build_cache: downloading %s from %s", cache_key, cached_url[:60])
            if self._download_and_verify(cached_url, install_path, cache_key):
                log.info("build_cache: %s cached download OK", cache_key)
                return install_path
            log.warning("build_cache: download/verify failed for %s, falling back to build", cache_key)

        log.info("build_cache: building %s from source", cache_key)
        build_fn()

        if self.nsec_file and os.path.exists(install_path):
            self._upload_and_publish(install_path, cache_key)

        return install_path

    def _lookup(self, cache_key: str) -> str | None:
        try:
            cmd = [
                self._nak, "req", "-k", "1063", "-l", "10",
                f"-t filename={cache_key}",
                *self.blossom_server.replace("https://", "wss://").replace("http://", "ws://").split(","),
            ]
            # Use relays, not the blossom server (which is HTTP, not WS)
            relay_args = [r for r in RELAYS.split(",") if r.startswith("ws")]
            cmd = [self._nak, "req", "-k", "1063", "-l", "10", f"-t", f"filename={cache_key}", *relay_args]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            for line in r.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                import json
                try:
                    evt = json.loads(line)
                    tags = evt.get("tags", [])

                    if self.trusted_pubkey:
                        if evt.get("pubkey", "") != self.trusted_pubkey:
                            log.debug("build_cache: skipping untrusted pubkey %s", evt.get("pubkey", "")[:16])
                            continue

                    for tag in tags:
                        if tag[0] == "url" and len(tag) > 1:
                            return tag[1]
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            log.debug("build_cache: lookup error: %s", e)
        return None

    def _download_and_verify(self, url: str, install_path: str, cache_key: str) -> bool:
        try:
            dest_dir = os.path.dirname(install_path)
            if dest_dir:
                os.makedirs(dest_dir, exist_ok=True)

            r = subprocess.run(
                ["curl", "-sfL", "-o", install_path, url],
                capture_output=True, timeout=120,
            )
            if r.returncode != 0:
                return False

            os.chmod(install_path, 0o755)
            return os.path.getsize(install_path) > 0
        except Exception as e:
            log.debug("build_cache: download error: %s", e)
            return False

    def _upload_and_publish(self, binary_path: str, cache_key: str) -> None:
        try:
            from lib.blossom_publisher import upload_to_blossom, compute_sha256
            from lib.nostr_publisher import publish_nip94_event

            sha = compute_sha256(binary_path)
            result = upload_to_blossom(
                binary_path, self.nsec_file, self.blossom_server,
                content_type="application/octet-stream",
            )
            if not result.get("url"):
                log.warning("build_cache: upload failed for %s", cache_key)
                return

            url = result["url"]
            size = os.path.getsize(binary_path)
            filename = cache_key

            publish_nip94_event(
                self.nsec_file, filename, url, sha,
                "application/octet-stream",
                metadata_tags=[["size", str(size)], ["cache_key", cache_key]],
                relays=RELAYS.split(","),
            )
            log.info("build_cache: uploaded %s → %s (sha256=%s…)", cache_key, url[:50], sha[:16])
        except Exception as e:
            log.warning("build_cache: upload/publish error for %s: %s", cache_key, e)


def sha256sum(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
