"""Fail-closed protected-device guard.

The MT6000 gateway and the fleet control hosts must never be used as test
targets, even if a private inventory is stale or a test passes the wrong host.
This module is intentionally independent of the labgrid inventory: it matches
committed, non-secret hardware identities (board name, br-lan MAC, hostname,
management address).

Usage:
    from lib.protected_devices import assert_not_protected

    assert_not_protected(board_name=board, macs=[lan_mac], hostname=host,
                         addresses=[ssh_host])
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Iterable

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _REPO_ROOT / "config" / "protected_devices.yaml"


class ProtectedDeviceError(RuntimeError):
    """The target is a protected production/control device."""


def _config_path() -> Path:
    return Path(os.environ.get("TOLLGATE_PROTECTED_DEVICES", _DEFAULT_PATH)).expanduser()


def load_protected() -> list[dict]:
    src = _config_path()
    if not src.is_file():
        raise ProtectedDeviceError(
            f"protected-device list missing at {src}; refusing to classify targets"
        )
    doc = yaml.safe_load(src.read_text()) or {}
    rows = doc.get("protected") or []
    if not isinstance(rows, list):
        raise ProtectedDeviceError(f"{src}: 'protected' must be a list")
    return rows


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _norm_mac(value: str) -> str:
    return value.strip().lower().replace("-", ":")


def normalize_macs(values: Iterable[str] | str | None) -> set[str]:
    return {_norm_mac(v) for v in _as_list(values) if v}


def _norm_text(value: str) -> str:
    return value.strip().lower()


_BY_ID_SERIAL_RE = re.compile(r"_([A-Z0-9]{6,})-if\d", re.IGNORECASE)


def resolve_serial_adapter(path: str, *, udev: Callable[[str], str | None] | None = None) -> str | None:
    """The USB adapter's serial number for a console path, or None.

    Two sources, in order: the by-id symlink name carries the serial
    (``usb-FTDI_FT232R_USB_UART_BG02QAPG-if00-port0``), otherwise udev is asked
    for ``ID_SERIAL_SHORT``. Resolving by SERIAL and not by path is deliberate:
    ``/dev/ttyUSB0`` renumbers on replug, so a path-based guard would protect a
    different cable after the next reboot.
    """
    name = os.path.basename(os.path.realpath(path) if os.path.islink(path) else path)
    match = _BY_ID_SERIAL_RE.search(path) or _BY_ID_SERIAL_RE.search(name)
    if match:
        return match.group(1).upper()
    probe = udev or _udev_serial_short
    try:
        serial = probe(path)
    except Exception:  # noqa: BLE001 — an unresolvable adapter is reported, not raised
        return None
    return serial.upper() if serial else None


def _udev_serial_short(path: str) -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["udevadm", "info", "-q", "property", "-n", path],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("ID_SERIAL_SHORT="):
            return line.split("=", 1)[1].strip() or None
    return None


def assert_console_not_protected(
    path: str,
    *,
    udev: Callable[[str], str | None] | None = None,
) -> str:
    """Raise when a console path belongs to a protected device.

    This is the guard for the incident this function was written after
    (2026-11-03): the production gateway's console was probed by a harness that
    had no idea what was on the other end of the cable. Checking the IDENTITY of
    the adapter — not the path, not the place name — is what makes that
    impossible rather than merely discouraged.

    An adapter whose serial CANNOT be resolved is refused too. "I don't know what
    is on this cable" is exactly the state that caused the incident, so it fails
    closed rather than open.
    """
    serial = resolve_serial_adapter(path, udev=udev)
    if not serial:
        raise ProtectedDeviceError(
            f"console {path!r}: cannot determine the USB adapter serial — refusing "
            "rather than probing an unidentified cable"
        )
    row = match_protected(serial_adapters=[serial])
    if row:
        raise ProtectedDeviceError(
            f"console {path!r} (adapter {serial}) belongs to a protected device "
            f"({row.get('id', '?')}): {row.get('reason', 'do not test on this device')}"
        )
    return serial


def _model_variants(value: str) -> set[str]:
    """Spellings of one model string that must all match the protected list.

    A board reports its model as ``GL.iNet GL-MT6000`` while the protected list
    stores the vendor-catalogue form ``GL-MT6000``. Exact matching therefore
    MISSES the production gateway: measured 2026-11-03, ``is_protected(model=
    "GL.iNet GL-MT6000")`` returned False while ``is_protected(model=
    "GL-MT6000")`` returned True — a fail-OPEN on the one device the list exists
    to protect.

    So each identifier is compared on both its full form and its vendor-stripped
    form. Stripping only a leading vendor prefix is deliberate: it must not
    collapse two genuinely different models into one.
    """
    norm = _norm_text(value)
    if not norm:
        return set()
    variants = {norm}
    stripped = _VENDOR_PREFIX_RE.sub("", norm).strip()
    if stripped and stripped != norm:
        variants.add(stripped)
    return variants


_VENDOR_PREFIX_RE = re.compile(
    r"^(?:gl\.inet|glinet|d-?link|tp-?link|netgear|linksys|asus|xiaomi|cudy|comfast|openwrt)\s+",
    re.IGNORECASE,
)


def match_protected(
    *,
    board_name: str | None = None,
    model: str | None = None,
    macs: Iterable[str] | str | None = None,
    hostname: str | None = None,
    addresses: Iterable[str] | str | None = None,
    serial_adapters: Iterable[str] | str | None = None,
) -> dict | None:
    """Return the protected entry matching any supplied identifier, else None."""
    supplied_macs = normalize_macs(macs)
    supplied_hosts = {_norm_text(hostname)} if hostname else set()
    supplied_addrs = {_norm_text(a) for a in _as_list(addresses) if a}
    supplied_adapters = {_norm_text(a) for a in _as_list(serial_adapters) if a}
    board = _norm_text(board_name) if board_name else ""
    model_l = _norm_text(model) if model else ""
    board_v = _model_variants(board_name or "")
    model_v = _model_variants(model or "")

    for row in load_protected():
        boards: set[str] = set()
        for b in _as_list(row.get("board_names")):
            boards |= _model_variants(b)
        models: set[str] = set()
        for m in _as_list(row.get("models")):
            models |= _model_variants(m)
        row_macs = normalize_macs(row.get("macs"))
        hosts = {_norm_text(h) for h in _as_list(row.get("hostnames"))}
        addrs = {_norm_text(a) for a in _as_list(row.get("addresses"))}

        # `board_name` and `model` are both IDENTITY spellings of the same
        # device, and which one a board reports through which field is
        # firmware-dependent (this MT6000 answers its model, "GL.iNet GL-MT6000",
        # to the board-model read). Comparing each field only against its own
        # list meant a real board read missed the entry that names it.
        identities = boards | models
        supplied = board_v | model_v
        if identities & supplied:
            return row
        if supplied_macs and supplied_macs & row_macs:
            return row
        if supplied_hosts and supplied_hosts & hosts:
            return row
        if supplied_addrs and supplied_addrs & addrs:
            return row
        row_adapters = {_norm_text(a) for a in _as_list(row.get("serial_adapters")) if a}
        if supplied_adapters and supplied_adapters & row_adapters:
            return row
    return None


def is_protected(**kwargs) -> bool:
    return match_protected(**kwargs) is not None


def assert_not_protected(**kwargs) -> None:
    row = match_protected(**kwargs)
    if row:
        raise ProtectedDeviceError(
            f"target is protected ({row.get('id', '?')}): "
            f"{row.get('reason', 'do not test on this device')}"
        )


def protected_ids() -> list[str]:
    return [str(r.get("id", "?")) for r in load_protected()]
