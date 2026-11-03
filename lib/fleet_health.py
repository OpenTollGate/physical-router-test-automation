"""Router health telemetry — the producer slice 1 was missing.

Slice 1 decides *what a piece of evidence supports*; slice 4 adapters produce
coordinator/host/guest evidence. Nothing produced ``RouterHealthObservation``,
so ``router_capacity`` could only ever answer ``EXPORTED_AVAILABLE`` with
capacity 0 — "exported is not idle" was true and permanent.

This module is the missing producer. It parses output that was *already
collected* from one authorized place, so every rule below is testable without a
router, a console or a network:

* a read that did not happen is ``telemetry_read_ok=False`` — never a pass;
* health is the router itself answering (uptime) **and** the board matching the
  identity its faction declared; a rich-looking client list proves nothing;
* ``session_active`` is a *paid* session, taken from the captive portal's own
  client states — "there are clients" is not "somebody is paying";
* ``vantage_ok`` is about the TESTER: it is True only when the observer's own
  MAC is visible to the portal in a pre-authenticated (stranger) state. An
  authenticated or trusted tester is bypassed by design, so every reading taken
  from it is meaningless as customer evidence (measured 2026-10-21). Unknown
  observer MAC yields ``None`` → UNKNOWN, never True.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from lib.fleet_capacity import RouterHealthObservation, router_observation

__all__ = [
    "CLIENT_STATE_AUTHENTICATED",
    "CLIENT_STATE_TRUSTED",
    "CLIENT_STATE_PREAUTHENTICATED",
    "ClientEntry",
    "ClientList",
    "HEALTH_COMMANDS",
    "parse_ndsctl_clients",
    "parse_uptime_seconds",
    "read_router_health",
    "RouterCommand",
    "RouterProbe",
    "router_health_from_probe",
]

#: Exactly the commands this producer issues, in order. A fixed tuple (not a
#: free-form string from a manifest) so an operator cannot turn a read-only
#: health probe into an arbitrary remote command.
HEALTH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("system", "uptime"),
    ("board", "cat /tmp/sysinfo/model"),
    ("portal", "ndsctl status"),
    ("clients", "ndsctl clients"),
)

#: Commands must be issued ONE PER EXEC. Measured 2026-11-03 on a GL-MT3000
#: over FTDI BG046H5F, alternating on one console, fresh process per trial:
#:
#:     'ndsctl status'                   -> ok
#:     'ndsctl status; echo'             -> ok
#:     'ndsctl status; ndsctl clients'   -> EMPTY
#:     'uptime'                          -> ok
#:     'cat /tmp/sysinfo/model'          -> ok
#:     'uptime; cat /tmp/sysinfo/model'  -> EMPTY
#:     'true; uptime'                    -> ok
#:
#: Two commands that each produce output silence the capture entirely; a
#: no-output prefix (``true``, ``echo``) does not. Any redirect (``2>&1``,
#: ``2>/dev/null``) also silences it — 8/8 empty vs 8/8 correct for the same
#: command without one. An empty capture is indistinguishable from a genuinely
#: quiet router, so both spellings are refused at build time rather than sent
#: and silently misread.

#: Redirect spellings measured to BREAK the wrapper's framing.
#:
#: ``router-serial.py exec`` returns an EMPTY capture whenever the command
#: carries any stderr redirect, deterministically. Measured 2026-11-03 on a
#: GL-MT3000 over FTDI BG046H5F, alternating on one console, 8 trials each:
#:
#:     cat /tmp/sysinfo/model          ok=8/8
#:     cat /tmp/sysinfo/model 2>&1     ok=0/8
#:     cat /tmp/sysinfo/model 2>/dev/null  ok=0/5 (earlier run)
#:
#: An empty capture is indistinguishable from a genuinely quiet router, so a
#: command carrying a redirect is refused at build time rather than sent and
#: silently misread. If the router must be checked for a missing path, do that
#: with a separate command, never a redirect on the read that matters.
_FORBIDDEN_REDIRECTS: tuple[str, ...] = (
    "2>/dev/null",
    "2> /dev/null",
    "&>/dev/null",
    "2>&1",
)

#: nodogsplash client states that mean "this MAC is receiving service".
CLIENT_STATE_AUTHENTICATED = "Authenticated"
CLIENT_STATE_TRUSTED = "Trusted"
CLIENT_STATE_PREAUTHENTICATED = "Preauthenticated"

#: States where the portal allows the client through without payment. A tester
#: in one of these is bypassed, so its vantage is not a customer vantage.
_BYPASS_STATES = frozenset({CLIENT_STATE_AUTHENTICATED, CLIENT_STATE_TRUSTED})

_CLIENT_HEADER_RE = re.compile(r"^Client\s+(\d+)\s*$")
_CLIENT_MAC_RE = re.compile(r"^(?:MAC|mac)\s*[:=]\s*(\S+)\s*$")
_CLIENT_IP_RE = re.compile(r"^(?:IP|ip)\s*[:=]\s*(\S+)\s*$")
_CLIENT_STATE_RE = re.compile(r"^(?:State|state)\s*[:=]\s*(\S+)\s*$")

_UPTIME_RE = re.compile(
    r"up\s+(?:(\d+)\s+days?\s*,?\s*)?"
    r"(?:(\d+):(\d+)|\s*(\d+)\s+min)",
)


@dataclass(frozen=True)
class ClientEntry:
    """One client as the captive portal reports it."""

    mac: str
    ip: str = ""
    state: str = ""

    @property
    def is_bypassing(self) -> bool:
        """True when this client is being served without being a stranger."""
        return self.state in _BYPASS_STATES


@dataclass(frozen=True)
class ClientList:
    """The portal's client list, or the reason it could not be read."""

    clients: tuple[ClientEntry, ...] = ()
    read_ok: bool = False
    detail: str = ""

    def find(self, mac: str) -> ClientEntry | None:
        wanted = _normalise_mac(mac)
        if not wanted:
            return None
        for entry in self.clients:
            if _normalise_mac(entry.mac) == wanted:
                return entry
        return None

    @property
    def paid_sessions(self) -> tuple[ClientEntry, ...]:
        """Authenticated clients — the portal's own definition of a paying one."""
        return tuple(c for c in self.clients if c.state == CLIENT_STATE_AUTHENTICATED)


def _normalise_mac(mac: str) -> str:
    return re.sub(r"[^0-9A-F]", "", (mac or "").upper())


def parse_ndsctl_clients(text: str, *, portal_banner: str = "") -> ClientList:
    """Parse ``ndsctl clients`` output.

    Blocks are counted headers (``Client 0``); the fields inside are matched by
    name, never by position, so an added field or a reordered block does not
    silently shift a MAC into the state slot.

    An empty read (no ``Client N`` header) is ``read_ok=False``: a portal that
    answered with nothing may also be a portal that is not running, and those
    two must not be conflated into "no clients, so idle".
    """
    clients: list[ClientEntry] = []
    current: dict[str, str] = {}
    saw_header = False

    def flush() -> None:
        if not current:
            return
        mac = current.get("mac", "")
        if mac:
            clients.append(
                ClientEntry(mac=mac, ip=current.get("ip", ""), state=current.get("state", ""))
            )
        current.clear()

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _CLIENT_HEADER_RE.match(line):
            flush()
            saw_header = True
            continue
        for key, pattern in (("mac", _CLIENT_MAC_RE), ("ip", _CLIENT_IP_RE), ("state", _CLIENT_STATE_RE)):
            match = pattern.match(line)
            if match:
                current[key] = match.group(1)
                break
    flush()

    if not saw_header:
        # Measured 2026-11-03: with no clients, ``ndsctl clients`` prints nothing
        # at all. The probe therefore also collects ``ndsctl status`` separately
        # (one command per exec — see HEALTH_COMMANDS), and its banner is
        # positive proof the portal is running. Then an absent client list means
        # "zero clients", not "unreadable".
        #
        # Without that banner the read stays ambiguous (stopped portal vs silent
        # success) and must not be read as "no paying customers".
        lowered = (text or "").lower() + " " + (portal_banner or "").lower()
        played = "nodogsplash status" in lowered or "version:" in lowered
        if played:
            return ClientList(
                clients=(),
                read_ok=True,
                detail="portal running, 0 client(s)",
            )
        if "not running" in lowered or "command not found" in lowered or "no such file" in lowered:
            return ClientList(read_ok=False, detail="captive portal is not running")
        return ClientList(read_ok=False, detail="no client blocks in ndsctl output")
    return ClientList(clients=tuple(clients), read_ok=True, detail=f"{len(clients)} client(s)")


def _clean_lines(text: str) -> list[str]:
    """Drop the serial wrapper's own echo/sentinel lines from a capture.

    ``router-serial.py`` echoes the command back and terminates with a sentinel
    (``__SERIAL_END___:$?``) so a caller can find the end of output. Those lines
    are transport noise, not router output: measured 2026-11-03, a board read
    whose real answer was one line came back as ``__SERIAL_END___:$?"`` — and a
    parser that takes "the first line" then reports an identity mismatch against
    a string that is not an identity at all.

    A line is noise when it carries the sentinel or is the echoed command
    (``# <cmd>`` / ``$ <cmd>``). Everything else is kept verbatim, in order.
    """
    kept: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "__SERIAL_END" in line or line.endswith(': $?"') or line.endswith(':$?"'):
            continue
        if line in {'$?', '$?"', '0'} or line.strip('\'"') == '$?':
            # The wrapper echoes the status expression itself before the sentinel
            # line; measured as a bare '$?"' line preceding real output.
            continue
        if line.startswith(("# ", "$ ")):
            continue
        kept.append(line)
    return kept


def parse_uptime_seconds(text: str) -> float | None:
    """Seconds of router uptime from ``uptime``, or None when unparseable.

    Deliberately narrow: the presence of a *parseable* uptime is the proof the
    box answered a shell command. Anything else is not health evidence.
    """
    match = _UPTIME_RE.search(text or "")
    if match is None:
        return None
    days, hh, mm, mins = match.groups()
    total = 0.0
    if days:
        total += int(days) * 86400
    if hh is not None and mm is not None:
        total += int(hh) * 3600 + int(mm) * 60
    elif mins is not None:
        total += int(mins) * 60
    return total


@dataclass(frozen=True)
class RouterProbe:
    """Raw output collected from ONE authorized place. No interpretation yet.

    ``system_text`` is the output of the liveness command (``uptime``),
    ``board_text`` the output of the identity command, ``clients_text`` the
    portal client list. Empty/None means the read did not happen.
    """

    fleet_id: str
    resource_id: str
    system_text: str = ""
    board_text: str = ""
    portal_text: str = ""
    clients_text: str = ""
    expected_identity: str = ""
    observer_mac: str = ""
    protected: bool = False
    read_errors: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RouterCommand:
    """A read-only command issued to one authorized place, and its outcome.

    ``stdout``/``stderr``/``returncode``/``timed_out`` are what the caller
    observed. A timed-out or non-zero read is reported as an error, never as
    empty output — ``""`` from a dead console would otherwise look like a
    router with no clients.
    """

    name: str
    command: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0


def read_router_health(
    fleet_id: str,
    resource_id: str,
    *,
    run: Callable[[RouterCommand], RouterCommand],
    expected_identity: str = "",
    observer_mac: str = "",
    protected: bool = False,
) -> RouterProbe:
    """Issue the fixed read-only health commands and collect the raw output.

    ``run`` is injected: the transport (serial console, SSH, a labgrid
    NetworkSerialDriver) is the caller's choice and is never hard-coded here, so
    this is testable without a router. The command *set* is fixed
    (``HEALTH_COMMANDS``) — this adapter cannot be steered into issuing
    something else by a manifest value.
    """
    texts: dict[str, str] = {}
    errors: dict[str, str] = {}
    for name, command in HEALTH_COMMANDS:
        lowered = command.replace(" ", "").lower()
        if any(bad.replace(" ", "").lower() in lowered for bad in _FORBIDDEN_REDIRECTS):
            # Refused before any subprocess exists: a silently-empty capture
            # would look like a router with no output.
            errors[name] = "command carries a redirect measured to break the serial wrapper"
            continue
        result = run(RouterCommand(name=name, command=command))
        if not result.ok:
            why = "timed out" if result.timed_out else f"rc={result.returncode}"
            detail = (result.stderr or result.stdout or "").strip()[:160]
            errors[name] = f"{why}: {detail}" if detail else why
            continue
        texts[name] = result.stdout or ""
    return RouterProbe(
        fleet_id=fleet_id,
        resource_id=resource_id,
        system_text=texts.get("system", ""),
        board_text=texts.get("board", ""),
        portal_text=texts.get("portal", ""),
        clients_text=texts.get("clients", ""),
        expected_identity=expected_identity,
        observer_mac=observer_mac,
        protected=protected,
        read_errors=errors,
    )


def router_health_from_probe(
    probe: RouterProbe, *, now: float, ttl_s: float, authority_epoch: int
) -> RouterHealthObservation:
    """Turn raw place output into router health evidence.

    Every dimension is decided from the *text the router printed*. Nothing here
    defaults to a happy value: ``telemetry_read_ok`` requires that every read
    that was attempted actually returned, and each dimension that cannot be
    established is left ``None`` (UNKNOWN), which slice 1 refuses to count.
    """
    attempted = {
        "system": probe.system_text,
        "board": probe.board_text,
        "portal": probe.portal_text,
        "clients": probe.clients_text,
    }
    failed_reads = {
        name: reason
        for name, reason in probe.read_errors.items()
        if name in attempted
    }
    # A dimension counts as read only when the caller did not report an error
    # for it AND it produced something. Empty-without-an-error is still not a
    # read of anything.
    telemetry_read_ok = not failed_reads

    uptime_s = parse_uptime_seconds(probe.system_text)
    board = _clean_lines(probe.board_text)
    identity = board[0] if board else ""

    identity_ok: bool | None
    if not identity or not probe.expected_identity:
        identity_ok = None
    else:
        identity_ok = identity == probe.expected_identity

    clients = parse_ndsctl_clients(probe.clients_text, portal_banner=probe.portal_text)

    # Health = the box answered AND it is the box we think it is. A box that
    # answered with an unparseable uptime did not prove liveness.
    health_ok: bool | None
    if not telemetry_read_ok and "system" in failed_reads:
        health_ok = None
    elif uptime_s is None:
        health_ok = False
    elif identity_ok is False:
        health_ok = False
    elif identity_ok is None:
        health_ok = None
    else:
        health_ok = True

    # A paying customer is a portal fact, so it needs a readable client list.
    session_active: bool | None
    if not clients.read_ok:
        session_active = None
    else:
        session_active = bool(clients.paid_sessions)

    # Vantage is about the TESTER, not the DUT. An authenticated or trusted
    # tester is bypassed by design, so readings taken from it are not customer
    # evidence (measured 2026-10-21 — the incident that retracted a claim).
    vantage_ok: bool | None
    observer = clients.find(probe.observer_mac) if (clients.read_ok and probe.observer_mac) else None
    if observer is None or observer.state in _BYPASS_STATES:
        vantage_ok = False if observer is not None else None
    else:
        vantage_ok = True

    return router_observation(
        fleet_id=probe.fleet_id,
        resource_id=probe.resource_id,
        now=now,
        ttl_s=ttl_s,
        authority_epoch=authority_epoch,
        identity=identity,
        expected_identity=probe.expected_identity,
        health_ok=health_ok,
        session_active=session_active,
        vantage_ok=vantage_ok,
        telemetry_read_ok=telemetry_read_ok,
        protected=probe.protected,
    )
