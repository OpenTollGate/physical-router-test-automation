"""Load Makefile → pytest migration registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MAP = _PROJECT_ROOT / "config" / "make-pytest-map.yaml"


@dataclass(frozen=True)
class MigrationEntry:
    make_target: str
    pytest: str = ""
    status: str = "make-only"
    lock: str = "none"
    router_env: str = "mint-health"
    markers: str = ""
    timeout: int = 600
    requires: tuple[str, ...] = ()
    runner: str = "pytest"
    delegate: str = ""
    notes: str = ""

    @property
    def is_migrated(self) -> bool:
        return self.status == "migrated"

    @property
    def is_ops(self) -> bool:
        return self.status == "ops"

    @property
    def pytest_nodes(self) -> list[str]:
        if not self.pytest:
            return []
        return self.pytest.split()


def load_registry(path: str | Path | None = None) -> dict[str, MigrationEntry]:
    map_path = Path(path) if path else _DEFAULT_MAP
    with open(map_path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    entries: dict[str, MigrationEntry] = {}
    for target, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        requires = spec.get("requires", "")
        if isinstance(requires, str):
            req_tuple = tuple(r.strip() for r in requires.split(",") if r.strip())
        else:
            req_tuple = tuple(requires)
        entries[target] = MigrationEntry(
            make_target=target,
            pytest=str(spec.get("pytest", "") or ""),
            status=str(spec.get("status", "make-only")),
            lock=str(spec.get("lock", "none")),
            router_env=str(spec.get("router_env", "mint-health")),
            markers=str(spec.get("markers", "") or ""),
            timeout=int(spec.get("timeout", 600)),
            requires=req_tuple,
            runner=str(spec.get("runner", "pytest")),
            delegate=str(spec.get("delegate", "") or ""),
            notes=str(spec.get("notes", "") or ""),
        )
    return entries


def get_entry(target: str, path: str | Path | None = None) -> MigrationEntry | None:
    return load_registry(path).get(target)
