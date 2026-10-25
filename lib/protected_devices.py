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
from pathlib import Path
from typing import Iterable

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


def match_protected(
    *,
    board_name: str | None = None,
    model: str | None = None,
    macs: Iterable[str] | str | None = None,
    hostname: str | None = None,
    addresses: Iterable[str] | str | None = None,
) -> dict | None:
    """Return the protected entry matching any supplied identifier, else None."""
    supplied_macs = normalize_macs(macs)
    supplied_hosts = {_norm_text(hostname)} if hostname else set()
    supplied_addrs = {_norm_text(a) for a in _as_list(addresses) if a}
    board = _norm_text(board_name) if board_name else ""
    model_l = _norm_text(model) if model else ""

    for row in load_protected():
        boards = {_norm_text(b) for b in _as_list(row.get("board_names"))}
        models = {_norm_text(m) for m in _as_list(row.get("models"))}
        row_macs = normalize_macs(row.get("macs"))
        hosts = {_norm_text(h) for h in _as_list(row.get("hostnames"))}
        addrs = {_norm_text(a) for a in _as_list(row.get("addresses"))}

        if board and board in boards:
            return row
        if model_l and model_l in models:
            return row
        if supplied_macs and supplied_macs & row_macs:
            return row
        if supplied_hosts and supplied_hosts & hosts:
            return row
        if supplied_addrs and supplied_addrs & addrs:
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
