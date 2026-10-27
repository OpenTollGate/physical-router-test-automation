# pr-queue — reconcile an open PR queue against `main`, with evidence

`reconcile.py` answers one question per open PR, with per-commit evidence: is this
branch's content already in `main` (landed), does it conflict, or is it genuinely
open? It exists because a merge train that rebuilds branches under new PR numbers
makes ancestry checks (`git cherry main..head`) lie — content does not.

## What it does

Pass 1 (default), for every open PR:

- fetches `refs/pull/N/head` and compares it to the API head (head-drift check);
- `git merge-base` + `git diff` against `upstream/main`, then compares blobs
  per touched path (content-equality "landed" signal);
- `git merge-tree --write-tree` for a real conflict verdict;
- parses `supersedes #N` / `rebuild of #N` / `stacked on #N` / `closes #N`.

Pass 2 (`--cherry`), the one that matches a rebuild train:

- cherry-picks each of the PR's **own non-merge commits** onto `main` in a scratch
  worktree; a pick that stages nothing is `empty(landed)`, one that conflicts is
  `conflicted`, anything else is `live`;
- classifies `fully-landed` / `partially-landed` / `open`, and records every
  commit's subject + pick result for the close comment.

Outputs `queue-<timestamp>.md` + `queue-latest.json` in `--out` (JSON is the
machine-readable source for a review checklist).

## Requirements

- `git` >= 2.38 (`merge-tree --write-tree`)
- `gh` with read access to the target repo (public is enough)
- Python 3.10+ (stdlib only)

## Usage

```bash
# Needs a clone with an `upstream` remote pointing at the real repo.
python3 scripts/pr-queue/reconcile.py \
  --repo /path/to/tollgate-module-basic-go \
  --remote upstream --out ./pr-queue-reports --cherry

# Restrict to a few PRs (useful while re-checking a batch):
python3 scripts/pr-queue/reconcile.py --repo ... --out ... --cherry --only 1119 1123
```

## How to use the verdicts

- `fully-landed` → the close comment can cite the landing PR/main SHA and the
  per-commit pick results; close it.
- `partially-landed` → the `live` commits are the remaining work; look for a
  newer rebuild before asking for a rebase.
- `conflicted` + `open` → a genuine rebase queue item.
- `clean` → review queue.

Never close on a two-dot diff alone: a branch behind `main` shows `main`'s newer
work as "removals" in `git diff main..head`. The `--cherry` pass is the check that
answers "is anything here not in main yet".
