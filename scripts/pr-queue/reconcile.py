#!/usr/bin/env python3
"""pr-queue reconcile — classify every open PR of a repo against current main.

Answers one question per PR, with evidence: is this branch's content already
in main (landed), does it conflict, or is it genuinely open work?

Classification is content-based, not history-based: a PR is *landed* when
every path its diff touches has the same blob at the PR head as at main
(added paths must exist at main). The merge train rebuilds branches under new
PR numbers, so ancestry checks (`git cherry main..head`) lie; content does not.

Usage:
    reconcile.py --repo /path/to/tollgate-module-basic-go --remote upstream --out ./pr-queue-reports

Requires: git >= 2.38 (merge-tree --write-tree), gh (read access only).
Output: queue.md (human), queue.json (machine), per-PR evidence fields.
No secrets: PR metadata only, never credentials or lab identifiers.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def sh(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = sh(["git", "-C", str(repo), *args], check=False)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:400]}")
    return r.stdout.strip()


def gh_json(*args: str) -> object:
    r = sh(["gh", "api", *args], check=False)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)} failed: {r.stderr.strip()[:400]}")
    return json.loads(r.stdout)


REF_RE = re.compile(r"#(\d+)")


def body_refs(text: str, patterns: list[str]) -> list[int]:
    out: list[int] = []
    low = (text or "").lower()
    for pat in patterns:
        for m in re.finditer(pat + r"\s+\*{0,2}#?(\d+)", low):
            out.append(int(m.group(1)))
    return sorted(set(out))


def cherry_pass(repo: Path, remote: str, rows: list[dict], only: list[int] | None) -> None:
    """Containment test: a commit whose content is already in main picks empty.

    Uses a scratch worktree, `git cherry-pick -n <sha>` onto main, and
    `git diff --cached --quiet` to detect emptiness. Merge commits are skipped
    (they carry main in, never the PR's own work). A pick that conflicts means
    the commit's region moved on main — human judgement, not a verdict.
    """
    main_sha = git(repo, "rev-parse", f"{remote}/main")
    scratch = repo.parent / "scratch-cherry"
    if scratch.exists():
        sh(["rm", "-rf", str(scratch)])
    git(repo, "worktree", "add", "--detach", str(scratch), main_sha, "--quiet")
    try:
        for r in rows:
            n = r["pr"]
            if only and n not in only:
                continue
            if r.get("verdict") in ("unfetchable", "head-drift"):
                continue
            ref = f"refs/prs/{n}"
            commits = git(repo, "rev-list", "--no-merges", "--reverse",
                         f"{main_sha}..{ref}").split()
            landed, live, conflicted = [], [], []
            detail = []
            git(scratch, "reset", "--hard", main_sha, "--quiet")
            git(scratch, "clean", "-fdxq", check=False)
            for c in commits:
                subject = git(repo, "log", "-1", "--format=%s", c)
                pick = sh(["git", "-C", str(scratch), "cherry-pick", "-n", c], check=False)
                if pick.returncode != 0:
                    conflicted.append(c)
                    detail.append({"sha": c, "subject": subject, "pick": "conflicted"})
                    git(scratch, "cherry-pick", "--abort", check=False)
                    git(scratch, "reset", "--hard", main_sha, "--quiet")
                    continue
                empty = sh(["git", "-C", str(scratch), "diff", "--cached", "--quiet"],
                           check=False).returncode == 0
                if empty:
                    landed.append(c)
                    detail.append({"sha": c, "subject": subject, "pick": "empty(landed)"})
                else:
                    live.append(c)
                    detail.append({"sha": c, "subject": subject, "pick": "changes"})
                git(scratch, "reset", "--hard", main_sha, "--quiet")
            r["own_commits"] = len(commits)
            r["landed_commits"] = len(landed)
            r["live_commits"] = len(live)
            r["conflicted_commits"] = len(conflicted)
            r["commit_detail"] = detail
            if commits and not live and not conflicted:
                r["containment"] = "fully-landed"
            elif landed and not live:
                r["containment"] = "fully-landed(picks-conflicted)"
            elif not landed:
                r["containment"] = "open"
            else:
                r["containment"] = "partially-landed"
    finally:
        sh(["rm", "-rf", str(scratch)], check=False)
        git(repo, "worktree", "prune", check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--remote", default="upstream")
    ap.add_argument("--owner-repo", default="OpenTollGate/tollgate-module-basic-go")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", type=int, nargs="*", default=None,
                    help="restrict to these PR numbers")
    ap.add_argument("--cherry", action="store_true",
                    help="pass 2: per-commit containment test — cherry-pick -n each "
                         "of the PR's own commits onto main; empty pick == landed")
    args = ap.parse_args()

    repo, out = args.repo, args.out
    out.mkdir(parents=True, exist_ok=True)

    git(repo, "fetch", args.remote, "main", "--quiet")
    main_sha = git(repo, "rev-parse", f"{args.remote}/main")

    pulls = gh_json(f"repos/{args.owner_repo}/pulls?state=open&per_page=100&page=1")
    page = 2
    while isinstance(pulls, list) and len(pulls) == 100:
        more = gh_json(f"repos/{args.owner_repo}/pulls?state=open&per_page=100&page={page}")
        pulls.extend(more)
        page += 1

    rows = []
    for p in pulls:
        n = p["number"]
        if args.only and n not in args.only:
            continue
        head = p["head"]["sha"]
        ref = f"refs/prs/{n}"
        try:
            git(repo, "fetch", args.remote, f"refs/pull/{n}/head:{ref}", "--quiet", check=False)
            got = git(repo, "rev-parse", ref, check=False)
        except RuntimeError:
            rows.append({"pr": n, "title": p["title"], "draft": p["draft"],
                         "verdict": "unfetchable", "evidence": "head ref fetch failed"})
            continue
        if got != head:
            rows.append({"pr": n, "title": p["title"], "draft": p["draft"],
                         "verdict": "head-drift", "evidence": f"fetched {got} != api {head}"})
            continue

        base = git(repo, "merge-base", main_sha, ref)
        ahead = int(git(repo, "rev-list", "--count", f"{main_sha}..{ref}"))
        behind = int(git(repo, "rev-list", "--count", f"{ref}..{main_sha}"))
        paths = [l for l in git(repo, "diff", "--name-only", base, ref).splitlines() if l]

        equal, differing, missing = [], [], []
        for path in paths:
            try:
                head_blob = git(repo, "rev-parse", f"{ref}:{path}", check=False)
            except RuntimeError:
                head_blob = "-"  # deleted on the branch
            try:
                main_blob = git(repo, "rev-parse", f"{main_sha}:{path}", check=False)
            except RuntimeError:
                main_blob = "-"  # absent on main
            if head_blob == main_blob:
                equal.append(path)
            else:
                differing.append(path)
                if main_blob == "-":
                    missing.append(path)

        # merge-tree conflict probe (exit 1 == conflicts; --name-only lists them)
        mt = sh(["git", "-C", str(repo), "merge-tree", "--write-tree", "--name-only",
                 main_sha, ref], check=False)
        conflicts = mt.returncode != 0

        if not paths:
            verdict = "landed"
            ev = "no content delta vs merge-base"
        elif paths and not differing:
            verdict = "landed"
            ev = f"all {len(paths)} touched paths identical at main ({', '.join(paths[:5])}…)"
        elif conflicts:
            verdict = "conflicted"
            ev = f"{len(differing)}/{len(paths)} paths differ, merge-tree reports conflicts"
        else:
            verdict = "clean"
            ev = f"{len(differing)}/{len(paths)} paths differ, merge applies cleanly"

        sup = body_refs(p.get("body") or "", ["supersedes", "rebuild of", "replaces"])
        stacked = body_refs(p.get("body") or "", ["stacked on", "stacks on", "blocked by"])
        closes = body_refs(p.get("body") or "", ["closes", "fixes"])

        rows.append({
            "pr": n, "title": p["title"], "draft": p["draft"], "verdict": verdict,
            "evidence": ev, "head": head, "merge_base": base, "ahead": ahead,
            "behind": behind, "paths": len(paths), "differing": differing[:10],
            "missing_on_main": missing[:10], "conflicts": conflicts,
            "supersedes": sup, "stacked_on": stacked, "closes": closes,
            "author": p["user"]["login"], "updated_at": p["updated_at"],
        })

    # cross-PR supersession: any open PR whose body supersedes another open PR
    if args.cherry:
        cherry_pass(repo, args.remote, rows, args.only)
    open_by_n = {r["pr"] for r in rows}
    superseded_by: dict[int, list[int]] = {}
    for r in rows:
        for s in r.get("supersedes", []):
            if s in open_by_n and s != r["pr"]:
                superseded_by.setdefault(s, []).append(r["pr"])
    for r in rows:
        if r["pr"] in superseded_by:
            r["superseded_by_open"] = superseded_by[r["pr"]]
            if r["verdict"] == "landed":
                r["verdict"] = "landed+superseded"

    stamp = time.strftime("%Y-%m-%dT%H%M%S")
    (out / f"queue-{stamp}.json").write_text(json.dumps(rows, indent=1))
    (out / "queue-latest.json").write_text(json.dumps(rows, indent=1))

    order = {"landed": 0, "landed+superseded": 0, "superseded": 1, "head-drift": 2,
             "unfetchable": 2, "conflicted": 3, "clean": 4, "draft": 5}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), -r["pr"]))

    if args.cherry:
        lines = ["| PR | verdict | containment | own/landed/live/conf | paths | evidence |",
                 "|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| {r['pr']} | {r['verdict']}{' D' if r['draft'] else ''} | "
                f"{r.get('containment','—')} | "
                f"{r.get('own_commits','—')}/{r.get('landed_commits','—')}/"
                f"{r.get('live_commits','—')}/{r.get('conflicted_commits','—')} | "
                f"{r.get('paths','?')} | {r['evidence']}"
                + (f" — superseded by {r['superseded_by_open']}" if r.get("superseded_by_open") else "")
                + " |")
    else:
        lines = ["| PR | verdict | draft | ahead/behind | paths | evidence |",
                 "|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(
                f"| {r['pr']} | {r['verdict']} | {'D' if r['draft'] else ''} | "
                f"{r.get('ahead','?')}/{r.get('behind','?')} | {r.get('paths','?')} | "
                f"{r['evidence']}{' — superseded by ' + str(r['superseded_by_open']) if r.get('superseded_by_open') else ''} |")
    md = out / f"queue-{stamp}.md"
    md.write_text("\n".join(lines) + "\n")
    (out / "queue-latest.md").write_text("\n".join(lines) + "\n")

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print(json.dumps(counts, indent=1))
    print("main:", main_sha[:12], "prs:", len(rows))
    print("wrote", md)
    return 0


if __name__ == "__main__":
    sys.exit(main())