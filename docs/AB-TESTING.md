# A/B Testing Methodology for TollGate PRs

## Overview

Every bugfix PR should include an A/B test proving the fix works: build the
binary from `main` (control) and from the PR branch (treatment), deploy each
to the virtual lab, run identical test scenarios, and document what fails on
main but passes with the PR.

This document defines the standard prompt, test scenarios, and report format.

## Virtual Lab Setup

```
VM (OpenWrt 24.10.1, x86_64):  10.99.99.1   (TollGate backend)
Client (Debian container):     10.99.99.100  (test client)
CDK V2 Mint (cdk-mintd):       10.99.99.2:8383 (FakeWallet, no Lightning needed)
```

Environment: `TOLLGATE_VIRTUAL_LAB=true`, `--client=container`

## A/B Test Procedure

### 1. Build both binaries

```bash
# Binary A (control) — from upstream/main
git checkout upstream/main
cd src && GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags "-s -w" -o /tmp/tollgate-A .

# Binary B (treatment) — from PR branch
git checkout <pr-branch>
cd src && GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags "-s -w" -o /tmp/tollgate-B .
```

### 2. Deploy + test each binary

For each binary:
1. Kill running service: `killall -9 tollgate-wrt`
2. Deploy: `cat /tmp/tollgate-X | ssh root@10.99.99.1 "cat > /usr/bin/tollgate-wrt && chmod +x /usr/bin/tollgate-wrt"`
3. Configure: Set mint URL, any PR-specific config
4. Start: `/etc/init.d/tollgate-wrt start; sleep 8`
5. Run test scenarios (see below)
6. Capture: process alive?, API status code, logs, pytest results

### 3. Standard test scenarios

Run these for every PR:

```bash
cd /home/ubuntu/src/physical-router-test-automation
python3 -m pytest tests/api/test_health.py tests/api/test_info_endpoint.py \
  tests/api/test_mint_url_normalization.py tests/api/test_keyset_id_versions.py \
  tests/api/test_degraded_mode.py tests/api/test_minimum_token.py \
  -v --tb=short --client=container
```

### 4. PR-specific scenario

Each PR should define a scenario that triggers the bug on main but passes
on the PR branch. Examples:

- **URL normalization fix**: Configure trailing-slash mint URL → main crash-loops, PR works
- **Data race fix**: Run payment flow under load with `-race` → main detects race, PR clean
- **io.ReadAll limit**: Send oversized response → main accepts, PR rejects
- **Degraded mode fallback**: Trigger wallet init failure → main crashes, PR falls back

### 5. Report format

```markdown
## A/B Test Results: PR #NNN

### Standard suite
| Binary | Tests passed | Tests skipped | Tests failed |
|--------|:-:|:-:|:-:|
| A (main) | NN | NN | 0 |
| B (PR)   | NN | NN | 0 |

### PR-specific scenario: <description>
| Check | Binary A (main) | Binary B (PR) |
|-------|:-:|:-:|
| <check 1> | ❌ <failure> | ✅ <success> |
| <check 2> | ❌ <failure> | ✅ <success> |

### Conclusion
<1-2 sentence summary of what this PR fixes and why it matters>
```

## Running A/B Tests

Use the prompt in `plans/ab-test-prompt.md` to run an A/B test on any PR.
The prompt is designed to be executed by an AI agent or a human operator.
