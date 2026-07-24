# A/B Test Prompt: PR #NNN

## Goal
Prove that PR #NNN fixes a specific bug by showing the binary from main
fails while the binary from the PR branch passes, using identical test scenarios.

## Steps

### 1. Identify the bug
Read the PR description and identify what bug it fixes. Design a scenario
that triggers the bug on the current main branch.

### 2. Build both binaries
```bash
cd /home/ubuntu/src/tollgate-module-basic-go
git fetch upstream

# Binary A (control) — upstream/main
git checkout upstream/main
cd src && GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags "-s -w" -o /tmp/tollgate-A .

# Binary B (treatment) — PR branch
git checkout <pr-branch-name>
cd src && GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -ldflags "-s -w" -o /tmp/tollgate-B .
```

### 3. Run standard test suite on each binary
For each binary (A then B):
1. Deploy to VM: `cat /tmp/tollgate-X | ssh root@10.99.99.1 "killall -9 tollgate-wrt; sleep 1; cat > /usr/bin/tollgate-wrt && chmod +x /usr/bin/tollgate-wrt"`
2. Start: `ssh root@10.99.99.1 '/etc/init.d/tollgate-wrt start; sleep 8'`
3. Run: `cd /home/ubuntu/src/physical-router-test-automation && python3 -m pytest tests/api/test_health.py tests/api/test_info_endpoint.py tests/api/test_mint_url_normalization.py tests/api/test_keyset_id_versions.py tests/api/test_degraded_mode.py tests/api/test_minimum_token.py -v --tb=short --client=container`
4. Capture pass/skip/fail counts

### 4. Run PR-specific scenario
Design and run a test that triggers the bug condition. Compare A vs B behavior.

### 5. Post results to GitHub
Post a comment on the PR with the A/B comparison table.

## Environment
- VM: 10.99.99.1 (root, OpenWrt 24.10.1, x86_64)
- Client: 10.99.99.100 (container)
- CDK V2 Mint: 10.99.99.2:8383 (cdk-mintd FakeWallet)
- Test framework: /home/ubuntu/src/physical-router-test-automation
- .env already configured with TOLLGATE_VIRTUAL_LAB=true

## Standard test suite
```
tests/api/test_health.py
tests/api/test_info_endpoint.py
tests/api/test_mint_url_normalization.py
tests/api/test_keyset_id_versions.py
tests/api/test_degraded_mode.py
tests/api/test_minimum_token.py
```
