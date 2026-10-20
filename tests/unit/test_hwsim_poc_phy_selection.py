"""Unit tests for the hwsim PHY selection in scripts/hwsim-netns-poc.py.

Regression guard for a measured defect (fleet host CobradorWave, 2026-10-06):
``_move_phys_to_namespaces`` took the first three entries of ``ls
/sys/class/ieee80211``.  That listing is alphabetical, so on any host with a
physical Wi-Fi card ``phy0`` — the hardware radio — sorts first and the run
created its AP interface on real silicon.  Observed symptom: the "alpha" AP
never came up (only ``TollGate-BRAVO`` was visible in the client's ``iw scan``)
and the aborted run **leaked ``alpha-ap`` onto the Intel card**
(``phy0 -> /sys/devices/pci0000:00/0000:00:1c.2/0000:3a:00.0/ieee80211/phy0``).

These tests are pure: no root, no ``mac80211_hwsim``, no netns.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
POC = REPO_ROOT / "scripts" / "hwsim-netns-poc.py"


def _load_poc() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hwsim_netns_poc", POC)
    assert spec and spec.loader, f"cannot load {POC}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["hwsim_netns_poc"] = module
    spec.loader.exec_module(module)
    return module


# A realistic listing on a host that HAS a WiFi card: the hardware radio sorts
# first (phy0), the simulator radios the run just created sort after it. This is
# `readlink -f /sys/class/ieee80211/*` verbatim.
LISTING_WITH_HARDWARE_RADIO = "\n".join(
    [
        "/sys/devices/pci0000:00/0000:00:1c.2/0000:3a:00.0/ieee80211/phy0",
        "/sys/devices/virtual/mac80211_hwsim/hwsim0/ieee80211/phy13",
        "/sys/devices/virtual/mac80211_hwsim/hwsim1/ieee80211/phy14",
        "/sys/devices/virtual/mac80211_hwsim/hwsim2/ieee80211/phy15",
    ]
)

# The same host with no WiFi card: every phy is ours.
LISTING_HWSIM_ONLY = "\n".join(
    [
        "/sys/devices/virtual/mac80211_hwsim/hwsim0/ieee80211/phy3",
        "/sys/devices/virtual/mac80211_hwsim/hwsim1/ieee80211/phy4",
        "/sys/devices/virtual/mac80211_hwsim/hwsim2/ieee80211/phy5",
    ]
)


class _Result:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeRunner:
    """Stands in for the POC's Runner: only the listing command is called."""

    def __init__(self, listing: str) -> None:
        self.listing = listing
        self.calls: list[list[str]] = []

    def run(self, cmd: list[str], *, timeout: int = 30, check: bool = True) -> Any:
        self.calls.append(list(cmd))
        assert any("ieee80211" in part for part in cmd), f"unexpected command: {cmd}"
        return _Result(self.listing)


@pytest.fixture(scope="module")
def poc() -> ModuleType:
    return _load_poc()


def test_never_selects_a_hardware_radio(poc, capsys) -> None:
    """The hardware phy is refused, and the three hwsim phys are returned."""
    phys = poc._hwsim_phys(_FakeRunner(LISTING_WITH_HARDWARE_RADIO))
    assert phys == ["phy13", "phy14", "phy15"]
    assert "phy0" not in phys

    err = capsys.readouterr().err
    assert "ignoring non-hwsim radio(s) ['phy0']" in err, (
        "a refused hardware radio must be named, so a future failure is attributable: " + err
    )


def test_negative_control_blind_slice_would_touch_hardware(poc) -> None:
    """Reproduce the old selection from the same fixture.

    If this ever stops holding, the fixture no longer exercises the defect and
    ``test_never_selects_a_hardware_radio`` would pass for the wrong reason.
    """
    old_selection = [Path(p).name for p in LISTING_WITH_HARDWARE_RADIO.splitlines()[:3]]
    assert old_selection == ["phy0", "phy13", "phy14"], old_selection
    assert poc._hwsim_phys(_FakeRunner(LISTING_WITH_HARDWARE_RADIO)) != old_selection


def test_hwsim_only_host_is_unchanged(poc) -> None:
    """A host with no WiFi card keeps the previous behaviour exactly."""
    assert poc._hwsim_phys(_FakeRunner(LISTING_HWSIM_ONLY)) == ["phy3", "phy4", "phy5"]


def test_malformed_input_is_ignored(poc) -> None:
    """readlink noise must not be mistaken for a radio.

    The unexpanded glob (`readlink -f` of a pattern that matched nothing) and
    any non-`phy*` path are dropped, so a host with no phys yields an empty
    list and the caller's own "expected >=3 hwsim phys" error fires — rather
    than a bogus phy name reaching `iw`.
    """
    listing = "\n".join(
        [
            "/sys/devices/virtual/mac80211_hwsim/hwsim0/ieee80211/phy13",
            "/sys/class/ieee80211/*",
            "",
            "garbage",
            "/sys/devices/virtual/mac80211_hwsim/hwsim1/ieee80211/phy14",
        ]
    )
    assert poc._hwsim_phys(_FakeRunner(listing)) == ["phy13", "phy14"]
