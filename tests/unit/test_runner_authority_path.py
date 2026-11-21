"""Parser-only checks: do not create stores, run tests, or contact hardware."""
import argparse
import pytest
from test_runner_fleet_lifecycle import runner


@pytest.mark.parametrize("explicit", [False, True])
def test_runner_honors_shared_authority_environment(monkeypatch, tmp_path, explicit):
    shared = tmp_path / "shared.sqlite3"
    override = tmp_path / "override.sqlite3"
    monkeypatch.setenv("TOLLGATE_FLEET_AUTHORITY", str(shared))
    argv = ["run-profile.py", "--profile", "unit"]
    if explicit:
        argv += ["--authority-store", str(override)]
    monkeypatch.setattr("sys.argv", argv)
    original = argparse.ArgumentParser.parse_known_args
    captured = []

    class Parsed(Exception):
        pass

    def capture(parser, *args, **kwargs):
        result = original(parser, *args, **kwargs)
        captured.append(result[0])
        raise Parsed

    monkeypatch.setattr(argparse.ArgumentParser, "parse_known_args", capture)
    with pytest.raises(Parsed):
        runner.main()
    assert captured[0].authority_store == str(override if explicit else shared)
    assert not shared.exists()
    assert not override.exists()
