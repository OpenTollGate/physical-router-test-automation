"""Test-matrix loader tests — the SSOT seam (configs/test-matrix.yaml)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.test_matrix import MatrixParams, matrix  # noqa: E402


def test_defaults_are_the_pinned_matrix():
    m = matrix()
    assert m.version == "25.12.5"
    assert m.family == "25.12"
    assert m.target == "mediatek/filogic"
    assert m.pkg_manager == "apk"
    # the derived filename matches the bench image byte-for-byte
    assert m.image_filename == (
        "openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin"
    )


def test_urls_derive_from_version():
    m = matrix()
    assert m.image_url.startswith(
        "https://downloads.openwrt.org/releases/25.12.5/targets/mediatek/filogic/"
    )
    assert m.sha256_sums_url.endswith("/mediatek/filogic/sha256sums")


def test_required_tokens_gate_the_family():
    tokens = matrix().required_image_tokens
    assert "openwrt-25.12" in tokens
    assert "glinet_gl-mt3000" in tokens
    assert "sysupgrade" in tokens


def test_pkg_manager_follows_major():
    assert matrix().pkg_manager == "apk"
    derived = MatrixParams(
        version="24.10.2", family="24.10", target="t", device="d",
        image_kind="squashfs-sysupgrade", sha256="x", provider_default="shc",
    )
    assert derived.pkg_manager == "opkg"


def test_env_override_replaces_everything(monkeypatch):
    monkeypatch.setenv("PRTA_OPENWRT_VERSION", "25.12.6")
    import lib.test_matrix as tm

    tm.matrix.cache_clear()
    m = tm.matrix()
    assert m.version == "25.12.6"
    assert m.family == "25.12"  # derived from the override, not the file
    assert "25.12.6" in m.image_url
    assert "openwrt-25.12.6" in m.image_filename
    tm.matrix.cache_clear()


def test_env_override_rejects_garbage(monkeypatch):
    monkeypatch.setenv("PRTA_OPENWRT_VERSION", "snapshot")
    import lib.test_matrix as tm

    tm.matrix.cache_clear()
    with pytest.raises(ValueError, match="X.Y"):
        tm.matrix()
    tm.matrix.cache_clear()


def test_properties_are_flat_recordable_strings():
    props = matrix().as_properties()
    assert set(props) == {
        "openwrt_version", "openwrt_family", "openwrt_target",
        "image_device", "pkg_manager", "provider_default",
    }
    assert all(isinstance(v, str) for v in props.values())
