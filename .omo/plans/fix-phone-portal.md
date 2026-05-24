# Fix Phone Portal Detection + Token Typing Flow

## TL;DR

> **Quick Summary**: Fix the phone test timeout caused by broken captive portal detection, and redesign the payment flow to type cashu tokens into the portal UI (like a real user) instead of using URL parameters.
> 
> **Deliverables**:
> - Fixed `_open_portal_on_phone()` that opens portal directly in browser
> - New `type_token_in_portal()` flow that finds input, types token, submits
> - Updated `connected_wifi` fixture using the new flow
> - Redesigned `test_url_param.py` → `test_token_input.py` with real user flow
> - New `test_captive_portal_auto.py` for Android captive portal auto-detection
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4

---

## Context

### Original Request
Phone tests are timing out. The user wants to understand why and fix it. The user also wants to type cashu tokens into the portal instead of using URL parameters, to simulate a more realistic user flow.

### Root Cause Analysis

**Why phone tests timeout:**

1. **WiFi connects fine** — phone gets IP 192.168.1.149 on TollGate-6EAB
2. **NDS intercepts HTTP** — phone added as Preauthenticated client
3. **Android marks WiFi as CAPTIVE_PORTAL** — `isUsable: false`, mobile data stays primary route
4. **`_open_portal_on_phone()` looks for "Sign in" in WiFi Settings UI** — but Android puts the captive portal notification in the **notification shade**, NOT WiFi Settings
5. **30s timeout expires** → RERUN → another 30s → suite exceeds 600s

**Why it "worked before":**
Previous phone tests (multihop 4/5) used `--quick-phone` mode or `pay_direct()` backend API — these **never call `_open_portal_on_phone()`**. This is the first time testing the full captive portal flow on router-b.

**Why NDS returned 503 (red herring):**
The 503 was from curling the portal FROM THE ROUTER ITSELF. NDS source code (`http_microhttpd.c`) returns 503 when `get_client_mac()` fails (no ARP entry for the router's own IP). From the actual phone, NDS works correctly — it adds the phone as client and serves the portal.

**Key network detail:**
Android keeps mobile data as default route, but the phone HAS a route to `192.168.1.0/24` via `wlan0`. So opening `http://192.168.1.1:2050/` in the browser works even when mobile data is primary — it's a subnet route, not a default gateway route.

### Interview Summary
**Key Discussions**:
- User wants token typing (real user flow) instead of URL parameter
- No phones or routers available right now — plan must be ready for execution when hardware is available
- User confirmed: "yes, type in the cashu token instead of using the token= path and simulate more true to what a user would do"

---

## Work Objectives

### Core Objective
Fix the phone portal detection flow and implement a realistic token-typing payment flow that works end-to-end through the captive portal on a physical Android phone.

### Concrete Deliverables
- `lib/clients/wifi.py` — Fixed `_open_portal_on_phone()` + new `_type_token_in_portal()`
- `tests/phone/test_token_input.py` — New test: real user flow (connect → portal → type token → auth)
- `tests/phone/test_captive_portal_auto.py` — Test Android auto-detects captive portal and shows sign-in
- `tests/phone/test_url_param.py` — Fix to use direct browser open instead of captive portal notification

### Definition of Done
- [ ] `test_token_input.py` passes: phone connects to WiFi, portal opens in browser, token typed, session active
- [ ] `test_captive_portal_auto.py` passes: Android detects captive portal within 30s of WiFi connect
- [ ] `test_url_param.py` passes without timeout
- [ ] Existing tests (`test_paste.py`, `test_auto.py`, etc.) still pass unchanged
- [ ] No test takes longer than 120s

### Must Have
- Portal opens directly in Chrome/browser via `am start -a android.intent.action.VIEW`
- Token typing via ADB `input text` into the portal's input field
- Works even when Android has mobile data as primary route (uses subnet route to 192.168.1.0/24)
- Backwards compatible with existing `pay_direct()` tests

### Must NOT Have (Guardrails)
- Do NOT modify `pay_direct()` or backend payment tests — they work fine
- Do NOT use `cmd wifi connect-network` — requires root, not available on non-rooted phones
- Do NOT rely on Android captive portal notification being in WiFi Settings — it's in the notification shade
- Do NOT disable mobile data — tests should work with mobile data active (realistic)
- Do NOT assume specific browser package name — use `am start -a VIEW` intent
- Do NOT increase test timeout beyond 120s — fix the root cause, not the timeout

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (pytest framework)
- **Automated tests**: Tests-after (we're fixing test infrastructure itself)
- **Framework**: pytest

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Phone tests**: Use ADB (tap, type, ui_xml, screenshot) to verify portal rendering and token input
- **WiFi connection**: Use `dumpsys wifi` to verify SSID connection
- **Router state**: Use SSH to verify NDS client state, session state

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - core fixes):
├── Task 1: Fix _open_portal_on_phone() in wifi.py [quick]
├── Task 2: Add _type_token_in_portal() to wifi.py [deep]
└── Task 3: Add _open_portal_in_browser() helper to adb.py [quick]

Wave 2 (After Wave 1 - test rewrites + new tests):
├── Task 4: Rewrite test_url_param.py to use direct browser open [quick]
├── Task 5: Create test_token_input.py (real user flow) [unspecified-high]
└── Task 6: Create test_captive_portal_auto.py [quick]

Wave FINAL (After ALL tasks — 4 parallel reviews):
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Integration test review (deep)
└── Task F4: Scope fidelity check (deep)
-> Present results -> Get explicit user okay
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 1 | - | 4, 5 |
| 2 | 1, 3 | 5 |
| 3 | - | 2, 4, 5 |
| 4 | 1, 3 | F1-F4 |
| 5 | 1, 2, 3 | F1-F4 |
| 6 | 1 | F1-F4 |

### Agent Dispatch Summary

- **Wave 1**: **3** — T1 → `quick`, T2 → `deep`, T3 → `quick`
- **Wave 2**: **3** — T4 → `quick`, T5 → `unspecified-high`, T6 → `quick`
- **FINAL**: **4** — F1 → `oracle`, F2 → `unspecified-high`, F3 → `deep`, F4 → `deep`

---

## TODOs

- [x] 1. Fix `_open_portal_on_phone()` in wifi.py

  **What to do**:
  - Rewrite `_open_portal_on_phone()` to open the portal URL directly in the phone's browser via `adb.start_activity(action="android.intent.action.VIEW", data_uri="http://192.168.1.1:2050/")`
  - Use `self.router.host` or the LAN gateway IP (not hardcoded) for the portal URL
  - After opening the URL, wait for `data-sm=` attribute to appear in the UI XML (portal React app rendering)
  - Remove the `_tap_sign_in()` and `_tap_ssid_captive_entry()` code paths — these search WiFi Settings UI which is wrong
  - Keep the state_pattern parameter for flexibility, default to `data-sm="[^"]*"|Tollgate Captive Portal`
  - Add a helper `_get_portal_host()` that returns the LAN IP of the router (the gateway the phone got via DHCP, typically 192.168.1.1)
  - After opening browser, wait up to 30s polling `ui_xml()` every 3s for the portal state machine to render
  - Close the browser app (Chrome/Samsung Internet) when done via `adb.force_stop()` — prevents stale tabs

  **Must NOT do**:
  - Do NOT look for "Sign in to network" in WiFi Settings
  - Do NOT use notification shade swipe to find captive portal notification
  - Do NOT disable mobile data
  - Do NOT hardcode 192.168.1.1 — derive from router config or DHCP gateway

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Focused fix to one method in one file, clear requirements
  - **Skills**: `[]`
    - No special skills needed for Python test framework edits

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 2, 3)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 4, 5, 6
  - **Blocked By**: None

  **References**:
  - `lib/clients/wifi.py:271-294` — Current `_open_portal_on_phone()` that's broken (searches WiFi Settings for "Sign in")
  - `lib/clients/adb.py:140-148` — `start_activity()` method for opening URLs in browser
  - `lib/clients/adb.py:71-77` — `ui_xml()` for reading phone screen state
  - `lib/clients/wifi.py:267-269` — `reconnect()` calls `_open_portal_on_phone()` with state pattern

  **Acceptance Criteria**:
  - [ ] `_open_portal_on_phone()` calls `start_activity(action="android.intent.action.VIEW")` with portal URL
  - [ ] No references to `_tap_sign_in()` or `_tap_ssid_captive_entry()` in `_open_portal_on_phone()`
  - [ ] Waits for `data-sm=` in UI XML, not for "Sign in" text
  - [ ] Uses router LAN IP (not hardcoded) for portal URL

  **QA Scenarios:**
  ```
  Scenario: Portal opens in browser and renders state machine
    Tool: Bash (ADB commands)
    Preconditions: Phone connected to TollGate WiFi, router accessible at 192.168.1.1
    Steps:
      1. Run: python -c "from lib.clients.wifi import WiFi; print('import OK')"
      2. Grep wifi.py for "start_activity" — confirm it's called in _open_portal_on_phone
      3. Grep wifi.py for "_tap_sign_in" — confirm it's NOT called in _open_portal_on_phone
      4. Grep wifi.py for hardcoded "192.168.1.1" — confirm none exist
    Expected Result: Import succeeds, start_activity found, _tap_sign_in not found, no hardcoded IPs
    Evidence: .sisyphus/evidence/task-1-portal-fix.txt

  Scenario: No notification shade dependency
    Tool: Bash (grep)
    Steps:
      1. grep -n "notification\|shade\|swipe.*1600.*800" lib/clients/wifi.py
      2. If found in _open_portal_on_phone, FAIL
    Expected Result: No notification shade logic in portal opening code
    Evidence: .sisyphus/evidence/task-1-no-notification.txt
  ```

  **Commit**: YES (groups with Task 2, 3)
  - Message: `fix(phone): open portal in browser instead of searching WiFi settings`
  - Files: `lib/clients/wifi.py`
  - Pre-commit: `python -m py_compile lib/clients/wifi.py`

- [x] 2. Add `_type_token_in_portal()` to wifi.py

  **What to do**:
  - Add new method `_type_token_in_portal(self, token: str, timeout: int = 60) -> bool` to WiFi class
  - Steps:
    1. Wait for `data-sm="portal_ready"` or `data-sm="token_typing"` in UI XML (portal loaded)
    2. Find the token input field in the portal: look for `<input` or `EditText` node in UI XML that's within the WebView
    3. Tap the input field (extract bounds from UI XML)
    4. Type the token via `self.adb.input_text(token)` — ADB `input text` sends keystrokes
    5. Wait 1s for text to appear
    6. Find and tap the submit/button element in the portal (look for clickable node near the input)
    7. Wait for auth confirmation: `data-sm="authed"` or `data-sm="countdown"` in UI XML
    8. Return True if authed within timeout, False otherwise
  - Handle the case where `input_text` doesn't support special characters (cashu tokens have `+`, `=` etc.) — use `adb shell am broadcast` with clipboard intent as fallback:
    ```python
    # ADB input text doesn't handle spaces and some special chars well
    # Use clipboard broadcast as fallback
    self.adb.shell(f"am broadcast -a clipper.set -e text '{token}'")
    # Then long-press input field and paste
    ```
  - Actually, cashu tokens are URL-safe base64 (no spaces), so `input text` should work. But add a fallback for `%` and other problematic chars using the clipboard approach.
  - Add logging at each step for debuggability

  **Must NOT do**:
  - Do NOT use URL parameters (`?token=`) — we're typing into the UI
  - Do NOT assume specific portal HTML structure — use generic UI XML node search
  - Do NOT skip waiting for `portal_ready` state

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Complex multi-step UI interaction logic with edge cases (special chars, timing, WebView quirks)
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1, 3)
  - **Parallel Group**: Wave 1
  - **Blocks**: Task 5
  - **Blocked By**: None (reads wifi.py but only adds a new method)

  **References**:
  - `lib/clients/adb.py:91-95` — `input_text()` method for typing
  - `lib/clients/adb.py:79-83` — `tap()` for clicking
  - `lib/clients/adb.py:71-77` — `ui_xml()` for reading screen state
  - `lib/clients/wifi.py:45-60` — `_tap_ssid()` shows pattern for finding nodes by text and extracting bounds
  - `lib/clients/wifi.py:267-269` — `reconnect()` shows current state pattern usage
  - `lib/constants.py` — `TOKEN_DEFAULT` constant

  **Acceptance Criteria**:
  - [ ] `_type_token_in_portal()` method exists on WiFi class
  - [ ] Waits for `portal_ready` or `token_typing` state before typing
  - [ ] Uses `ui_xml()` to find input field and submit button
  - [ ] Handles special characters in cashu tokens (has clipboard fallback or escaping)
  - [ ] Waits for `authed` state after submission
  - [ ] Returns bool with clear success/failure

  **QA Scenarios:**
  ```
  Scenario: Method handles token with special characters
    Tool: Bash (python import test)
    Steps:
      1. python -c "from lib.clients.wifi import WiFi; w = WiFi.__new__(WiFi); print(hasattr(w, '_type_token_in_portal'))"
      2. grep -n "clipboard\|clipper\|input_text" lib/clients/wifi.py — confirm both approaches exist
    Expected Result: Method exists and has fallback for special chars
    Evidence: .sisyphus/evidence/task-2-token-typing.txt

  Scenario: Method waits for correct portal states
    Tool: Bash (grep)
    Steps:
      1. grep -n "portal_ready\|token_typing\|authed\|countdown" lib/clients/wifi.py
      2. Confirm _type_token_in_portal checks portal_ready before typing
      3. Confirm it checks authed/countdown after submission
    Expected Result: Both pre-type and post-submit state checks exist
    Evidence: .sisyphus/evidence/task-2-state-checks.txt
  ```

  **Commit**: YES (groups with Task 1, 3)
  - Message: `fix(phone): add token typing flow for captive portal`
  - Files: `lib/clients/wifi.py`
  - Pre-commit: `python -m py_compile lib/clients/wifi.py`

- [x] 3. Add `_open_portal_in_browser()` helper to adb.py

  **What to do**:
  - Add method `open_url(self, url: str)` to ADBDevice class
  - Implementation: `self.shell(f"am start -a android.intent.action.VIEW -d '{url}'")`
  - Also add `open_portal(self, host: str, port: int = 2050)` convenience method:
    - Calls `open_url(f"http://{host}:{port}/")`
    - Waits 3s for browser to load
    - Returns True
  - Add `force_stop_browser(self)` method:
    - Tries Samsung Internet first: `self.shell("am force-stop com.sec.android.app.sbrowser")`
    - Then Chrome: `self.shell("am force-stop com.android.chrome")`
    - This cleans up stale browser tabs between tests

  **Must NOT do**:
  - Do NOT hardcode browser package names in `open_url()` — use intent resolution
  - Do NOT add unnecessary helper methods beyond these three

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Three simple methods, clear requirements
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 1, 2)
  - **Parallel Group**: Wave 1
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: None

  **References**:
  - `lib/clients/adb.py:140-148` — Existing `start_activity()` method (refactor target)
  - `lib/clients/adb.py:137-138` — Existing `force_stop()` method

  **Acceptance Criteria**:
  - [ ] `open_url(url)` method exists on ADBDevice
  - [ ] `open_portal(host, port)` convenience method exists
  - [ ] `force_stop_browser()` method exists
  - [ ] `open_url` uses intent resolution, not hardcoded package

  **QA Scenarios:**
  ```
  Scenario: Methods exist and are correct
    Tool: Bash (python)
    Steps:
      1. python -c "from lib.clients.adb import ADBDevice; d = ADBDevice(); print(hasattr(d, 'open_url'), hasattr(d, 'open_portal'), hasattr(d, 'force_stop_browser'))"
      2. grep -n "am start.*VIEW" lib/clients/adb.py — confirm intent-based opening
      3. grep -n "force-stop.*chrome\|force-stop.*sbrowser" lib/clients/adb.py — confirm browser cleanup
    Expected Result: All three methods exist, uses intent, cleans up both browsers
    Evidence: .sisyphus/evidence/task-3-adb-helpers.txt
  ```

  **Commit**: YES (groups with Task 1, 2)
  - Message: `feat(adb): add URL opening and browser cleanup helpers`
  - Files: `lib/clients/adb.py`
  - Pre-commit: `python -m py_compile lib/clients/adb.py`

- [x] 4. Rewrite `test_url_param.py` to use direct browser open

  **What to do**:
  - Rewrite `tests/phone/test_url_param.py`:
    - Remove the old flow that relied on captive portal notification
    - New flow:
      1. `connected_wifi` fixture connects WiFi (already works)
      2. Mint a cashu token
      3. Open portal URL directly: `adb.open_portal(host=router.lan_ip)`
      4. Wait for portal to render (`data-sm="portal_ready"` in UI XML)
      5. Encode token in URL param: open `http://{host}:2050/?token={encoded_token}`
      6. Wait for auth: `router.wait_for_auth(timeout=60)`
      7. Assert session active
    - Keep `pytestmark = [pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120), pytest.mark.extended]`
    - Keep the `screenshot_portal` calls for evidence
  - The key change: instead of relying on `_open_portal_on_phone()` in the `connected_wifi` fixture (which times out), the test opens the portal URL directly with the token already in the query string
  - Add `skip_portal=True` to `connected_wifi` fixture call to avoid the broken portal detection in the fixture — the test handles portal opening itself

  **Must NOT do**:
  - Do NOT increase timeout beyond 120s
  - Do NOT remove the test entirely — it tests a valid flow (URL param delivery)
  - Do NOT modify other test files

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single test file rewrite, clear pattern to follow
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 5, 6)
  - **Parallel Group**: Wave 2
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 1, 3

  **References**:
  - `tests/phone/test_url_param.py` — Current test (25 lines, simple)
  - `tests/phone/test_paste.py` — Pattern for payment + assert_session_active
  - `tests/phone/test_auto.py` — Pattern for direct payment flow
  - `lib/clients/adb.py:140-148` — `start_activity()` for opening URLs
  - `lib/constants.py` — `TOKEN_DEFAULT` constant

  **Acceptance Criteria**:
  - [ ] Test uses `adb.open_portal()` or `adb.open_url()` to open portal directly
  - [ ] Test passes `skip_portal=True` or equivalent to avoid broken fixture portal detection
  - [ ] No reference to captive portal notification or WiFi Settings in the test
  - [ ] timeout remains 120s

  **QA Scenarios:**
  ```
  Scenario: Test file compiles and has correct markers
    Tool: Bash (python)
    Steps:
      1. python -m py_compile tests/phone/test_url_param.py
      2. grep "pytest.mark.phone" tests/phone/test_url_param.py
      3. grep "pytest.mark.timeout(120)" tests/phone/test_url_param.py
    Expected Result: Compiles clean, has phone and timeout markers
    Evidence: .sisyphus/evidence/task-4-url-param.txt

  Scenario: No notification/sign-in dependency
    Tool: Bash (grep)
    Steps:
      1. grep -n "sign_in\|notification\|Sign in\|captive.*entry" tests/phone/test_url_param.py
    Expected Result: No matches (test doesn't rely on captive portal notification)
    Evidence: .sisyphus/evidence/task-4-no-notification.txt
  ```

  **Commit**: YES (groups with Task 5, 6)
  - Message: `test(phone): rewrite test_url_param with direct browser open`
  - Files: `tests/phone/test_url_param.py`
  - Pre-commit: `python -m py_compile tests/phone/test_url_param.py`

- [x] 5. Create `test_token_input.py` — real user flow

  **What to do**:
  - Create `tests/phone/test_token_input.py` — the primary realistic user flow test:
    ```
    1. Connect to TollGate WiFi (connected_wifi fixture with skip_portal=True)
    2. Open portal in browser: adb.open_portal(host=router.lan_ip)
    3. Wait for portal_ready state in UI XML
    4. Mint a cashu token
    5. Type the token into the portal's input field using wifi._type_token_in_portal()
    6. Wait for auth: router.wait_for_auth(timeout=60)
    7. Assert session active
    8. Screenshot the authenticated state
    ```
  - Markers: `pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120), pytest.mark.critical`
  - This is the "golden path" test — if this passes, the full user flow works
  - Add a second test case `test_token_input_invalid_token` that types garbage and verifies the portal shows an error (doesn't auth)
  - Use `screenshot_portal` for evidence capture at key points (before typing, after auth, on error)

  **Must NOT do**:
  - Do NOT use `pay_direct()` — this test goes through the portal UI
  - Do NOT use URL parameters — this test types the token
  - Do NOT add more than 2 test cases (keep it focused)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: New test file with complex UI interaction, needs careful design
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 4, 6)
  - **Parallel Group**: Wave 2
  - **Blocks**: F1-F4
  - **Blocked By**: Tasks 1, 2, 3

  **References**:
  - `tests/phone/test_paste.py` — Existing pattern for payment + assert_session_active (but uses pay_direct, we use portal UI)
  - `tests/phone/test_auto.py` — Pattern for test structure with screenshot_portal
  - `lib/clients/wifi.py` — New `_type_token_in_portal()` method (from Task 2)
  - `lib/clients/adb.py` — New `open_portal()` method (from Task 3)
  - `lib/constants.py` — `TOKEN_DEFAULT` constant
  - `tests/phone/test_degraded_mode.py:132` — Pattern for reading `data-sm` state from UI XML

  **Acceptance Criteria**:
  - [ ] Test file exists at `tests/phone/test_token_input.py`
  - [ ] Has `test_token_input_happy_path` test case
  - [ ] Has `test_token_input_invalid_token` test case
  - [ ] Uses `_type_token_in_portal()` NOT `pay_direct()` or URL params
  - [ ] Marked as `critical` tier
  - [ ] Compiles without errors

  **QA Scenarios:**
  ```
  Scenario: Test file structure correct
    Tool: Bash (python + grep)
    Steps:
      1. python -m py_compile tests/phone/test_token_input.py
      2. grep "def test_token_input" tests/phone/test_token_input.py — count test functions
      3. grep "pytest.mark.critical" tests/phone/test_token_input.py
      4. grep "_type_token_in_portal\|pay_direct" tests/phone/test_token_input.py
    Expected Result: Compiles, has 2 test functions, marked critical, uses _type_token_in_portal not pay_direct
    Evidence: .sisyphus/evidence/task-5-token-input.txt

  Scenario: No URL param usage
    Tool: Bash (grep)
    Steps:
      1. grep -n "token=" tests/phone/test_token_input.py
    Expected Result: No URL parameter token= usage (token typed via UI)
    Evidence: .sisyphus/evidence/task-5-no-url-param.txt
  ```

  **Commit**: YES (groups with Task 4, 6)
  - Message: `test(phone): add real user flow test with token typing`
  - Files: `tests/phone/test_token_input.py`
  - Pre-commit: `python -m py_compile tests/phone/test_token_input.py`

- [x] 6. Create `test_captive_portal_auto.py` — Android auto-detection test

  **What to do**:
  - Create `tests/phone/test_captive_portal_auto.py`:
    - Test that Android automatically detects the captive portal after connecting to TollGate WiFi
    - Flow:
      1. Connect to TollGate WiFi via `connected_wifi` fixture (with `skip_portal=True`)
      2. Wait up to 30s for Android to show "Sign in to network" notification
      3. Open notification shade via `adb.shell("input swipe 540 0 540 500 300")`
      4. Look for "Sign in" or "Login" or "TollGate" in notification XML
      5. Tap the notification
      6. Wait for portal to render in the captive WebView (data-sm= check)
    - Markers: `pytest.mark.phone, pytest.mark.slow, pytest.mark.timeout(120), pytest.mark.extended`
    - This tests the FULL Android captive portal auto-detection flow
    - This is separate from `test_token_input` because captive portal detection can be flaky (depends on Android's connectivity check timing)

  **Must NOT do**:
  - Do NOT type any token in this test — it only verifies portal auto-opens
  - Do NOT make this a `critical` test — captive portal detection timing is unreliable
  - Do NOT increase timeout beyond 120s

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple test file, clear structure
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Tasks 4, 5)
  - **Parallel Group**: Wave 2
  - **Blocks**: F1-F4
  - **Blocked By**: Task 1

  **References**:
  - `tests/phone/test_auto.py` — Pattern for test structure
  - `lib/clients/adb.py:103-108` — `swipe()` for opening notification shade
  - `lib/clients/adb.py:71-77` — `ui_xml()` for reading notifications

  **Acceptance Criteria**:
  - [ ] Test file exists at `tests/phone/test_captive_portal_auto.py`
  - [ ] Has `test_android_detects_captive_portal` test case
  - [ ] Opens notification shade to find "Sign in" notification
  - [ ] Does NOT type any token
  - [ ] Marked as `extended` (not critical — timing-dependent)

  **QA Scenarios:**
  ```
  Scenario: Test file structure correct
    Tool: Bash (python + grep)
    Steps:
      1. python -m py_compile tests/phone/test_captive_portal_auto.py
      2. grep "def test_" tests/phone/test_captive_portal_auto.py — count functions
      3. grep "pytest.mark.extended" tests/phone/test_captive_portal_auto.py
      4. grep -c "input_text\|_type_token" tests/phone/test_captive_portal_auto.py
    Expected Result: Compiles, has 1 test function, marked extended, 0 token typing calls
    Evidence: .sisyphus/evidence/task-6-captive-auto.txt
  ```

  **Commit**: YES (groups with Task 4, 5)
  - Message: `test(phone): add Android captive portal auto-detection test`
  - Files: `tests/phone/test_captive_portal_auto.py`
  - Pre-commit: `python -m py_compile tests/phone/test_captive_portal_auto.py`

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 4 review agents run in PARALLEL. ALL must APPROVE. Present consolidated results to user and get explicit "okay" before completing.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, grep for patterns). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run linter + `python -m py_compile` on all changed files. Review for: bare except, excessive time.sleep without explanation, hardcoded IPs/SSIDs (should use router object), commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names.
  Output: `Lint [PASS/FAIL] | Files [N clean/N issues] | VERDICT`

- [x] F3. **Integration Test Review** — `deep`
  Verify all test files import correctly: `python -c "import tests.phone.test_token_input"`, same for all test files. Check that conftest fixtures are used correctly. Verify test markers are correct. Check that no test modifies global state without cleanup.
  Output: `Imports [N/N] | Markers [N/N] | Fixtures [N/N] | VERDICT`

- [x] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-task contamination. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **Wave 1**: `fix(phone): fix portal detection and add token typing flow` — wifi.py, adb.py
- **Wave 2**: `test(phone): redesign portal tests with real token typing` — test_*.py files

---

## Success Criteria

### Verification Commands
```bash
python -m py_compile lib/clients/wifi.py   # Expected: no output (success)
python -m py_compile lib/clients/adb.py    # Expected: no output (success)
python -c "from lib.clients.wifi import WiFi; print('OK')"  # Expected: OK
grep -r "notification.*shade\|swipe.*notification" lib/     # Expected: no matches (not relying on notification shade)
grep -r "svc data disable\|mobile_data" lib/ tests/        # Expected: no matches (not disabling mobile data)
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] No test exceeds 120s timeout
- [ ] Existing `pay_direct()` tests unchanged
- [ ] Portal opens via browser intent, not captive portal notification
