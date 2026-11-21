"""Diagnostic CLI contracts, with all external commands replaced by doubles."""
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
WARNING = "DIAGNOSTIC ONLY: not Gate E or production scheduler qualification"


def run_script(name):
    try:
        runpy.run_path(str(ROOT / "scripts" / name), run_name="__main__")
    except SystemExit as exc:
        return exc.code
    return 0


@pytest.mark.parametrize("failed_call", [None, 0, 1, 2, 3, 4, 5])
def test_real_workload_reports_failure_in_either_phase(monkeypatch, capsys, failed_call):
    calls = []

    def run(argv, **kwargs):
        index = len(calls)
        calls.append(argv)
        return SimpleNamespace(returncode=7 if index == failed_call else 0)

    monkeypatch.setattr(subprocess, "run", run)
    rc = run_script("bench-real-workload.py")
    out = capsys.readouterr().out
    assert len(calls) == 6
    assert rc == (0 if failed_call is None else 1)
    assert WARNING in out
    if failed_call is not None:
        assert "speedup" not in out
        assert "FAILED" in out


def test_throughput_labels_diagnostic_output(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["bench-throughput.py", "--coordinator", "unused.invalid:1",
                                      "--place", "a", "--place", "b"])
    run_script("bench-throughput.py")
    assert WARNING in capsys.readouterr().out
