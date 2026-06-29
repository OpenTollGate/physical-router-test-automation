"""Log-based session verification — confirm payment success WITHOUT spending tokens.

These helpers inspect router state (backend ``logread`` logs, ``ndsctl``,
and the TollGate API ``/balance`` & ``/usage`` endpoints) to verify that a
client has an active session. They are **read-only**: they never POST a token,
so they can be called repeatedly in assertions and post-payment checks without
burning tokens.

This satisfies the requirement: *"Pytests also check the backend logs or
ndsctl --json programmatically so that you don't need to burn tokens to check
this."*

Typical use (after a Playwright/phone/API test has performed a payment)::

    from lib.session_verify import verify_session

    result = verify_session(router, mac=router.phone_mac, ip=router.phone_ip)
    assert result.any_success, f"no active session: {result.summary()}"

Each individual probe is also available standalone:
  * :func:`check_backend_logs`   — grep ``logread`` for session-creation logs
  * :func:`check_ndsctl`         — parse ``ndsctl json`` / ``ndsctl clients``
  * :func:`check_balance_api`    — GET ``/balance``, look for active session
  * :func:`check_usage_api`      — GET ``/usage``, look for allotment > 0

All probes degrade gracefully: a probe that cannot run (e.g. ``ndsctl json``
unsupported on this nodogsplash build) returns a falsy result with an evidence
string, it never raises.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

log = logging.getLogger("tollgate.session_verify")

# Log signatures emitted by the Go / Rust backend when a client session is
# established after a successful payment.
#
# "PurchaseSession" is the canonical Go log (tollgate-module-basic-go). The
# Rust v1 backend emits similar "session created" / "authorized client" lines.
# Patterns are matched case-insensitively.
_SESSION_LOG_PATTERNS = [
    r"PurchaseSession",
    r"session (?:created|started|active|established)",
    r"authorized client",
    r"client (?:[0-9a-fA-F:]{11,17}|\S+) authenticated",
    r"created session for",
    r"payment (?:accepted|received|processed)",
    r"allotment granted",
]

_SESSION_LOG_RE = re.compile("|".join(_SESSION_LOG_PATTERNS), re.IGNORECASE)


@dataclass
class SessionVerification:
    """Aggregated result of all read-only session-verification probes."""

    backend_logs: bool = False
    backend_log_evidence: str = ""
    ndsctl_authenticated: bool = False
    ndsctl_state: str = ""
    balance_session_active: bool = False
    balance_evidence: str = ""
    usage_allotment: int = 0
    usage_evidence: str = ""

    @property
    def any_success(self) -> bool:
        """True if *any* probe indicates an active session."""
        return (
            self.backend_logs
            or self.ndsctl_authenticated
            or self.balance_session_active
            or self.usage_allotment > 0
        )

    def summary(self) -> str:
        return (
            f"backend_logs={self.backend_logs} "
            f"ndsctl={self.ndsctl_state or 'n/a'} "
            f"balance_active={self.balance_session_active} "
            f"usage_allotment={self.usage_allotment}"
        )


# --------------------------------------------------------------------------- #
# Individual read-only probes
# --------------------------------------------------------------------------- #


def check_backend_logs(router, mac: str | None = None, lines: int = 500) -> tuple[bool, str]:
    """Grep recent backend logs for session-creation signatures.

    Uses ``router.get_tollgate_logs()`` (which runs ``logread -e tollgate`` on
    the router). When ``mac`` is given, matching lines containing the MAC are
    preferred as evidence, but the search is not restricted to MAC-only lines
    (the session-creation log may not embed the MAC on every backend).

    Returns:
        (matched, evidence_snippet) — read-only, never spends a token.
    """
    try:
        logs = router.get_tollgate_logs(lines=lines)
    except Exception as exc:
        log.debug("could not read tollgate logs: %s", exc)
        return False, f"<logread error: {exc}>"

    if not logs:
        return False, "<empty tollgate logs>"

    mac_line: str | None = None
    if mac:
        mac_lc = mac.lower()
        mac_nocolon = mac_lc.replace(":", "")
        for line in logs.splitlines():
            if _SESSION_LOG_RE.search(line):
                blob = line.lower().replace(":", "")
                if mac_lc in line.lower() or mac_nocolon in blob:
                    mac_line = line.strip()
                    break
        if mac_line:
            return True, mac_line[:200]

    # Fall back to any matching line.
    match = _SESSION_LOG_RE.search(logs)
    if match:
        for line in logs.splitlines():
            if _SESSION_LOG_RE.search(line):
                return True, line.strip()[:200]
        return True, logs[:200]
    return False, "<no session log signature found>"


def check_ndsctl(router, mac: str | None = None) -> tuple[bool, str]:
    """Query ``ndsctl`` for the client's authenticated state.

    Tries ``ndsctl json`` first (structured output from recent nodogsplash),
    then falls back to parsing ``ndsctl clients`` text via
    ``router.get_nds_state()``. Returns ``(is_authenticated, state_string)``.

    Read-only — never spends a token.
    """
    mac = mac or router.phone_mac

    raw = ""
    try:
        # Some nodogsplash builds accept "ndsctl json"; others emit JSON to
        # "ndsctl status --json". Try both, tolerate failure.
        raw = router.ssh("ndsctl json 2>/dev/null", timeout=10)
        if not raw or not raw.lstrip().startswith(("{", "[")):
            raw = router.ssh("ndsctl status --json 2>/dev/null", timeout=10)
    except Exception as exc:
        log.debug("ndsctl json query failed: %s", exc)

    if raw and raw.lstrip().startswith(("{", "[")):
        state, evidence = _parse_ndsctl_json(raw, mac)
        if state:
            return state == "Authenticated", evidence

    # Fallback: text clients listing (parses "state=..." lines).
    try:
        state = router.get_nds_state(mac)
    except Exception as exc:
        log.debug("ndsctl clients parse failed: %s", exc)
        return False, f"<ndsctl error: {exc}>"
    return state == "Authenticated", state or "<client not found in ndsctl>"


def _parse_ndsctl_json(raw: str, mac: str | None) -> tuple[str, str]:
    """Parse ``ndsctl json`` output, returning (state, evidence_json)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", "<invalid json>"

    clients = None
    if isinstance(data, dict):
        clients = data.get("clients") or data.get("client_list")
    if clients is None and isinstance(data, list):
        clients = data
    if not clients:
        return "", "<no clients in ndsctl json>"

    if isinstance(clients, dict):
        client_iter = list(clients.values())
    elif isinstance(clients, list):
        client_iter = clients
    else:
        return "", "<unexpected clients shape>"

    for client in client_iter:
        if not isinstance(client, dict):
            continue
        c_mac = str(client.get("mac") or client.get("hw") or client.get("macaddr") or "").lower()
        if mac:
            target = mac.lower()
            target_nc = target.replace(":", "")
            if target not in c_mac and target_nc not in c_mac.replace(":", "") and c_mac not in target:
                continue
        state = str(client.get("state") or client.get("status") or "").strip()
        if state:
            return state, json.dumps(client)[:200]
    return "", "<client mac not found in ndsctl json>"


def check_balance_api(router, ip: str | None = None) -> tuple[bool, str]:
    """GET ``/balance`` and check whether the client has an active session.

    Returns ``(session_active, evidence_snippet)``. Read-only.

    An active session is indicated by any of:
      * ``session_active == true``
      * a non-zero ``remaining`` / ``allotment`` field
      * a NIP-1022 session event (``kind == 1022``)
      * the presence of an ``allotment`` tag/value
    """
    ip = ip or router.phone_ip
    try:
        resp = router.backend_curl_xff(router.backend_url("/balance"), ip)
    except Exception as exc:
        return False, f"<balance error: {exc}>"
    if not resp:
        return False, "<empty balance response>"
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        return False, f"<non-json balance: {resp[:120]}>"

    active = (
        data.get("session_active") is True
        or (isinstance(data.get("remaining"), (int, float)) and data["remaining"] > 0)
        or (isinstance(data.get("allotment"), (int, float)) and data["allotment"] > 0)
        or data.get("kind") == 1022
        or _has_allotment_tag(data)
    )
    return bool(active), json.dumps(data)[:200]


def check_usage_api(router, ip: str | None = None) -> tuple[bool, str]:
    """GET ``/usage`` and check the allotment granted to the client.

    Returns ``(has_allotment, evidence_snippet)``. Read-only.
    """
    ip = ip or router.phone_ip
    try:
        resp = router.backend_curl_xff(router.backend_url("/usage"), ip)
    except Exception as exc:
        return False, f"<usage error: {exc}>"
    if not resp:
        return False, "<empty usage response>"
    try:
        data = json.loads(resp)
    except json.JSONDecodeError:
        return False, f"<non-json usage: {resp[:120]}>"

    allotment = _extract_allotment(data)
    return allotment > 0, json.dumps(data)[:200]


# --------------------------------------------------------------------------- #
# Aggregated / convenience API
# --------------------------------------------------------------------------- #


def snapshot_session(router, mac: str | None = None, ip: str | None = None) -> SessionVerification:
    """Run all four probes once and return the aggregated result."""
    result = SessionVerification()
    try:
        result.backend_logs, result.backend_log_evidence = check_backend_logs(router, mac=mac)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("backend log check error: %s", exc)
    try:
        result.ndsctl_authenticated, result.ndsctl_state = check_ndsctl(router, mac=mac)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("ndsctl check error: %s", exc)
    try:
        result.balance_session_active, result.balance_evidence = check_balance_api(router, ip=ip)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("balance check error: %s", exc)
    try:
        ok, evidence = check_usage_api(router, ip=ip)
        result.usage_allotment = 1 if ok else 0
        result.usage_evidence = evidence
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("usage check error: %s", exc)
    return result


def verify_session(
    router,
    mac: str | None = None,
    ip: str | None = None,
    timeout: int = 30,
    poll_interval: float = 1.0,
) -> SessionVerification:
    """Poll all verification sources until a session is confirmed or timeout.

    Read-only — never spends a token. Polls every ``poll_interval`` seconds for
    up to ``timeout`` seconds, returning as soon as any probe succeeds.

    Returns the final :class:`SessionVerification` (call ``.any_success``).
    """
    deadline = time.monotonic() + timeout
    last = SessionVerification()
    while True:
        last = snapshot_session(router, mac=mac, ip=ip)
        if last.any_success or time.monotonic() >= deadline:
            break
        time.sleep(poll_interval)
    if last.any_success:
        log.info("session verification OK: %s", last.summary())
    else:
        log.warning("session verification found NO active session: %s", last.summary())
    return last


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _extract_allotment(data) -> int:
    """Best-effort extract a positive allotment int from a usage/balance blob."""
    if isinstance(data, dict):
        for key in ("allotment", "remaining", "download_limit", "upload_limit"):
            val = data.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return int(val)
        # NIP-style event with an allotment tag.
        for tag in data.get("tags", []) or []:
            if isinstance(tag, list) and tag and tag[0] == "allotment":
                try:
                    return int(tag[1])
                except (ValueError, IndexError):
                    continue
    return 0


def _has_allotment_tag(data) -> bool:
    if isinstance(data, dict):
        for tag in data.get("tags", []) or []:
            if isinstance(tag, list) and tag and tag[0] == "allotment":
                return True
    return False
