"""Offline unit tests for the virtual-lab overlay lifecycle (#26).

A stop-poc/start-poc cycle reused the DUT overlay by design, so a corrupted
DUT came back identical after a "fresh" restart — the issue's clean-DUT
procedure was a manual ``rm -f overlays/*.qcow2`` between stop and start.
These tests pin the replacement, everything offline (no SSH, no qemu, no
router):

* ``start-poc --fresh`` parses, purges the overlay (warning about the
  internal snapshots it discards), and refuses to honor the purge on a
  live VM instead of silently skipping it;
* the default path keeps the reuse behavior but ANNOUNCES it, so the
  persistence is discoverable at the console;
* doctor's DUT integrity check detects zero-byte binaries in /usr/bin (the
  opkg orphan-removal corruption class) and fails doctor, while an
  unreachable DUT is a skip, not a failure.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "virtual-lab.py"

_spec = importlib.util.spec_from_file_location("virtual_lab_test", SCRIPT)
vl = importlib.util.module_from_spec(_spec)
sys.modules["virtual_lab_test"] = vl
_spec.loader.exec_module(vl)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _make_stub_bin(tmp_path: Path) -> Path:
    """A PATH-prependable bin dir with qemu-img/sshpass/ssh stubs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_executable(
        bin_dir / "qemu-img",
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  snapshot)\n"
        "    # Real qemu-img prints header + dashed separator + one row per\n"
        "    # snapshot; the snippet counts from line 3.\n"
        "    [ \"$2\" = \"-l\" ] && { printf 'ID  TAG    VM SIZE\\n--- ---     -------\\n1  snap1  0\\n2  snap2  0\\n'; exit 0; }\n"
        "    ;;\n"
        "  create)\n"
        "    for a in \"$@\"; do last=\"$a\"; done\n"
        "    : > \"$last\"\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n",
    )
    # The snippet calls `sshpass -p <pw> ssh ...` — drop the two password
    # args, then exec the rest (a bare `exec "$@"` would try to exec `-p`).
    _write_executable(bin_dir / "sshpass", "#!/bin/sh\nshift 2\nexec \"$@\"\n")
    _write_executable(
        bin_dir / "ssh",
        "#!/bin/bash\n"
        "for a in \"$@\"; do cmd=\"$a\"; done\n"
        "[ -n \"$STUB_SSH_RC\" ] && exit \"$STUB_SSH_RC\"\n"
        "case \"$cmd\" in\n"
        "  true) exit 0 ;;\n"
        "  find\\ *)\n"
        "    c=\"$cmd\"\n"
        "    c=\"${c//\\/usr\\/bin/$STUB_USRBIN}\"\n"
        "    c=\"${c//\\/usr\\/sbin/$STUB_USRSBIN}\"\n"
        "    c=\"${c//\\/usr\\/lib/$STUB_USRLIB}\"\n"
        "    c=\"${c//\\/lib/$STUB_LIB}\"\n"
        "    exec bash -c \"$c\" ;;\n"
        "esac\n"
        "exit 0\n",
    )
    return bin_dir


def _run_snippet(snippet: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    harness = (
        "set -eu\n"
        "disk=\"$TMP/disk.qcow2\"\n"
        "base=\"$TMP/base.qcow2\"\n"
        ": > \"$base\"\n"
        + snippet
    )
    full_env = {**os.environ, **env}
    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True, text=True, env=full_env, timeout=30,
    )


# ------------------------------------------------------------- start-poc --fresh

def test_start_poc_parses_fresh_flag() -> None:
    args = vl.build_parser().parse_args(["start-poc", "--fresh"])
    assert args.fresh is True
    args = vl.build_parser().parse_args(["start-poc"])
    assert args.fresh is False


def test_fresh_purges_existing_overlay_and_warns_about_snapshots(tmp_path: Path) -> None:
    bin_dir = _make_stub_bin(tmp_path)
    (tmp_path / "disk.qcow2").write_bytes(b"corrupted overlay content")

    result = _run_snippet(
        vl._overlay_prepare_script(fresh=True),
        {"PATH": f"{bin_dir}:{os.environ['PATH']}", "TMP": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "Purged the DUT overlay (--fresh)" in result.stdout
    # The stub's snapshot table has 2 rows -> the discard warning must say 2.
    assert "discards 2 internal snapshot(s)" in result.stdout
    # The overlay was purged and re-created (empty stub file, not the old bytes).
    assert (tmp_path / "disk.qcow2").read_bytes() == b""


def test_fresh_on_missing_overlay_just_creates(tmp_path: Path) -> None:
    bin_dir = _make_stub_bin(tmp_path)
    result = _run_snippet(
        vl._overlay_prepare_script(fresh=True),
        {"PATH": f"{bin_dir}:{os.environ['PATH']}", "TMP": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "Purged" not in result.stdout
    assert (tmp_path / "disk.qcow2").exists()


def test_default_reuses_overlay_and_announces_it(tmp_path: Path) -> None:
    bin_dir = _make_stub_bin(tmp_path)
    (tmp_path / "disk.qcow2").write_bytes(b"previous DUT state")

    result = _run_snippet(
        vl._overlay_prepare_script(fresh=False),
        {"PATH": f"{bin_dir}:{os.environ['PATH']}", "TMP": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    assert "Reusing existing DUT overlay" in result.stdout
    assert "--fresh" in result.stdout
    # The persistence itself is unchanged: same bytes, no recreation.
    assert (tmp_path / "disk.qcow2").read_bytes() == b"previous DUT state"


def test_infra_script_refuses_fresh_on_live_vm(tmp_path: Path) -> None:
    """--fresh + a running VM must FAIL, not silently skip the purge."""
    captured: dict[str, str] = {}

    def fake_run_remote(host: str, script: str, timeout: int = 60) -> object:
        captured["script"] = script
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    original = vl.run_remote
    vl.run_remote = fake_run_remote  # type: ignore[assignment]
    try:
        args = vl.build_parser().parse_args(["start-poc", "--fresh"])
        vl.start_poc(args)
    finally:
        vl.run_remote = original  # type: ignore[assignment]

    guard = captured["script"]
    assert "--fresh needs it stopped" in guard
    assert "exit 1" in guard.split("--fresh needs it stopped")[1][:60]


def test_infra_script_default_keeps_idempotent_running_exit(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def fake_run_remote(host: str, script: str, timeout: int = 60) -> object:
        captured["script"] = script
        return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

    original = vl.run_remote
    vl.run_remote = fake_run_remote  # type: ignore[assignment]
    try:
        args = vl.build_parser().parse_args(["start-poc"])
        vl.start_poc(args)
    finally:
        vl.run_remote = original  # type: ignore[assignment]

    guard = captured["script"]
    assert "--fresh needs it stopped" not in guard


# ------------------------------------------------------------------ doctor DUT

def _run_dut_check(tmp_path: Path, usrbin_files: dict[str, int], ssh_rc: str = "") -> subprocess.CompletedProcess[str]:
    bin_dir = _make_stub_bin(tmp_path)
    # The doctor scan covers /usr/bin /usr/sbin /usr/lib /lib. Give EVERY one a
    # stub tree and leave the other three empty, so "clean DUT" is an assertion
    # about the files we placed rather than about whatever happens to exist on
    # the machine running the suite. (Before this, only /usr/bin was rewritten
    # and the widened scan read the real host filesystem.)
    trees: dict[str, Path] = {}
    for key, sub in (
        ("STUB_USRBIN", "usrbin"),
        ("STUB_USRSBIN", "usrsbin"),
        ("STUB_USRLIB", "usrlib"),
        ("STUB_LIB", "lib"),
    ):
        d = tmp_path / sub
        d.mkdir(exist_ok=True)
        trees[key] = d
    for name, size in usrbin_files.items():
        target = trees["STUB_USRBIN"] / name
        target.write_bytes(b"x" * size if size else b"")
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_SSH_RC": ssh_rc,
        **{k: str(v) for k, v in trees.items()},
    }
    return subprocess.run(
        ["bash", "-c", vl._dut_integrity_script()],
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_doctor_dut_check_flags_zero_byte_binaries(tmp_path: Path) -> None:
    result = _run_dut_check(
        tmp_path,
        {"nodogsplash": 0, "curl": 0, "jq": 512_000},
    )
    assert result.returncode == 0, result.stderr
    assert "DUT_ZERO_BYTE_BINARIES=1" in result.stdout
    assert "zero-byte system files" in result.stdout
    # find prints absolute paths (/usr/bin/<name>, rewritten to the stub
    # dir by the ssh shim) — match on the basename, and only the zeroed
    # ones: jq has real bytes and must not be reported.
    reported = {line.rsplit("/", 1)[-1] for line in result.stdout.splitlines() if "/" in line and "usrbin" in line}
    assert {"nodogsplash", "curl"} <= reported
    assert "jq" not in reported
    # remediation must name the new flag
    assert "start-poc --fresh" in result.stdout


def test_doctor_dut_check_clean_dut_has_no_sentinel(tmp_path: Path) -> None:
    result = _run_dut_check(tmp_path, {"nodogsplash": 900_000, "curl": 300_000})
    assert result.returncode == 0, result.stderr
    assert "DUT_ZERO_BYTE_BINARIES=1" not in result.stdout
    assert "no zero-byte" in result.stdout


def test_doctor_dut_check_does_not_read_the_real_host_filesystem(tmp_path: Path) -> None:
    """The stub must cover EVERY path the doctor scan reaches.

    Regression guard for the bug where the scan was widened to
    `/usr/bin /usr/sbin /usr/lib /lib` but the ssh shim rewrote only
    `/usr/bin`: the test then read the *real* host filesystem, so the result
    depended on whether the CI/dev machine happened to have a zero-byte file
    anywhere under those trees. It passed by luck on a clean host and failed on
    a dirty one — the worst kind of flake.

    Proof: plant a zero-byte file in each stubbed tree and assert it is reported.
    If a tree is not rewritten, the planted file is invisible and this fails.
    """
    for key, sub in (
        ("STUB_USRSBIN", "usrsbin"),
        ("STUB_USRLIB", "usrlib"),
        ("STUB_LIB", "lib"),
    ):
        d = tmp_path / sub
        d.mkdir(exist_ok=True)
        (d / f"planted_{sub}").write_bytes(b"")  # zero-byte, must be found
    # _run_dut_check mkdirs with exist_ok, so the planted files survive.
    result = _run_dut_check(tmp_path, {"nodogsplash": 900_000})
    assert result.returncode == 0, result.stderr
    assert "DUT_ZERO_BYTE_BINARIES=1" in result.stdout, (
        "a zero-byte file planted under /usr/sbin, /usr/lib or /lib was NOT "
        "reported — the ssh shim is not rewriting that tree, so the scan is "
        f"reading the real host filesystem.\nstdout:\n{result.stdout}"
    )
    for sub in ("usrsbin", "usrlib", "lib"):
        assert f"planted_{sub}" in result.stdout, f"missing planted_{sub} in output"


def test_doctor_dut_check_unreachable_is_skip_not_failure(tmp_path: Path) -> None:
    result = _run_dut_check(tmp_path, {}, ssh_rc="255")
    assert result.returncode == 0, result.stderr
    assert "unreachable" in result.stdout
    assert "skipped" in result.stdout
    assert "DUT_ZERO_BYTE_BINARIES=1" not in result.stdout


def test_doctor_fails_on_sentinel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Result:
        returncode = 0
        stdout = "== DUT (OpenWrt VM) integrity ==\nnodogsplash\nDUT_ZERO_BYTE_BINARIES=1\n"
        stderr = ""

    def fake_run_remote(host: str, script: str, timeout: int = 60) -> Result:
        return Result()

    monkeypatch.setattr(vl, "run_remote", fake_run_remote)
    args = vl.build_parser().parse_args(["doctor"])
    rc = vl.doctor(args)
    assert rc == 1
