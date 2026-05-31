"""Resolve --pr / --branch / --commit into a RunTarget."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from lib.backend import BackendConfig


@dataclass(frozen=True)
class RunTarget:
    repo: str
    branch: str
    sut_commit: str
    pr: str | None
    backend: str
    pr_repo: str = ""

    @property
    def workflow(self) -> str:
        return BackendConfig(self.backend).workflow


def _gh_json(args: list[str], timeout: int = 30) -> dict | list:
    r = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return json.loads(r.stdout)


def _resolve_pr(pr_number: str, backend: str) -> RunTarget:
    backend_cfg = BackendConfig(backend)
    data = _gh_json([
        "pr", "view", pr_number,
        "--repo", backend_cfg.repo,
        "--json", "headRefName,headRefOid,headRepository,headRepositoryOwner",
    ])
    if not isinstance(data, dict):
        raise RuntimeError(f"PR {pr_number}: unexpected gh response type")
    ref = data.get("headRefName")
    sha = data.get("headRefOid")
    head_repo = data.get("headRepository") or {}
    head_owner = data.get("headRepositoryOwner") or {}
    owner = head_owner.get("login") if isinstance(head_owner, dict) else None
    name = head_repo.get("name") if isinstance(head_repo, dict) else None
    if not isinstance(ref, str) or not isinstance(sha, str):
        raise RuntimeError(f"PR {pr_number} has no usable head ref/sha")
    repo = f"{owner}/{name}" if owner and name else backend_cfg.repo
    return RunTarget(
        repo=repo,
        branch=ref,
        sut_commit=sha,
        pr=pr_number,
        backend=backend,
        pr_repo=backend_cfg.repo,
    )


def _resolve_commit(commit: str, backend: str, branch_hint: str | None) -> RunTarget:
    backend_cfg = BackendConfig(backend)
    repo = backend_cfg.repo
    short = commit[:7] if len(commit) >= 7 else commit

    if branch_hint:
        return RunTarget(
            repo=repo, branch=branch_hint, sut_commit=commit, pr=None, backend=backend, pr_repo=repo,
        )

    # Try to find an open PR whose head matches this commit
    try:
        prs = _gh_json([
            "pr", "list",
            "--repo", repo,
            "--state", "all",
            "--search", f"head:{short}",
            "--json", "number,headRefOid,headRefName,headRepository,headRepositoryOwner",
            "--limit", "10",
        ])
        if isinstance(prs, list):
            for item in prs:
                oid = item.get("headRefOid", "")
                if oid == commit or oid.startswith(commit) or commit.startswith(oid[: len(commit)]):
                    pr_num = str(item.get("number", ""))
                    ref = item.get("headRefName", "")
                    head_repo = item.get("headRepository") or {}
                    head_owner = item.get("headRepositoryOwner") or {}
                    owner = head_owner.get("login") if isinstance(head_owner, dict) else None
                    name = head_repo.get("name") if isinstance(head_repo, dict) else None
                    pr_repo = f"{owner}/{name}" if owner and name else repo
                    return RunTarget(
                        repo=pr_repo,
                        branch=str(ref),
                        sut_commit=oid if isinstance(oid, str) else commit,
                        pr=pr_num or None,
                        backend=backend,
                        pr_repo=repo,
                    )
    except RuntimeError:
        pass

    raise RuntimeError(
        f"Cannot resolve commit {commit} without --branch. "
        f"Pass --branch <name> or use --pr <N>."
    )


def resolve_target(
    *,
    pr: str | None = None,
    branch: str | None = None,
    commit: str | None = None,
    backend: str = "go",
    repo_override: str | None = None,
) -> RunTarget:
    """Resolve exactly one of pr, branch, or commit into a RunTarget.

    *repo_override* forces the artifact repo (e.g. a fork) instead of
    deriving it from the backend config.  Useful for testing branches
    that only exist on a fork.
    """
    provided = sum(1 for x in (pr, branch, commit) if x)
    if provided != 1:
        raise ValueError("Specify exactly one of --pr, --branch, or --commit")

    if pr:
        target = _resolve_pr(pr, backend)
        if repo_override:
            target = RunTarget(
                repo=repo_override, branch=target.branch,
                sut_commit=target.sut_commit, pr=target.pr,
                backend=target.backend, pr_repo=target.pr_repo,
            )
        return target
    if commit:
        target = _resolve_commit(commit, backend, branch)
        if repo_override:
            target = RunTarget(
                repo=repo_override, branch=target.branch,
                sut_commit=target.sut_commit, pr=target.pr,
                backend=target.backend, pr_repo=target.pr_repo,
            )
        return target
    assert branch is not None
    backend_cfg = BackendConfig(backend)
    return RunTarget(
        repo=repo_override or backend_cfg.repo,
        branch=branch,
        sut_commit="",
        pr=None,
        backend=backend,
        pr_repo=repo_override or backend_cfg.repo,
    )
