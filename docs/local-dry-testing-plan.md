# Plan: Dry Local Testing for physical-router-test-automation

## Goal

Tests run identically on SHC, GCP, or local — no VM required for local.
Provider selected via env var. Pulumi handles VM lifecycle for cloud providers.
Local mode runs Go backend + mock mint as local processes.

---

## Architecture

### Current: All providers are SSH-to-VM

```
Test → VMProvider.ssh("pytest ...") → SSH → VM (SHC/GCP/QEMU/Physical)
```

### Proposed: TestTarget abstraction

```
Test → TestTarget.backend_url → HTTP → Backend (anywhere)
                                    ↑
                          Pulumi (cloud VM) | LocalProcess (dev machine)
```

Tests interact with **HTTP endpoints and browser UIs**, not VMs. The
TestTarget's job is to make `backend_url` and `frontend_url` available —
whether backed by a cloud VM or a local process.

### New `TestTarget` interface

```python
@dataclass
class TestTarget:
    backend_url: str        # http://localhost:2121 or http://<vm-ip>:2121
    frontend_url: str       # http://localhost:5173 or http://<vm-ip>/
    mint_url: str           # http://localhost:3338 (mock mint)
    mac_address: str        # device identifier for payment attribution

    def exec(self, cmd: str) -> str:
        """Run command on the backend host (SSH for VMs, subprocess for local)."""

    def logs(self) -> str:
        """Get backend stdout/stderr."""

    def restart_backend(self):
        """Restart the backend process."""
```

### Two implementations

| Implementation | How backend starts | `exec()` | `logs()` |
|---|---|---|---|
| **VMTestTarget** | Pulumi creates VM → SCP deploy → SSH start | SSH (via VMProvider) | SSH `journalctl` or `cat log` |
| **LocalProcessTarget** | Build Go binary → start with `TOLLGATE_TEST_CONFIG_DIR` | `subprocess.run()` | Process stdout/stderr |

Both produce the same `TestTarget` — tests don't know the difference.

### Pulumi's role

Pulumi remains the **infrastructure provisioning layer for cloud VMs** only:

- `TOLLGATE_VM_PROVIDER=shc` → Pulumi creates SHC VM → SSH bootstrap → VMTestTarget
- `TOLLGATE_VM_PROVIDER=gcloud` → GCP snapshot VM → SSH bootstrap → VMTestTarget
- `TOLLGATE_VM_PROVIDER=local` → No Pulumi. Direct process management → LocalProcessTarget

Pulumi does NOT manage local processes. That's overkill — a Python process
manager (start/stop/restart) is simpler and more reliable for dev machines.

---

## Deliverables

### 1. `lib/test_target.py` — TestTarget abstraction (NEW)

```python
@dataclass
class TestTarget:
    backend_url: str
    frontend_url: str
    mint_url: str
    mac_address: str
    _provider: VMProvider | None  # None for local

    def exec(self, cmd: str) -> str: ...
    def logs(self) -> str: ...
    def restart_backend(self) -> None: ...
```

### 2. `lib/local_process.py` — Local process orchestrator (NEW)

Manages lifecycle of:
- Go backend (`tollgate` binary with `TOLLGATE_TEST_CONFIG_DIR`)
- Mock Cashu mint (Python HTTP server)
- Vite dev server (optional, for browser tests)

```python
class LocalProcessTarget:
    def start(self) -> TestTarget:
        # 1. mkdir -p /tmp/tollgate-test-config
        # 2. Start mock mint on :3338
        # 3. Build + start Go backend with TOLLGATE_TEST_CONFIG_DIR
        # 4. Wait for backend health (GET / whoami)
        # 5. Return TestTarget(backend_url="http://localhost:2121", ...)

    def stop(self):
        # Kill all child processes
```

### 3. `lib/mock_mint.py` — Mock Cashu mint server (NEW or extend existing)

Check if prta's `lib/fake_mint.py` can be reused. If not, create a minimal
HTTP server implementing the critical Cashu endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /v1/info` | Return keysets, mint info |
| `GET /v1/keys` | Return active keyset public keys |
| `GET /v1/keysets` | Return all keysets |
| `POST /v1/swap` | Accept proofs, return blinded signatures |
| `POST /v1/checkstate` | Return proof states (UNSPENT/PENDING/SPENT) |
| `POST /v1/mint/quote/bolt11` | Create mint quote (for Lightning flow) |
| `POST /v1/mint/bolt11` | Mint tokens (return signatures) |

The mock mint needs:
- A fixed keyset with known private keys (for token generation)
- Ability to mint valid tokens on demand (for test fixtures)
- Ability to verify and swap tokens (for payment flow)
- Configurable error responses (429 rate limit, 500 errors)

### 4. `conftest.py` — pytest fixture that creates the right target (MODIFY)

```python
@pytest.fixture(scope="session")
def test_target():
    provider = os.environ.get("TOLLGATE_VM_PROVIDER", "shc")

    if provider == "local":
        target = LocalProcessTarget()
        target.start()
        yield target.test_target
        target.stop()
    else:
        vm_provider = get_provider(provider)
        vm = vm_provider.create_vm(name="test-runner")
        vm_provider.wait_for_ready(vm)
        # ... deploy backend, start it ...
        yield TestTarget(
            backend_url=f"http://{vm.ip}:2121",
            ...
        )
        vm_provider.destroy_vm(vm)
```

### 5. `tests/api/test_local_*.py` — Provider-agnostic API tests (NEW)

Tests that interact only with HTTP endpoints:

```python
def test_raw_token_payment(test_target):
    """POST a raw Cashu token to the backend → should return success."""
    token = generate_test_token(test_target.mint_url, amount=210)
    resp = requests.post(
        f"{test_target.backend_url}/",
        data=token,
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200

def test_v4_token_payment(test_target):
    """POST a V4 (cashuB) token → backend decodes natively."""
    token = generate_v4_test_token(test_target.mint_url, amount=210)
    resp = requests.post(
        f"{test_target.backend_url}/",
        data=token,
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200

def test_rate_limiting(test_target):
    """Hit POST / 11 times rapidly → 11th should be rate-limited."""
    for i in range(10):
        requests.post(f"{test_target.backend_url}/", data="invalid")
    resp = requests.post(f"{test_target.backend_url}/", data="invalid")
    assert resp.status_code == 429
```

### 6. `tests/captive-portal.local.spec.mjs` — Playwright tests (NEW)

Browser tests that run against Vite dev server + local backend:

```javascript
test.describe('Captive portal — local dry mode', () => {
  test.beforeAll(async () => {
    // Verify backend is running on localhost:2121
  });

  test('V4 token validation + submission', async ({ page }) => {
    await page.goto('http://localhost:5173');
    // ... type V4 token, verify amount, submit
  });

  test('prehydrate via URL param', async ({ page }) => {
    await page.goto('http://localhost:5173/?token=cashuA...');
    // ... verify auto-fill
  });

  test('error response handling', async ({ page }) => {
    // ... mock 402, 500, network error
  });
});
```

### 7. `scripts/local-test.sh` — One-command local test runner (NEW)

```bash
#!/bin/bash
# Start Go backend + mock mint + Vite, run tests, stop everything.
export TOLLGATE_VM_PROVIDER=local
export TOLLGATE_TEST_CONFIG_DIR=$(mktemp -d)

# Start mock mint
python3 lib/mock_mint.py --port 3338 &
MINT_PID=$!

# Build + start Go backend
(cd ../tollgate-module-basic-go && go build -o /tmp/tollgate src/main.go)
TOLLGATE_TEST_CONFIG_DIR=$TOLLGATE_TEST_CONFIG_DIR /tmp/tollgate &
BACKEND_PID=$!

# Wait for backend health
until curl -s http://localhost:2121/ > /dev/null 2>&1; do sleep 0.5; done

# Run tests
pytest tests/api/ -m "local or api" --tb=short
npx playwright test tests/captive-portal.local.spec.mjs

# Cleanup
kill $BACKEND_PID $MINT_PID
rm -rf $TOLLGATE_TEST_CONFIG_DIR
```

### 8. `pyproject.toml` / `requirements.txt` — Dependencies (MODIFY)

Add:
- `psutil` (process management for local mode)
- Ensure `requests` already present

### 9. `AGENTS.md` — Document the new test tier (MODIFY)

Add `local` as a first-class test tier alongside api/phone/web/unit.

---

## Migration Path

### Phase 1: Local API tests (this session)
- Create `lib/mock_mint.py`
- Create `lib/local_process.py`
- Create `tests/api/test_local_payment.py`
- Run: `TOLLGATE_VM_PROVIDER=local pytest tests/api/`
- Tests: raw token POST, V4 token POST, rate limiting, error codes

### Phase 2: Local browser tests (next session)
- Start Vite dev server alongside backend
- Create `tests/captive-portal.local.spec.mjs`
- Tests: token input, V4 validation, prehydrate, error handling, QR upload

### Phase 3: Unify with existing infrastructure (later)
- Refactor existing SHC/GCP tests to use TestTarget abstraction
- Migrate from direct SSH calls to TestTarget.exec()
- Existing tests keep working — just route through the new abstraction

### Phase 4: Pulumi integration (future)
- When Pulumi is ready for local QEMU, add `PulumiLocalProvider`
- For now, local process management is simpler and sufficient

---

## What We Can Test Locally (Layer 4)

| Scenario | Testable? | How |
|---|---|---|
| Raw token POST (V3) | ✅ | Generate valid token with mock mint, POST to backend |
| Raw token POST (V4) | ✅ | Generate V4 token, POST to backend (proven) |
| Rate limiting (10/min) | ✅ | Hit POST / 11 times, verify 429 |
| Error code mapping | ✅ | Submit spent token → verify error code |
| Token validation | ✅ | Submit malformed tokens → verify CU100-CU104 |
| Mint swap flow | ✅ | Submit token from untrusted mint → verify swap |
| 429 backoff | ✅ | Configure mock mint to return 429 → verify retry |
| Lightning payment | ❌ | Needs real Lightning node (skip or mock) |
| NDS captive portal redirect | ❌ | Needs real OpenWrt (skip) |
| WiFi client behavior | ❌ | Needs physical WiFi (skip) |
| Browser UI flows | ✅ | Vite + Playwright |
| Prehydrate | ✅ | Vite + Playwright |
| Error response UX | ✅ | Vite + Playwright with mock backend |

~80% of testable scenarios work locally. Only NDS/WiFi/Lightning need hardware.

---

## File Summary

| File | Action | Purpose |
|---|---|---|
| `lib/test_target.py` | NEW | TestTarget dataclass + factory |
| `lib/local_process.py` | NEW | LocalProcessTarget (start/stop Go backend + mock mint) |
| `lib/mock_mint.py` | NEW or EXTEND | Mock Cashu mint HTTP server |
| `conftest.py` | MODIFY | Add test_target fixture |
| `tests/api/test_local_payment.py` | NEW | Provider-agnostic payment API tests |
| `tests/captive-portal.local.spec.mjs` | NEW | Browser tests for local mode |
| `scripts/local-test.sh` | NEW | One-command test runner |
| `pyproject.toml` | MODIFY | Add psutil dependency |
| `AGENTS.md` | MODIFY | Document local test tier |
