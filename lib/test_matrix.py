"""Test-matrix loader — the single seam between configs/test-matrix.yaml
and every test/module that needs "what are we testing on".

DRY contract (docs/version-pins-ssot.md layering): modules import
`matrix()` instead of pinning versions/URLs; env overrides:

  PRTA_OPENWRT_VERSION  full X.Y.Z — replaces the pinned release
                       everywhere (URLs and expected tokens derive)

Results recording: fixtures attach the resolved matrix as pytest
`record_property` rows so every recorded result carries its parameters.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_MATRIX_PATH = Path(__file__).resolve().parents[1] / "configs" / "test-matrix.yaml"


@dataclass(frozen=True)
class MatrixParams:
    version: str
    family: str
    target: str
    device: str
    image_kind: str
    sha256: str
    provider_default: str

    @property
    def image_filename(self) -> str:
        return f"openwrt-{self.version}-{self.target.replace('/', '-')}-{self.device}-{self.image_kind}.bin"

    @property
    def image_url(self) -> str:
        return (
            f"https://downloads.openwrt.org/releases/{self.version}/targets/{self.target}/"
            + self.image_filename
        )

    @property
    def sha256_sums_url(self) -> str:
        return f"https://downloads.openwrt.org/releases/{self.version}/targets/{self.target}/sha256sums"

    @property
    def required_image_tokens(self) -> tuple[str, ...]:
        return (
            f"openwrt-{self.family}",  # release family gate
            self.target.replace("/", "-"),
            self.device,
            self.image_kind.split("-", 1)[-1],  # e.g. sysupgrade
        )

    @property
    def pkg_manager(self) -> str:
        return "opkg" if self.version.startswith("24.") else "apk"

    def as_properties(self) -> dict[str, str]:
        """Flat, junit-recordable form of the parameters."""
        return {
            "openwrt_version": self.version,
            "openwrt_family": self.family,
            "openwrt_target": self.target,
            "image_device": self.device,
            "pkg_manager": self.pkg_manager,
            "provider_default": self.provider_default,
        }


def _validate_version(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", version):
        raise ValueError(
            f"PRTA_OPENWRT_VERSION must be X.Y[.Z], got {version!r}"
        )
    return version


@lru_cache(maxsize=1)
def matrix() -> MatrixParams:
    raw = yaml.safe_load(_MATRIX_PATH.read_text())
    openwrt = raw["openwrt"]
    version = os.environ.get("PRTA_OPENWRT_VERSION") or openwrt["version"]
    if os.environ.get("PRTA_OPENWRT_VERSION"):
        version = _validate_version(version)
        # the family token derives from the override, not the file
        family = ".".join(version.split(".")[:2])
    else:
        family = openwrt["family"]
    return MatrixParams(
        version=version,
        family=family,
        target=openwrt["target"],
        device=openwrt["device"],
        image_kind=openwrt["image_kind"],
        sha256=openwrt["sha256"],
        provider_default=raw.get("provider", {}).get("default", "shc"),
    )
