"""Unit tests for lib.reflash — post-test fleet reflash logic.

The conwrt flashing functions are injected as doubles, so these tests
run without conwrt installed, without real routers, and without any
network access. They pin the gating, error-handling, and per-router
isolation behavior of the post-test reflash flow.
"""
import types
from unittest.mock import MagicMock

import pytest

from lib.reflash import reflash_fleet, ReflashResult


def _fake_router(host: str):
    """Minimal stand-in for the Router object used by all_routers."""
    obj = types.SimpleNamespace()
    obj.host = host
    return obj


@pytest.fixture
def fleet():
    return {
        "router-a": _fake_router("192.168.1.1"),
        "router-b": _fake_router("192.168.1.2"),
    }


@pytest.fixture
def tmp_image(tmp_path):
    image = tmp_path / "firmware.bin"
    image.write_bytes(b"\x00" * 16)
    return str(image)


@pytest.fixture
def tmp_conwrt(tmp_path):
    """A fake conwrt checkout with a scripts/ subdirectory."""
    scripts = tmp_path / "conwrt" / "scripts"
    scripts.mkdir(parents=True)
    return str(tmp_path / "conwrt")


class TestDisabledByDefault:
    def test_disabled_is_noop(self, fleet, tmp_image, tmp_conwrt):
        result = reflash_fleet(fleet, tmp_image, enable=False, conwrt_dir=tmp_conwrt)
        assert isinstance(result, ReflashResult)
        assert result.disabled is True
        assert result.reflashed == []
        assert result.failed == {}
        assert result.image_missing is False
        assert result.conwrt_unavailable is False


class TestImageMissing:
    def test_none_image_path(self, fleet, tmp_conwrt):
        result = reflash_fleet(fleet, None, enable=True, conwrt_dir=tmp_conwrt)
        assert result.image_missing is True
        assert result.reflashed == []

    def test_nonexistent_image_path(self, fleet, tmp_conwrt):
        result = reflash_fleet(fleet, "/no/such/firmware.bin", enable=True, conwrt_dir=tmp_conwrt)
        assert result.image_missing is True
        assert result.reflashed == []


class TestConwrtUnavailable:
    def test_conwrt_dir_missing(self, fleet, tmp_image, tmp_path):
        result = reflash_fleet(
            fleet, tmp_image, enable=True,
            conwrt_dir=str(tmp_path / "does-not-exist"),
        )
        assert result.conwrt_unavailable is True
        assert result.reflashed == []


class TestHappyPath:
    def test_all_routers_reflashed(self, fleet, tmp_image, tmp_conwrt):
        flash = MagicMock(return_value=True)
        reboot_wait = MagicMock()
        result = reflash_fleet(
            fleet, tmp_image, enable=True, conwrt_dir=tmp_conwrt,
            flash_fn=flash, reboot_wait_fn=reboot_wait,
        )
        assert set(result.reflashed) == {"router-a", "router-b"}
        assert result.failed == {}
        assert flash.call_count == 2
        flash.assert_any_call("192.168.1.1", tmp_image)
        flash.assert_any_call("192.168.1.2", tmp_image)
        assert reboot_wait.call_count == 2
        reboot_wait.assert_any_call("192.168.1.1", timeout=180)
        reboot_wait.assert_any_call("192.168.1.2", timeout=180)


class TestPartialFailure:
    def test_one_router_exception_does_not_block_others(self, fleet, tmp_image, tmp_conwrt):
        def flaky_flash(host, image):
            if host == "192.168.1.1":
                raise RuntimeError("ssh timeout")
            return True
        reboot_wait = MagicMock()
        result = reflash_fleet(
            fleet, tmp_image, enable=True, conwrt_dir=tmp_conwrt,
            flash_fn=flaky_flash, reboot_wait_fn=reboot_wait,
        )
        assert result.reflashed == ["router-b"]
        assert "router-a" in result.failed
        assert "ssh timeout" in result.failed["router-a"]
        reboot_wait.assert_called_once_with("192.168.1.2", timeout=180)

    def test_flash_returning_false_is_recorded_as_failed(self, fleet, tmp_image, tmp_conwrt):
        flash = MagicMock(return_value=False)
        result = reflash_fleet(
            fleet, tmp_image, enable=True, conwrt_dir=tmp_conwrt,
            flash_fn=flash, reboot_wait_fn=MagicMock(),
        )
        assert result.reflashed == []
        assert set(result.failed) == {"router-a", "router-b"}

    def test_router_without_host_recorded_as_failed(self, tmp_image, tmp_conwrt):
        fleet = {"router-x": types.SimpleNamespace()}  # no .host attr
        result = reflash_fleet(
            fleet, tmp_image, enable=True, conwrt_dir=tmp_conwrt,
            flash_fn=MagicMock(), reboot_wait_fn=MagicMock(),
        )
        assert result.reflashed == []
        assert "router-x" in result.failed
