# Fix Secret Leakage in Test Report Publishing

## TL;DR

> **Quick Summary**: Fix 4 vectors where secrets (IPs, tokens, local paths, video content) can leak through the sanitize-results → gh-pages publishing pipeline.
> 
> **Deliverables**:
> - sanitize-results.sh redacts run.json and summary.json (not just text files)
> - .webm/.mp4 video files stripped from sanitized output and gitignored
> - redaction-report.json local path redacted
> - Automated verification test confirms all patterns are scrubbed
> 
> **Estimated Effort**: Quick
> **Parallel Execution**: NO — 3 tasks in 1 file + 1 gitignore, sequential
> **Critical Path**: Task 1 (gitignore) → Task 2 (sanitizer) → Task 3 (verification)

---

## Context

### Original Request
User asked to verify no env settings, passwords, MAC addresses, or other secrets leak through test reports published to gh-pages. Audit found 4 issues.

### Interview Summary
**Key Discussions**:
- Issue 1 (HIGH): run.json/summary.json copied verbatim by sanitizer — not sed'd
- Issue 2 (MEDIUM): .webm files not gitignored
- Issue 3 (MEDIUM): .webm files copied as-is through sanitizer wildcard case
- Issue 4 (LOW): redaction-report.json leaks local `/Users/` paths

**Research Findings**:
- run.json has one confirmed sensitive field: `lab.router_ip`. Defaults to `<REDACTED>` in standard `test-pr.sh` flow but leaks when `--router-ip` is passed manually.
- summary.json is NOT published to gh-pages by publish-report.sh — only a concern for standalone `make sanitize` usage.
- Videos are base64-embedded in pytest-html AND saved as standalone .webm files.
- Existing sed expressions already handle all needed patterns — just need to apply them to JSON files.
- jq not available on all systems — sed is the correct tool.

### Metis Review
**Identified Gaps** (addressed):
- redaction-report.json line 263 leaks `$(cd "$IN_DIR" && pwd)` — local path with username → added as Task 2 sub-fix
- summary.json not actually published to gh-pages → clarified scope: fix is for standalone sanitize usage
- Container mode bypass is deliberate design → excluded from scope
- sanitize-results.sh silent failure mode → excluded from scope, flagged for future
- Empty ARGS array should warn → added as defensive improvement in Task 2

---

## Work Objectives

### Core Objective
Ensure the sanitize-results.sh pipeline removes all secrets from every file type it outputs, and prevent video files from being committed or published.

### Concrete Deliverables
- `scripts/sanitize-results.sh`: redacts JSON metadata files, strips videos, fixes path leak in report, warns on empty config
- `.gitignore`: adds `*.webm *.mp4`
- Verification test: automated script confirms all patterns are scrubbed

### Definition of Done
- [ ] `shellcheck -s bash -S warning scripts/sanitize-results.sh` → clean
- [ ] Verification test passes: fake run.json with IP → sanitized output has no IP
- [ ] `git ls-files '*.webm' '*.mp4'` → empty
- [ ] `grep -E '\*\.webm|\*\.mp4' .gitignore` → matches

### Must Have
- run.json and summary.json sed'd with existing redaction patterns
- Video files (.webm, .mp4) excluded from sanitized output
- Video files (.webm, .mp4) in .gitignore
- redaction-report.json does not leak local paths
- No new dependencies

### Must NOT Have (Guardrails)
- Do NOT modify publish-report.sh
- Do NOT modify collect-results.py or generate-run-metadata.py
- Do NOT change the sed expressions or sanitize_text function
- Do NOT add jq, ffmpeg, exiftool, or any new dependency
- Do NOT change container mode behavior
- Do NOT modify the publish-report.sh silent-failure fallback
- Do NOT touch test code (conftest.py, etc.)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: YES (bash)
- **Automated tests**: Tests-after (verification script)
- **Framework**: bash test script

### QA Policy
Each task includes agent-executed QA scenarios with exact commands and assertions.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (sequential — single file changes):
├── Task 1: Add video extensions to .gitignore [quick]
└── Task 2: Fix sanitize-results.sh — JSON redaction + video handling + path leak [quick]

Wave 2 (after wave 1):
└── Task 3: Automated verification test [quick]

Wave FINAL:
├── F1: Plan compliance audit [oracle]
├── F2: Code quality review [unspecified-high]
└── F3: Scope fidelity check [deep]

Critical Path: Task 1 → Task 2 → Task 3 → F1-F3
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| 1 | — | 2 |
| 2 | 1 | 3 |
| 3 | 2 | F1-F3 |

### Agent Dispatch Summary

- **Wave 1**: 2 tasks — T1 → `quick`, T2 → `quick`
- **Wave 2**: 1 task — T3 → `quick`
- **FINAL**: 3 tasks — F1 → `oracle`, F2 → `unspecified-high`, F3 → `deep`

---

## TODOs

- [x] 1. Add video extensions to .gitignore

  **What to do**:
  - Verify no .webm or .mp4 files are currently tracked: `git ls-files '*.webm' '*.mp4'` must be empty
  - Add `*.webm` and `*.mp4` to `.gitignore` in the media section (after existing `*.png *.jpg` entries)

  **Must NOT do**:
  - Do not add blanket exclusions for other media types
  - Do not reorganize .gitignore

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (Task 2 depends on gitignore being updated)
  - **Parallel Group**: Wave 1 (sequential with Task 2)
  - **Blocks**: Task 2
  - **Blocked By**: None

  **References**:
  **Pattern References**:
  - `.gitignore:34-35` — existing `*.png` and `*.jpg` entries to follow pattern

  **Acceptance Criteria**:
  - [ ] `git ls-files '*.webm' '*.mp4'` returns empty (no tracked videos)
  - [ ] `grep -E '\*\.webm' .gitignore` matches
  - [ ] `grep -E '\*\.mp4' .gitignore` matches

  **QA Scenarios:**

  ```
  Scenario: Video files are gitignored
    Tool: Bash
    Preconditions: .gitignore updated
    Steps:
      1. Run: git check-ignore -v test-recording.webm
      2. Run: git check-ignore -v test-recording.mp4
    Expected Result: Both commands output .gitignore:line matching
    Failure Indicators: "fatal: no pathspec" or empty output
    Evidence: .sisyphus/evidence/task-1-gitignore-video.txt

  Scenario: No existing tracked video files
    Tool: Bash
    Steps:
      1. Run: git ls-files '*.webm' '*.mp4'
    Expected Result: Empty output
    Evidence: .sisyphus/evidence/task-1-no-tracked-video.txt
  ```

  **Commit**: YES (groups with Task 2)
  - Message: `fix(security): redact secrets from JSON metadata and strip videos from published reports`
  - Files: `.gitignore, scripts/sanitize-results.sh`

- [x] 2. Fix sanitize-results.sh — JSON redaction, video handling, path leak, empty-args warning

  **What to do**:

  **Fix A — Redact run.json and summary.json (lines 170-174)**:
  Currently the script does:
  ```bash
  for metafile in run.json summary.json; do
      if [ -f "$IN_DIR/$metafile" ]; then
          cp "$IN_DIR/$metafile" "$OUT_DIR/$metafile"
      fi
  done
  ```
  Change to copy first, then apply `sanitize_text`:
  ```bash
  for metafile in run.json summary.json; do
      if [ -f "$IN_DIR/$metafile" ]; then
          if [ "$INPLACE" = true ]; then
              tmp="$(mktemp)"
              sanitize_text "$IN_DIR/$metafile" "$tmp"
              mv "$tmp" "$OUT_DIR/$metafile"
          else
              sanitize_text "$IN_DIR/$metafile" "$OUT_DIR/$metafile"
          fi
          FILE_COUNT=$((FILE_COUNT + 1))
      fi
  done
  ```

  **Fix B — Strip video files from sanitized output (lines 195-211)**:
  Add video extensions to the case statement, BEFORE the `*)` catch-all. Videos should be excluded (not copied), similar to how phone screenshots are stripped in non-container mode:
  ```bash
  *.webm|*.mp4)
      # Strip video files from output — may contain IPs, SSIDs, portal UI
      SCREENSHOTS_STRIPPED=$((SCREENSHOTS_STRIPPED + 1))
      ;;
  ```

  **Fix C — Redact local path from redaction-report.json (line 263)**:
  Currently:
  ```bash
  "input_dir": "$(cd "$IN_DIR" && pwd)",
  ```
  Change to use a sanitized version that replaces the local path:
  ```bash
  "input_dir": "$(cd "$IN_DIR" && pwd | sed -E 's|/Users/[^/]+|<local-path>|g; s|/home/[^/]+|<local-path>|g')",
  ```
  Also do the same for `output_dir` on line 264.

  **Fix D — Warn when no redaction patterns configured (after line 110)**:
  After the ARGS array is built (around line 148), add:
  ```bash
  if [[ ${#ARGS[@]} -eq 0 ]]; then
      echo "WARNING: No redaction patterns configured. Output will not be sanitized." >&2
  fi
  ```

  **Must NOT do**:
  - Do NOT modify the existing sed expressions in ARGS
  - Do NOT modify the sanitize_text function
  - Do NOT add jq, ffmpeg, or other dependencies
  - Do NOT change container mode logic
  - Do NOT touch publish-report.sh, collect-results.py, or generate-run-metadata.py

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1 (after Task 1)
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `scripts/sanitize-results.sh:170-174` — current JSON copy block to modify
  - `scripts/sanitize-results.sh:195-211` — case statement to extend with video types
  - `scripts/sanitize-results.sh:150-152` — `sanitize_text()` function to reuse
  - `scripts/sanitize-results.sh:260-265` — redaction-report.json path fields
  - `scripts/sanitize-results.sh:110-148` — ARGS array construction, warning insertion point

  **API/Type References**:
  - `scripts/sanitize-results.sh:74-77` — env vars read for redaction values
  - `scripts/sanitize-results.sh:106-148` — all sed expression patterns

  **WHY Each Reference Matters**:
  - Lines 170-174: This is THE bug — cp without sed
  - Lines 195-211: This is where video files leak through `*) cp`
  - Lines 150-152: The existing sed function we reuse — DO NOT modify
  - Lines 260-265: Path leak in output report

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: run.json IP is redacted in sanitized output
    Tool: Bash
    Preconditions: Test directory with fake run.json containing 192.168.99.1
    Steps:
      1. mkdir -p /tmp/test-sanitize/raw
      2. echo '{"lab":{"router_ip":"192.168.99.1"}}' > /tmp/test-sanitize/run.json
      3. TOLLGATE_SSH_HOST=192.168.99.1 bash scripts/sanitize-results.sh /tmp/test-sanitize /tmp/test-sanitize-out
      4. grep -c "192.168.99.1" /tmp/test-sanitize-out/run.json
      5. grep -c "<router-ip>" /tmp/test-sanitize-out/run.json
    Expected Result: Step 4 returns exit code 1 (no match). Step 5 returns 0 with count >= 1.
    Failure Indicators: Step 4 returns 0 (IP still present) or step 5 returns 1 (replacement missing)
    Evidence: .sisyphus/evidence/task-2-runjson-redacted.txt

  Scenario: summary.json failure_message IP is redacted
    Tool: Bash
    Preconditions: Test directory with fake summary.json containing IP in failure_message
    Steps:
      1. mkdir -p /tmp/test-sanitize2/raw
      2. echo '{"tests":[{"failure_message":"ssh root@192.168.99.1 failed"}]}' > /tmp/test-sanitize2/summary.json
      3. TOLLGATE_SSH_HOST=192.168.99.1 bash scripts/sanitize-results.sh /tmp/test-sanitize2 /tmp/test-sanitize2-out
      4. grep -c "192.168.99.1" /tmp/test-sanitize2-out/summary.json
    Expected Result: Step 4 returns exit code 1 (no match)
    Evidence: .sisyphus/evidence/task-2-summaryjson-redacted.txt

  Scenario: .webm files are excluded from sanitized output
    Tool: Bash
    Preconditions: Test directory with a .webm file in raw/
    Steps:
      1. mkdir -p /tmp/test-sanitize3/raw
      2. echo 'fake' > /tmp/test-sanitize3/raw/test.webm
      3. echo '{}' > /tmp/test-sanitize3/run.json
      4. bash scripts/sanitize-results.sh /tmp/test-sanitize3 /tmp/test-sanitize3-out
      5. test -f /tmp/test-sanitize3-out/raw/test.webm && echo "FAIL" || echo "PASS"
    Expected Result: Step 5 prints "PASS" (file not present)
    Failure Indicators: "FAIL" (file was copied through)
    Evidence: .sisyphus/evidence/task-2-webm-stripped.txt

  Scenario: redaction-report.json does not leak local path
    Tool: Bash
    Preconditions: Sanitize has been run (reusing output from previous scenario)
    Steps:
      1. grep -c "/Users/" /tmp/test-sanitize3-out/redaction-report.json
    Expected Result: Exit code 1 (no match)
    Evidence: .sisyphus/evidence/task-2-no-path-leak.txt

  Scenario: Warning printed when no redaction patterns configured
    Tool: Bash
    Preconditions: No TOLLGATE_SSH_HOST or related env vars set
    Steps:
      1. mkdir -p /tmp/test-sanitize4/raw
      2. echo '{}' > /tmp/test-sanitize4/run.json
      3. env -i PATH="$PATH" bash scripts/sanitize-results.sh /tmp/test-sanitize4 /tmp/test-sanitize4-out 2>&1
    Expected Result: stderr contains "WARNING: No redaction patterns"
    Evidence: .sisyphus/evidence/task-2-empty-args-warning.txt

  Scenario: shellcheck passes
    Tool: Bash
    Steps:
      1. shellcheck -s bash -S warning scripts/sanitize-results.sh
    Expected Result: No output (clean)
    Evidence: .sisyphus/evidence/task-2-shellcheck.txt
  ```

  **Commit**: YES (groups with Task 1)
  - Message: `fix(security): redact secrets from JSON metadata and strip videos from published reports`
  - Files: `.gitignore, scripts/sanitize-results.sh`

---

- [x] 3. End-to-end verification test

  **What to do**:
  Create and run a comprehensive verification script that exercises all fixes:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  
  REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  SCRATCH="/tmp/tollgate-sanitize-verify-$$"
  PASS=0; FAIL=0
  
  cleanup() { rm -rf "$SCRATCH"; }
  trap cleanup EXIT
  
  ok() { PASS=$((PASS+1)); echo "  PASS: $1"; }
  fail() { FAIL=$((FAIL+1)); echo "  FAIL: $1"; }
  
  # Setup: canonical run dir with fake sensitive data
  mkdir -p "$SCRATCH/input/raw/api"
  cat > "$SCRATCH/input/run.json" << 'EOF'
  {"schema_version":1,"lab":{"router_ip":"192.168.99.1","client_type":"adb"},"counts":{"total":1}}
  EOF
  cat > "$SCRATCH/input/summary.json" << 'EOF'
  {"tests":[{"name":"test_x","failure_message":"ssh root@192.168.99.1: Connection refused - password=tollgate"}]}
  EOF
  echo "fake video" > "$SCRATCH/input/raw/api/recording.webm"
  echo "some log with 192.168.99.1 and AA:BB:CC:DD:EE:FF mac" > "$SCRATCH/input/raw/api/output.log"
  
  # Run sanitizer
  TOLLGATE_SSH_HOST=192.168.99.1 TOLLGATE_LUCI_PASSWORD=tollgate \
    bash "$REPO_DIR/scripts/sanitize-results.sh" "$SCRATCH/input" "$SCRATCH/output" 2>/dev/null
  
  # Test 1: run.json IP redacted
  if grep -q "192.168.99.1" "$SCRATCH/output/run.json" 2>/dev/null; then
    fail "run.json still contains router IP"
  else
    ok "run.json router IP redacted"
  fi
  
  # Test 2: summary.json IP redacted
  if grep -q "192.168.99.1" "$SCRATCH/output/summary.json" 2>/dev/null; then
    fail "summary.json still contains router IP"
  else
    ok "summary.json router IP redacted"
  fi
  
  # Test 3: summary.json password redacted
  if grep -q "tollgate" "$SCRATCH/output/summary.json" 2>/dev/null; then
    fail "summary.json still contains password"
  else
    ok "summary.json password redacted"
  fi
  
  # Test 4: .webm excluded
  if test -f "$SCRATCH/output/raw/api/recording.webm"; then
    fail ".webm file was copied to output"
  else
    ok ".webm file stripped from output"
  fi
  
  # Test 5: log file sanitized
  if grep -q "192.168.99.1" "$SCRATCH/output/raw/api/output.log"; then
    fail "log file still contains IP"
  else
    ok "log file IP redacted"
  fi
  if grep -q "AA:BB:CC:DD:EE:FF" "$SCRATCH/output/raw/api/output.log"; then
    fail "log file still contains MAC"
  else
    ok "log file MAC redacted"
  fi
  
  # Test 6: redaction report has no local paths
  if grep -q "/Users/" "$SCRATCH/output/redaction-report.json" 2>/dev/null; then
    fail "redaction-report.json leaks /Users/ path"
  elif grep -q "/home/" "$SCRATCH/output/redaction-report.json" 2>/dev/null; then
    fail "redaction-report.json leaks /home/ path"
  else
    ok "redaction-report.json no local path leak"
  fi
  
  echo ""
  echo "Results: $PASS passed, $FAIL failed"
  exit $FAIL
  ```

  **Must NOT do**:
  - Do not modify production scripts as part of this task
  - Do not require real router or real test results

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after Task 2)
  - **Blocks**: Final verification
  - **Blocked By**: Task 2

  **References**:
  - `scripts/sanitize-results.sh` — the script under test

  **Acceptance Criteria**:

  **QA Scenarios:**

  ```
  Scenario: All verification tests pass
    Tool: Bash
    Preconditions: Tasks 1 and 2 complete
    Steps:
      1. Run the verification script above
      2. Check exit code
    Expected Result: Exit code 0, all tests report PASS, 0 FAIL
    Failure Indicators: Any test reports FAIL or non-zero exit code
    Evidence: .sisyphus/evidence/task-3-verification-output.txt
  ```

  **Commit**: NO (verification only, no files to commit)

---

## Final Verification Wave (MANDATORY — after ALL implementation tasks)

> 3 review agents run in PARALLEL. ALL must APPROVE.

- [x] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists (read file, run command). For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [x] F2. **Code Quality Review** — `unspecified-high`
  Run `shellcheck -s bash -S warning scripts/sanitize-results.sh`. Verify no new warnings. Check the verification test passes. Review diff for: accidental sed expression changes, jq imports, publish-report.sh modifications.
  Output: `Shellcheck [PASS/FAIL] | Verification [PASS/FAIL] | Diff Scope [CLEAN/N violations] | VERDICT`

- [x] F3. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built (no missing), nothing beyond spec was built (no creep). Check "Must NOT do" compliance. Detect cross-file contamination: only sanitize-results.sh and .gitignore should have changes.
  Output: `Tasks [N/N compliant] | Contamination [CLEAN/N issues] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

- **1**: `fix(security): redact secrets from JSON metadata and strip videos from published reports` — .gitignore, scripts/sanitize-results.sh
  - Pre-commit: shellcheck + verification test

---

## Success Criteria

### Verification Commands
```bash
shellcheck -s bash -S warning scripts/sanitize-results.sh  # Expected: no output (clean)
grep -E '\*\.webm|\*\.mp4' .gitignore                       # Expected: matches
git ls-files '*.webm' '*.mp4'                                # Expected: empty
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] Verification test passes
- [ ] shellcheck clean
