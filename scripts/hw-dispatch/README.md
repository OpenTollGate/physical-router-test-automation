# hw-dispatch — the fallback bench dispatch lane (PRTA-REVIVE)

Two ways exist to get a hardware smoke run from a CI-like trigger, in
priority order:

1. **Primary — fork-scoped self-hosted runner.** A GitHub Actions runner
   registered against the FORK repo (`felixfelix-bot/physical-router-
   test-automation`, never the upstream org), labels
   `self-hosted,tollgate-router`, living on the bench host. Trigger:
   `gh workflow run hw-smoke.yml -f lane=smoke` (maintainer-only dispatch;
   the workflow is unreachable from PRs — enforced by
   `scripts/ci/check-workflow-hw-isolation.sh`).
2. **Fallback — this queue + cron.** When the runner cannot be registered,
   a bench-side cron pulls a local queue and posts verdicts back to the
   commit via `gh api repos/<repo>/statuses/<sha>`.

Both paths run the SAME payload (the zero-secret
`tests/browser/admin-ui-walkthrough.spec.mjs`) under the SAME machine-global
bench lease (`scripts/hw-bench-lease`), with the idle gate armed. Never
install both consumers at once — one dispatch consumer at a time.

## Registering the runner (primary path)

On the bench host (the machine wired to the router's LAN):

    mkdir -p ~/actions-runner-prta && cd ~/actions-runner-prta
    curl -L -o runner.tar.gz https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64-<ver>.tar.gz
    tar xzf runner.tar.gz
    ./config.sh --url https://github.com/felixfelix-bot/physical-router-test-automation \
        --token "$(gh api -X POST repos/felixfelix-bot/physical-router-test-automation/actions/runners/registration-token --jq .token)" \
        --name prta-bench-1 --labels tollgate-router --unattended
    ./run.sh &   # or a user-level systemd unit

Notes:
- Registration is repo-scoped to the fork by construction (`--url`); the
  runner never accepts jobs from the upstream org or PRs.
- Keep the label exactly `tollgate-router` (+ the implicit `self-hosted`):
  the isolation guard derives the denied label set from hw-smoke.yml.
- `hw-smoke.yml`'s `concurrency: hw-bench` serializes GitHub-side lanes; the
  bench lease serializes against every OTHER agent window (make targets,
  pytest sessions, cron).

## The queue (fallback path)

    scripts/hw-dispatch/enqueue.sh <full-sha> [purpose] [host]   # producer
    scripts/hw-dispatch/poll-queue.sh [--dry-run] [--all]        # consumer

Cron line (install only while the runner path is down):

    */10 * * * * ~/physical-router-test-automation/scripts/hw-dispatch/poll-queue.sh >> ~/.hermes/state/hw-queue/poll.log 2>&1

Behavioral contract:
- The poll loop is `flock`-guarded (`.poll.lock`); the run itself takes the
  bench lease with `--check-idle`, under `nice -n 10`.
- Lease-busy (lease rc 2) leaves the job queued — it is not a test failure.
- Idle-refused (rc 4) and unreachable (rc 5) post `failure` statuses with a
  precise description, so a red status never masquerades as a code verdict.
- Job files land in `$SPOOL/done/` after their verdict is posted.

## The one lease

Everything above funnels through `scripts/hw-bench-lease` (see its header):
`lib.bench_lock.BenchLock` — flock on
`~/.hermes/state/bench-mt3000.lock`, holder line, stale reclaim, and the
idle gate wired to `lib.session_verify.check_balance_api`. If you are
writing a new bench-driving surface, take that lease; do not add another
lock file.
