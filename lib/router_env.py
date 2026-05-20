"""Load router connection settings from mint-health/upstream-wifi routers.env."""

from __future__ import annotations

import os
import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_file(subdir: str) -> Path:
    return _PROJECT_ROOT / subdir / "routers.env"


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data


def router_prefix(router_label: str) -> str:
    return f"ROUTER_{router_label.upper()}"


def load_router_into_environ(
    router_label: str,
    *,
    env_subdir: str = "mint-health",
    secondary_label: str | None = None,
) -> dict[str, str]:
    """Set TOLLGATE_* env vars from routers.env for *router_label*."""
    parsed = _parse_env_file(_env_file(env_subdir))
    prefix = router_prefix(router_label)

    host = parsed.get(f"{prefix}_HOST", "")
    if not host:
        raise RuntimeError(
            f"Unknown router '{router_label}': {prefix}_HOST not in {env_subdir}/routers.env"
        )

    updates = {
        "TOLLGATE_ROUTER_ID": router_label,
        "TOLLGATE_SSH_HOST": host,
        "TOLLGATE_ROUTER_INVENTORY": str(_PROJECT_ROOT / "config" / "routers.json"),
        "TOLLGATE_SKIP_ROUTER_INVENTORY": "1",
    }

    password = (
        parsed.get(f"{prefix}_PASSWORD", "")
        or os.environ.get("TOLLGATE_LUCI_PASSWORD", "")
        or os.environ.get("TOLLGATE_SSH_PASSWORD", "")
    )
    if password:
        updates["TOLLGATE_SSH_PASSWORD"] = password
        updates["TOLLGATE_LUCI_PASSWORD"] = password

    serial = parsed.get(f"{prefix}_SERIAL", "")
    if serial:
        updates["TOLLGATE_SERIAL_PORT"] = serial

    netbird = parsed.get(f"{prefix}_NETBIRD_HOST", "")
    if netbird:
        updates["TOLLGATE_NETBIRD_HOST"] = netbird

    lan = parsed.get(f"{prefix}_LAN_HOST", "")
    if lan:
        updates["TOLLGATE_LAN_HOST"] = lan

    ssid = parsed.get(f"{prefix}_SSID", "") or parsed.get(f"{prefix}_PSK_SSID", "")
    if ssid:
        updates["TOLLGATE_ROUTER_SSID"] = ssid
    psk = parsed.get(f"{prefix}_PSK_PASS", "")
    if psk:
        updates["TOLLGATE_ROUTER_PSK"] = psk

    if secondary_label:
        sec_prefix = router_prefix(secondary_label)
        sec_host = parsed.get(f"{sec_prefix}_HOST", "")
        if sec_host:
            updates["TOLLGATE_SECONDARY_ROUTER_HOST"] = sec_host
        sec_ssid = parsed.get(f"{sec_prefix}_SSID", "") or parsed.get(f"{sec_prefix}_PSK_SSID", "")
        if sec_ssid:
            updates["TOLLGATE_SECONDARY_ROUTER_SSID"] = sec_ssid

    for key, val in updates.items():
        os.environ[key] = val

    return updates


def apply_cli_overrides(
    *,
    ssid: str | None = None,
    password: str | None = None,
    mint: str | None = None,
) -> None:
    if ssid:
        os.environ["TOLLGATE_UPSTREAM_WIFI_SSID"] = ssid
    if password:
        os.environ["TOLLGATE_UPSTREAM_WIFI_PASSWORD"] = password
    if mint:
        os.environ["TOLLGATE_TEST_MINT_URL"] = mint


def resolve_secondary_for_two_router(router_label: str, env_subdir: str = "mint-health") -> str:
    """Pick beta as secondary when primary is alpha, else first other router in env."""
    parsed = _parse_env_file(_env_file(env_subdir))
    labels = set()
    for key in parsed:
        m = re.match(r"ROUTER_([A-Z0-9_]+)_HOST$", key)
        if m:
            labels.add(m.group(1).lower())
    current = router_label.lower()
    if current == "alpha" and "beta" in labels:
        return "beta"
    for label in sorted(labels):
        if label != current:
            return label
    return ""
