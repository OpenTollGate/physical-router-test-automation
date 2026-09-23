"""Cloud lab worker — shell utilities."""

from __future__ import annotations

import logging
import subprocess
import time

log = logging.getLogger("tollgate.cloud_worker")
_REDACT_PATTERNS = [
    # GitHub tokens
    r"(gho_|ghp_|github_pat_)[A-Za-z0-9_]+",
    r"(GH_TOKEN=|gh-token=)[^\s,]+",
    # Passwords
    r"(password|passwd|sshpass\s+-p)\s+[^\s,]+",
    # SSH private keys (match BEGIN line and onward)
    r"(-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE KEY-----).*",
    # GCP service account keys (PEM blocks)
    r"(-----BEGIN\s+\w+\s+(?:PRIVATE\s+)?KEY-----).*",
    # GCP OAuth2 access tokens
    r"(ya29\.)[A-Za-z0-9_-]+",
    # Bearer tokens in Authorization headers
    r"(Bearer\s+)[A-Za-z0-9._-]+",
    # Generic API keys (api.key=..., apikey=..., api_key=...)
    r"(api[\._]?key\s*[:=]\s*)[A-Za-z0-9_-]{20,}",
    # Generic base64-encoded secrets labelled as token/secret/key/credential/password
    r"((?:token|secret|key|credential|password|passwd)\s*[:=]\s*)[A-Za-z0-9+/=]{40,}",
    # Passwords in config/env format (password=value, shorter than 40 chars)
    r"((?:password|passwd)\s*[:=]\s*)[^\s,]{4,}",
]
def _redact(text: str) -> str:
    import re as _re

    for pat in _REDACT_PATTERNS:
        text = _re.sub(pat, r"\1***", text)
    return text
def _run(cmd: str, timeout: int = 120, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    redacted = _redact(cmd[:300])
    log.debug("run: %s", redacted)
    t0 = time.monotonic()
    r = subprocess.run(
        ["bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    elapsed = time.monotonic() - t0
    if r.returncode != 0:
        err = _redact((r.stderr or r.stdout or "").strip()[-500:])
        log.info("cmd failed (%.1fs, rc=%d): %s | stderr: %s", elapsed, r.returncode, redacted[:120], err[:300])
        if check:
            raise RuntimeError(f"Command failed ({r.returncode}): {cmd[:120]}\n{err}")
    else:
        log.debug("cmd ok (%.1fs): %s", elapsed, redacted[:120])
    return r
