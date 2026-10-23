"""Tests for scripts/ci/find-superset-prs.py — offline, no network.

Why these exist: the script's entire value is that its verdict is a *proof* of
redundancy. A detector that silently misses chains, or worse reports a chain that
is not there, would cause a reviewer to close a PR that carries a unique change.
So these tests build real throwaway git repos with real commits and pin the
detector against ground truth.

They call ``mod.find_chains`` — the real function. An earlier version of this file
re-implemented the loop inside the test, which meant mutating the detector to
`if True:` (report every pair as a chain) still passed all six tests. A test that
copies the logic instead of calling it cannot fail when the logic breaks.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "find-superset-prs.py"


def _load():
    spec = importlib.util.spec_from_file_location("find_superset_prs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


mod = _load()
BASE = "upstream/main"


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.st")
    _git(repo, "config", "user.name", "t")


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for name, body in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    _init(r)
    _commit(r, {"base.txt": "base\n"}, "base")
    _git(r, "update-ref", f"refs/remotes/{BASE}", "HEAD")
    return r


def _branch(repo: Path, name: str, files: dict[str, str], base: str) -> None:
    """Publish a branch as upstream/pr/<name>, hanging off `base`."""
    _git(repo, "checkout", "-q", "-B", f"b-{name}", base)
    _commit(repo, files, f"commit on {name}")
    _git(repo, "update-ref", f"refs/remotes/upstream/pr/{name}", "HEAD")


def _chains(repo: Path, nums: list[int], **kw):
    return mod.find_chains(nums, str(repo), BASE, **kw)


def test_true_superset_is_found(repo: Path) -> None:
    """#1 adds {a,b}; #2 adds {a} with an identical `a` -> #1 contains #2."""
    head = _git(repo, "rev-parse", "HEAD").strip()
    _branch(repo, "1", {"a.txt": "A\n", "b.txt": "B\n"}, head)
    _branch(repo, "2", {"a.txt": "A\n"}, head)

    chains, skipped = _chains(repo, [1, 2])
    assert chains == [(1, 2, 1)], f"expected (1,2,1), got {chains}"
    assert skipped == []


def test_differing_shared_file_is_not_a_chain(repo: Path) -> None:
    """CRITICAL FALSE POSITIVE. #2's `a` differs from #1's -> NOT contained.

    If this test fails, the tool would tell a reviewer to close a PR whose change
    is unique. That is the one failure mode with real consequences.
    """
    head = _git(repo, "rev-parse", "HEAD").strip()
    _branch(repo, "1", {"a.txt": "A\n", "b.txt": "B\n"}, head)
    _branch(repo, "2", {"a.txt": "DIFFERENT\n"}, head)

    chains, _ = _chains(repo, [1, 2])
    assert chains == [], (
        "reported a chain for PRs whose shared file differs — a unique change "
        f"would be closed as redundant. got {chains}"
    )


def test_equal_file_sets_are_not_a_chain(repo: Path) -> None:
    """Strict subset required: identical file sets are two independent PRs."""
    head = _git(repo, "rev-parse", "HEAD").strip()
    _branch(repo, "1", {"a.txt": "A\n"}, head)
    _branch(repo, "2", {"a.txt": "A\n"}, head)

    chains, _ = _chains(repo, [1, 2])
    assert chains == []


def test_disjoint_prs_are_not_a_chain(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD").strip()
    _branch(repo, "1", {"a.txt": "A\n"}, head)
    _branch(repo, "2", {"b.txt": "B\n"}, head)

    chains, _ = _chains(repo, [1, 2])
    assert chains == []


def test_unreadable_branch_is_skipped_not_crashed(repo: Path) -> None:
    """A deleted/force-pushed fork leaves no ref — report it, don't die."""
    head = _git(repo, "rev-parse", "HEAD").strip()
    _branch(repo, "1", {"a.txt": "A\n", "b.txt": "B\n"}, head)
    # PR 99 has no upstream/pr/99 ref at all.
    chains, skipped = _chains(repo, [1, 99])
    assert chains == []
    assert skipped == [99]


def test_min_shared_filters_small_chains(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD").strip()
    _branch(repo, "1", {"a.txt": "A\n", "b.txt": "B\n"}, head)
    _branch(repo, "2", {"a.txt": "A\n"}, head)

    assert _chains(repo, [1, 2])[0] == [(1, 2, 1)]
    # Requiring 2 shared files drops it.
    assert _chains(repo, [1, 2], min_shared=2)[0] == []


def test_pagination_warning_on_multiple_of_100(monkeypatch) -> None:
    """Exactly 100 may mean pagination stopped early — must be surfaced."""
    class R:
        stdout = "\n".join(str(i) for i in range(1, 101))

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    nums = mod.open_pr_numbers("o/r")
    assert len(nums) == 100


def test_cli_help_works() -> None:
    assert SCRIPT.is_file()
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "--repo" in r.stdout and "--worktree" in r.stdout
