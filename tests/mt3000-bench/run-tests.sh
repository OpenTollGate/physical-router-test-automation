#!/usr/bin/env bash
#
# run-tests.sh — the bench-lock + bench-deploy negative-control suite (NO ROUTER).
#
# Proves, offline:
#   * lock exclusivity            — a second owner cannot take the bench
#   * holder identity in the error — every refusal names who owns the bench
#   * the wrapper refuses while held, and the router is untouched
#   * stale-lock recovery only with an explicit flag, and it warns
#   * a deploy refuses without the lock (and installs nothing)
#   * a deploy rotates stale staged apks and names its artifact
#   * the post-install identity check PASSES on the artifact's real payload bytes
#   * ... and FAILS LOUDLY when a different build is what actually got installed
#     (install build A while naming build B — the card's evidence gate)
#   * a substituted apk already staged under our name is refused BEFORE installing
#
# The "router" is a throw-away directory; ssh/scp/apk are PATH test doubles in
# harness/bin/. The remote scripts are the production ones — only TG_TMP/TG_BIN/TG_ETC/
# TG_APKLOG are exported into the harness root by the ssh double.
#
# Fixtures: two REAL aarch64 .apk files (their usr/bin/tollgate-wrt payloads are extracted
# with apk.static). Override with BENCH_TEST_APK_A / BENCH_TEST_APK_B. If they or
# apk.static are missing the hardware-identity tests SKIP with a reason (never a false pass).
#
# usage: tests/mt3000-bench/run-tests.sh
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/harness/lib.sh"

WORK="${BENCH_TEST_WORKDIR:-$(mktemp -d "${TMPDIR:-/tmp}/bench-lock-tests.XXXXXX")}"
mkdir -p "$WORK"
export BENCH_LOCK_PATH="$WORK/bench-mt3000.lock"
export BENCH_PROFILE="test-harness"
export BENCH_ROUTER_PW_FILE="$WORK/pw"
# the harness ssh double ignores this value; never put a real lab credential in a repo
printf 'harness-dummy-pw\n' > "$BENCH_ROUTER_PW_FILE"; chmod 600 "$BENCH_ROUTER_PW_FILE"
export BENCH_HARNESS_ROOT="$WORK/router"
export PATH="$HERE/harness/bin:$PATH"

APK_A="${BENCH_TEST_APK_A:-$HOME/.tg-e2e/cache/tollgate-wrt_0.6.0_alpha5_aarch64_cortex-a53_portalcu102ln004.apk}"
APK_B="${BENCH_TEST_APK_B:-$HOME/tollgate-pre16-validation/published/tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk}"
SHA_A=""; SHA_B=""; PAY_A=""; PAY_B=""

printf 'bench-lock tests: workdir=%s\nlock=%s\n' "$WORK" "$BENCH_LOCK_PATH"

# ---------------------------------------------------------------- fixtures
have_fixtures=0
if [ -f "$APK_A" ] && [ -f "$APK_B" ]; then
  mkdir -p "$WORK/payloads"
  if payload_extract "$APK_A" "$WORK/payloads/A" && payload_extract "$APK_B" "$WORK/payloads/B"; then
    PAY_A="$WORK/payloads/A"; PAY_B="$WORK/payloads/B"
    SHA_A="$(sha256sum "$APK_A" | cut -d' ' -f1)"
    SHA_B="$(sha256sum "$APK_B" | cut -d' ' -f1)"
    if [ "$(sha256sum "$PAY_A" | cut -d' ' -f1)" = "$(sha256sum "$PAY_B" | cut -d' ' -f1)" ]; then
      printf 'fixtures: A and B have identical payloads — using them would prove nothing\n'
    else
      have_fixtures=1
      printf 'fixtures: A=%s (payload %s)\n          B=%s (payload %s)\n' \
        "$(basename "$APK_A")" "$(sha256sum "$PAY_A" | cut -c1-16)" \
        "$(basename "$APK_B")" "$(sha256sum "$PAY_B" | cut -c1-16)"
    fi
  fi
fi
[ "$have_fixtures" = 1 ] || printf 'fixtures: MISSING (apk.static or the two fixture apks) — identity tests will SKIP\n'

new_router() {   # fresh harness router root with both payloads mapped
  harness_root_new "$BENCH_HARNESS_ROOT" "$PAY_B"
  harness_payload_map "$APK_A" "$PAY_A"
  harness_payload_map "$APK_B" "$PAY_B"
}

deploy_in_window() {   # $1=purpose ; rest = deploy args
  local purpose="$1"; shift
  run_cmd "$BENCH_WITH_LOCK" --purpose "$purpose" -- "$BENCH_DEPLOY" "$@"
}

# =============================================================================== 1. lock
t_begin "two owners cannot both take the bench"
rm -f "$BENCH_LOCK_PATH"
BENCH_PROFILE=alpha "$BENCH_LOCK" take --purpose "holder-test" --task t_aaa --hold 12 >"$WORK/holder1.out" 2>&1 &
HOLDER1=$!
sleep 1
run_cmd env BENCH_PROFILE=beta "$BENCH_LOCK" take --purpose "second-window"
check_rc "second take refused" 3 "$RC"
check_contains "refusal names the holder profile" "HOLDER alpha pid=" "$OUT"
check_contains "refusal names the holder purpose" "purpose=holder-test" "$OUT"
check_contains "refusal names the holder task"   "task=t_aaa" "$OUT"
check_contains "refusal says the bench is OWNED" "OWNED by another window" "$OUT"
HL="$(lock_holder_line)"
check_contains "holder line carries profile+pid" "alpha pid=" "$HL"
check_contains "holder line carries purpose+since" "purpose=holder-test since=" "$HL"

t_begin "the router-touching WRAPPER refuses while the bench is held (negative control A)"
run_cmd "$BENCH_WITH_LOCK" --purpose "second-window" -- true
check_rc "bench-with-lock refused" 3 "$RC"
check_contains "wrapper refusal names the holder" "HOLDER alpha pid=" "$OUT"
printf '   raw refusal: %s\n' "$(printf '%s' "$OUT" | head -2 | tr '\n' ' ')"

t_begin "a second ROUTER-TOUCHING SCRIPT is refused, naming the holder (negative control A2)"
run_cmd "$ROUTER_TOUCHER"
check_rc "router-touching script refused" 4 "$RC"
check_contains "script refusal names the holder line" "HOLDER alpha pid=" "$OUT"
check_contains "script refusal names the holder profile" "profile=alpha" "$OUT"
check_contains "script did not touch the router" "not running under the bench lock" "$OUT"
check_not_contains "script never reached its router commands" "ROUTER-TOUCHED" "$OUT"
printf '   raw refusal (stderr):\n%s\n' "$(printf '%s' "$OUT" | sed 's/^/        /')"

t_begin "the deploy helper refuses without the lock and installs nothing"
new_router
BEFORE="$(harness_installed_sha)"
run_cmd "$BENCH_DEPLOY" --apk "$APK_B" --sha256 "$SHA_B" --install-timeout 30
check_rc "deploy without a lock refused" 4 "$RC"
check_contains "refusal explains the required invocation" "not running under the bench lock" "$OUT"
check_eq "router binary untouched" "$BEFORE" "$(harness_installed_sha)"
check_eq "no apk install was attempted" "0" "$(harness_apk_installs)"

t_begin "release ends the window; the bench becomes takeable again"
run_cmd "$BENCH_LOCK" release --force
check_rc "release ok" 0 "$RC"
wait "$HOLDER1" 2>/dev/null
run_cmd "$BENCH_LOCK" status
check_rc "status FREE after release" 0 "$RC"
check_contains "status reports FREE" "STATE     FREE" "$OUT"
run_cmd env BENCH_PROFILE=beta "$BENCH_LOCK" status
check_eq "a different profile sees the same FREE state" "0" "$RC"

t_begin "stale holder line (no flock behind it) needs an EXPLICIT reclaim flag"
printf 'ghost pid=999999 purpose=dead-since-tuesday since=2020-01-01T00:00:00+00:00 task=- host=nowhere\n' > "$BENCH_LOCK_PATH"
run_cmd "$BENCH_LOCK" status
check_rc "status reports STALE-METADATA" 5 "$RC"
check_contains "status names the stale holder" "STATE     STALE-METADATA" "$OUT"
run_cmd env BENCH_PROFILE=beta "$BENCH_LOCK" take --purpose "should-refuse" --hold 1
check_rc "take refused on stale metadata" 5 "$RC"
check_contains "refusal shows the stale holder line" "HOLDER ghost pid=999999" "$OUT"
check_contains "refusal names the explicit recovery flag" "--reclaim-stale" "$OUT"
check_not_contains "auto-recovery did NOT happen silently" "LOCKED" "$OUT"
run_cmd env BENCH_PROFILE=beta "$BENCH_LOCK" take --purpose "explicit-reclaim" --hold 1 --reclaim-stale
check_rc "explicit reclaim succeeds" 0 "$RC"
check_contains "reclaim prints a warning" "WARNING: reclaiming a STALE holder line" "$OUT"
check_contains "reclaim restates the safety rule" "explicit-only by design" "$OUT"
: > "$BENCH_LOCK_PATH"

# =============================================================================== 2. deploy
if [ "$have_fixtures" != 1 ]; then
  for n in "deploy rotates a stale staged apk, names its artifact and verifies the installed identity" \
           "verify-only matches the installed build, and fails loudly when naming a different one" \
           "a substituted apk already staged under our name is REFUSED before installing" \
           "install build A while naming build B => loud identity MISMATCH" \
           "--refuse-foreign-staged refuses instead of rotating" \
           "install.json package_path revert bomb is refused, then cleared on request"; do
    t_begin "$n"; skip "no fixture apks / apk.static on this host"
  done
else
  t_begin "deploy rotates a stale staged apk, names its artifact and verifies the installed identity"
  new_router
  cp -f "$APK_A" "$BENCH_HARNESS_ROOT/tmp/tg-alpha5.apk"        # the 2026-09-24 revert bomb shape
  cp -f "$APK_A" "$BENCH_HARNESS_ROOT/tmp/tollgate-wrt-old.apk"
  deploy_in_window "t09-deploy" --apk "$APK_B" --sha256 "$SHA_B" --install-timeout 60
  check_rc "deploy succeeded" 0 "$RC"
  check_contains "artifact is NAMED with path+sha256" "ARTIFACT name=$(basename "$APK_B")" "$OUT"
  check_contains "artifact sha256 is the named one" "sha256=$SHA_B" "$OUT"
  check_contains "payload identity is named too" "PAYLOAD usr/bin/tollgate-wrt" "$OUT"
  check_contains "stale apk rotated aside" "ROTATED tg-alpha5.apk -> tg-alpha5.apk.rotated-" "$OUT"
  check_contains "second stale apk rotated aside" "ROTATED tollgate-wrt-old.apk ->" "$OUT"
  check_contains "staged under a sha-named artifact" "STAGED-VERIFIED /tmp/bench-${SHA_B:0:12}-" "$OUT"
  check_contains "installed identity verified" "INSTALLED VERIFIED" "$OUT"
  check_contains "identity names both hashes" "artifact_payload_sha256=$(sha256sum "$PAY_B" | cut -d' ' -f1)" "$OUT"
  check_eq "router binary IS the artifact payload" "$(sha256sum "$PAY_B" | cut -d' ' -f1)" "$(harness_installed_sha)"
  check_eq "an install actually happened" "1" "$(harness_apk_installs)"
  check_eq "no apk left staged under its original name" "0" "$(ls "$BENCH_HARNESS_ROOT/tmp/tg-alpha5.apk" 2>/dev/null | wc -l | tr -d ' ')"
  check_eq "rotated bytes are preserved, not deleted" "2" "$(ls "$BENCH_HARNESS_ROOT"/tmp/*.rotated-* 2>/dev/null | wc -l | tr -d ' ')"

  t_begin "verify-only matches the installed build, and fails loudly when naming a different one"
  deploy_in_window "t10-verify" --apk "$APK_B" --sha256 "$SHA_B" --verify-only
  check_rc "verify-only against the installed build: MATCH" 0 "$RC"
  check_contains "verify-only reports the identity" "IDENTITY VERIFIED (verify-only)" "$OUT"
  check_eq "verify-only installed nothing" "1" "$(harness_apk_installs)"
  deploy_in_window "t10-verify-mismatch" --apk "$APK_A" --sha256 "$SHA_A" --verify-only
  check_rc "verify-only naming build A fails loudly" 8 "$RC"
  check_contains "mismatch is reported" "IDENTITY MISMATCH (verify-only)" "$OUT"
  check_contains "mismatch shows the router's bytes" "$(harness_installed_sha)" "$OUT"
  check_contains "mismatch shows the named artifact's bytes" "$(sha256sum "$PAY_A" | cut -d' ' -f1)" "$OUT"

  t_begin "a substituted apk already staged under our name is REFUSED before installing"
  new_router
  cp -f "$APK_A" "$BENCH_HARNESS_ROOT/tmp/$(basename "$APK_B")"     # right name, wrong build
  BEFORE="$(harness_installed_sha)"
  deploy_in_window "t11-substituted" --apk "$APK_B" --sha256 "$SHA_B" --install-timeout 30
  check_rc "substituted staged artifact refused (exit 7)" 7 "$RC"
  check_contains "refusal says SUBSTITUTED ARTIFACT" "SUBSTITUTED ARTIFACT REFUSED" "$OUT"
  check_contains "refusal shows the staged sha256" "staged sha256=$SHA_A" "$OUT"
  check_contains "refusal shows the named sha256" "named sha256=$SHA_B" "$OUT"
  check_eq "router binary untouched" "$BEFORE" "$(harness_installed_sha)"
  check_eq "no install happened" "0" "$(harness_apk_installs)"

  t_begin "install build A while naming build B => loud identity MISMATCH (negative control B)"
  new_router
  export HARNESS_FORCE_PAYLOAD="$PAY_A"     # the stand-in apk installs A no matter what is staged
  deploy_in_window "t12-substitution" --apk "$APK_B" --sha256 "$SHA_B" --install-timeout 60
  RC_SUB="$RC"; OUT_SUB="$OUT"
  unset HARNESS_FORCE_PAYLOAD
  check_rc "deploy failed loudly (exit 8)" 8 "$RC_SUB"
  check_contains "loud failure is named" "IDENTITY MISMATCH - LOUD FAILURE" "$OUT_SUB"
  check_contains "failure shows what the router runs" "$(sha256sum "$PAY_A" | cut -d' ' -f1)" "$OUT_SUB"
  check_contains "failure shows what was named" "$(sha256sum "$PAY_B" | cut -d' ' -f1)" "$OUT_SUB"
  check_contains "failure says do not call the bench ready" "Do not report this bench as ready" "$OUT_SUB"
  check_eq "router really did end up with A's bytes" "$(sha256sum "$PAY_A" | cut -d' ' -f1)" "$(harness_installed_sha)"
  check_not_contains "no success line was printed" "INSTALLED VERIFIED" "$OUT_SUB"

  t_begin "--refuse-foreign-staged refuses instead of rotating"
  new_router
  cp -f "$APK_A" "$BENCH_HARNESS_ROOT/tmp/tg-alpha5.apk"
  deploy_in_window "t13-refuse-foreign" --apk "$APK_B" --sha256 "$SHA_B" --refuse-foreign-staged --install-timeout 30
  check_rc "foreign staged apk refused (exit 7)" 7 "$RC"
  check_contains "refusal names the foreign file" "FOREIGN STAGED APK REFUSED" "$OUT"
  check_eq "foreign file was NOT rotated" "1" "$(ls "$BENCH_HARNESS_ROOT/tmp/tg-alpha5.apk" 2>/dev/null | wc -l | tr -d ' ')"
  check_eq "no install happened" "0" "$(harness_apk_installs)"

  t_begin "install.json package_path revert bomb is refused, then cleared on request"
  new_router
  printf '{\n  "package_path": "/tmp/tg-alpha5.apk",\n  "version": "1"\n}\n' > "$BENCH_HARNESS_ROOT/etc/tollgate/install.json"
  deploy_in_window "t14-package-path" --apk "$APK_B" --sha256 "$SHA_B" --install-timeout 30
  check_rc "revert bomb refused (exit 7)" 7 "$RC"
  check_contains "refusal names the revert bomb" "package_path='/tmp/tg-alpha5.apk'" "$OUT"
  check_eq "no install happened" "0" "$(harness_apk_installs)"
  deploy_in_window "t14-clear" --apk "$APK_B" --sha256 "$SHA_B" --clear-package-path --install-timeout 60
  check_rc "deploy proceeds once package_path is cleared" 0 "$RC"
  check_contains "package_path was neutralised" "CLEARED_PACKAGE_PATH" "$OUT"
  check_contains "install.json now says false" '"package_path": "false"' "$(cat "$BENCH_HARNESS_ROOT/etc/tollgate/install.json")"
  check_contains "identity verified after the cleared deploy" "INSTALLED VERIFIED" "$OUT"
fi

# =============================================================================== 3. naming
t_begin "a deploy that does not NAME its artifact is refused before anything else"
new_router
run_cmd "$BENCH_LOCK" exec --purpose "t15-noname" -- "$BENCH_DEPLOY" --apk "$APK_B" 2>&1
check_rc "missing --sha256 refused (usage)" 2 "$RC"
check_contains "refusal says a deploy must name its sha256" "must name the sha256" "$OUT"

if [ "$have_fixtures" = 1 ]; then
t_begin "a named sha256 that does not match the file is refused"
run_cmd "$BENCH_LOCK" exec --purpose "t16-badhash" -- "$BENCH_DEPLOY" --apk "$APK_B" --sha256 "$(printf '0%.0s' $(seq 1 64))" 2>&1
check_rc "artifact/sha mismatch refused (exit 6)" 6 "$RC"
check_contains "refusal names both hashes" "artifact identity mismatch" "$OUT"

t_begin "--dry-run stops before the install"
new_router
deploy_in_window "t17-dry" --apk "$APK_B" --sha256 "$SHA_B" --dry-run
check_rc "dry-run exits 0" 0 "$RC"
check_contains "dry-run says it would install" "DRY-RUN: would now 'apk add" "$OUT"
check_eq "dry-run installed nothing" "0" "$(harness_apk_installs)"

t_begin "the whole deploy path runs under BusyBox ash (the router's shell)"
if command -v busybox >/dev/null 2>&1; then
  new_router
  export BENCH_HARNESS_SH="busybox ash"
  deploy_in_window "t18-busybox" --apk "$APK_B" --sha256 "$SHA_B" --install-timeout 60
  unset BENCH_HARNESS_SH
  check_rc "deploy under busybox ash succeeded" 0 "$RC"
  check_contains "identity verified under busybox ash" "INSTALLED VERIFIED" "$OUT"
  INST="$(ls -1 "$BENCH_HARNESS_ROOT"/tmp/bench-install-*.sh 2>/dev/null | head -1)"
  if [ -n "$INST" ] && busybox ash -n "$INST"; then pass "the generated install script is accepted by busybox ash"
  else fail "busybox ash rejected the generated install script ($INST)"; fi
else
  skip "no busybox on this host"
fi
else
  t_begin "a named sha256 that does not match the file is refused"; skip "no fixtures"
  t_begin "--dry-run stops before the install"; skip "no fixtures"
  t_begin "the whole deploy path runs under BusyBox ash (the router's shell)"; skip "no fixtures"
fi

lock_kill_all
rm -f "$BENCH_LOCK_PATH"
printf '\nworkdir kept for inspection: %s\n' "$WORK"
summary
