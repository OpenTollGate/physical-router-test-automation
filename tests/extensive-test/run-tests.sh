#!/usr/bin/env bash
# =============================================================================
# tests/extensive-test/run-tests.sh — the NO-LAB offline suite for the
# extensive-test.sh / run-local-tests.sh wrapper contract.
#
# Covers the PR-#25 review-debt row (#285..#292) with PATH doubles and
# ZERO network. What it proves without a lab:
#
#   * run_tests pairs value-taking flags: `-m EXPR` / `--story LIST` reach
#     pytest intact (the old flag/target split dropped the -m value into
#     the target list — every wrapper-driven tier was a usage error) and
#     directory targets recurse past -maxdepth 1 (#285);
#   * PATHS joins on ' or ' — not the tr ' '->'o' character mapping that
#     produced `smoke and (failureoregression)` (#292);
#   * ipk_version reads both ipk container formats (gzip-tar + classic ar)
#     (#291);
#   * the venue-virtual flow survives the #286 trap (pgrep -c prints 0 AND
#     exits 1 → the old '|| echo 0' produced "0\n0" and skipped bring-up
#     against a dead lab): bring-up and tier delegation are REACHED under
#     the same conditions, and the #290 revision check fires;
#   * the physical deploy is lease-wrapped (hw-bench-lease exec, the #289
#     hardware lock) and &&-coupled end to end (#288).
#
# Exit: 0 = all pass; 1 = any failure (named).
# =============================================================================
set -uo pipefail

HERE=$(cd "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
cd "$REPO_ROOT"

TMP=$(mktemp -d)
mkdir -p "$TMP/bin" "$TMP/tree" "$TMP/home/.tollgate-test-venv/bin"
trap 'rm -rf "$TMP"' EXIT

FAILURES=0
ok()   { echo "  ok    - $1"; }
bad()  { echo "  FAIL  - $1"; FAILURES=$((FAILURES + 1)); }

snapshot_results() { ls results 2>/dev/null | sort; }
# clean_new_results <snapshot-file>: rm only result dirs created after the
# snapshot (this suite's sweep output; results/ is gitignored, stay tidy).
clean_new_results() {
  comm -13 "$1" <(snapshot_results) | while IFS= read -r d; do
    rm -rf "results/$d"
  done
}

# --- doubles -----------------------------------------------------------------
# $PY recorder (extensive-test's tiers): one block per invocation.
cat > "$TMP/bin/py-rec" <<'EOF'
#!/usr/bin/env bash
{ echo "INVOCATION"; for a in "$@"; do printf '%s\n' "$a"; done; echo "END"; } \
  >> "${PYREC:?}"
exit 0
EOF
chmod +x "$TMP/bin/py-rec"

# sshpass double: silent success (venue asserts see a quiet router).
cat > "$TMP/bin/sshpass" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/bin/sshpass"

echo "suite: run-local-tests run_tests contract (#285)"

# Extract run_tests() alone and source it with doubles for its free names.
sed -n '/^run_tests()/,/^}/p' scripts/run-local-tests.sh > "$TMP/run_tests.fn"
# shellcheck source=/dev/null
. "$TMP/run_tests.fn"

log() { :; }
merge_junit() { :; }
configure_mint() { :; }
export PATH="$TMP/bin:$PATH"
export REPO_DIR="$TMP/tree"
export HOME="$TMP/home"
export LOCAL_TEST_FILE_BUDGET=0
export PYREC="$TMP/pyrec.args"
# venue env the sourced function reads (all stubbed: sshpass/ssh doubles
# answer nothing; empty MINT_URL keeps assert_venue_config quiet)
export OPENWRT_IP=127.0.0.1
export PASSWORD=stub
export MINT_URL=""
export DEBIAN_IP=127.0.0.1
export DEBIAN_VM_UP=false

# python3 recorder for run_tests' pytest calls (same file shape as py-rec)
cat > "$TMP/bin/python3" <<'EOF'
#!/usr/bin/env bash
{ echo "INVOCATION"; for a in "$@"; do printf '%s\n' "$a"; done; echo "END"; } \
  >> "${PYREC:?}"
exit 0
EOF
chmod +x "$TMP/bin/python3"

# venv activate double (run_tests sources it)
: > "$HOME/.tollgate-test-venv/bin/activate"

# recursion tree: root + one-deep + two-deep files
mkdir -p "$REPO_DIR/tests/api" "$REPO_DIR/tests/unit" "$REPO_DIR/tests/scenarios/stories"
touch "$REPO_DIR/tests/test_root.py" \
      "$REPO_DIR/tests/api/test_a.py" \
      "$REPO_DIR/tests/unit/test_b.py" \
      "$REPO_DIR/tests/scenarios/stories/test_c.py"

: > "$PYREC"
# subshells: run_tests cd's into REPO_DIR; the harness cwd must not move
( run_tests "$REPO_DIR/tests/" -m "smoke and (failure or regression)" --story "G2,G4" \
  > /dev/null 2>&1 )
n_inv=$(grep -c '^INVOCATION$' "$PYREC")

[ "$n_inv" -eq 4 ] \
  && ok "directory target recursed: 4 test files selected (root+api+unit+scenarios/stories)" \
  || bad "expected 4 pytest invocations from the recursed tree, got ${n_inv}"

# flags are identical across invocations → count-matching is block-exact
n_m=$(grep -cx 'smoke and (failure or regression)' "$PYREC")
[ "$n_inv" -gt 0 ] && [ "$n_m" -eq "$n_inv" ] \
  && ok "the -m expression reached every pytest invocation intact" \
  || bad "-m value lost: ${n_m}/${n_inv} invocations"
n_s=$(grep -cx 'G2,G4' "$PYREC")
n_sf=$(grep -cx -- '--story' "$PYREC")
[ "$n_s" -eq "$n_inv" ] && [ "$n_sf" -eq "$n_inv" ] \
  && ok "the --story pair reached every invocation intact" \
  || bad "--story pair lost: ${n_s}/${n_inv} values, ${n_sf} flags"
for f in test_root.py test_a.py test_b.py test_c.py; do
  grep -q "$f" "$PYREC" || bad "recursion missed $f"
done
[ "$FAILURES" -eq 0 ] && ok "recursion selected every depth (0/1/2)"

# single concrete file: single-invocation branch, flags still paired
: > "$PYREC"
( run_tests "$REPO_DIR/tests/api/test_a.py" -m smoke > /dev/null 2>&1 )
n_inv=$(grep -c '^INVOCATION$' "$PYREC")
[ "$n_inv" -eq 1 ] \
  && ok "single target ran once" \
  || bad "single target should be one invocation, got ${n_inv}"
grep -qx 'smoke' "$PYREC" \
  && ok "single-target -m value intact" \
  || bad "single-target -m value lost"

# -m without a value: usage error, not a silent wrong run
# (subshell: the sourced function's exit must not take the harness down)
( run_tests -m > /dev/null 2>&1 )
[ $? -eq 64 ] \
  && ok "dangling -m exits 64 (usage error)" \
  || bad "dangling -m did not fail as usage error"

echo "suite: extensive-test PATHS join + flag pass-through (#292, #285 shape)"

export PYREC="$TMP/pyrec2.args"
: > "$PYREC"
RES_BEFORE="$TMP/results-before.txt"; snapshot_results > "$RES_BEFORE"
TOLLGATE_VENUE=skip TIERS="smoke" PATHS="failure regression" STORIES="G2 G4" \
  TOLLGATE_PYTHON="$TMP/bin/py-rec" \
  bash scripts/extensive-test.sh > "$TMP/ext1.log" 2>&1
rc=$?
clean_new_results "$RES_BEFORE"
[ "$rc" -eq 0 ] \
  && ok "VENUE=skip sweep exits 0 with doubles" \
  || bad "VENUE=skip sweep exited ${rc}: $(tail -2 "$TMP/ext1.log" | tr '\n' ' ')"

grep -qx 'smoke and (failure or regression)' "$PYREC" \
  && ok "PATHS join: pytest received 'smoke and (failure or regression)'" \
  || bad "PATHS join mangled (expected the joined marker line)"
grep -qx 'G2,G4' "$PYREC" \
  && ok "STORIES list reached pytest comma-joined" \
  || bad "STORIES join mangled"
grep -qx 'failureoregression' "$PYREC" \
  && bad "the old tr-mangled form still appears" \
  || ok "no tr-mangled 'failureoregression' form"

echo "suite: ipk_version (#291 helper)"

# extract the helper from extensive-test.sh
sed -n '/^ipk_version()/,/^}/p' scripts/extensive-test.sh > "$TMP/ipkv.fn"
# shellcheck source=/dev/null
. "$TMP/ipkv.fn"

# synthetic gzip-container ipk
mkdir -p "$TMP/ipkctl"
printf 'Package: tollgate-wrt\nVersion: vTEST-tgz\nArchitecture: test\n' > "$TMP/ipkctl/control"
( cd "$TMP/ipkctl" && tar czf control.tar.gz ./control && \
  : > debian-binary && : > data.tar.gz && \
  tar czf "$TMP/fake-tgz.ipk" ./debian-binary ./control.tar.gz ./data.tar.gz )
v=$(ipk_version "$TMP/fake-tgz.ipk")
[ "$v" = "vTEST-tgz" ] \
  && ok "gzip-container ipk version read" \
  || bad "gzip-container ipk version: got '${v:-none}'"

# synthetic classic-ar ipk
( cd "$TMP/ipkctl" && ar rc "$TMP/fake-ar.ipk" debian-binary control.tar.gz data.tar.gz )
v=$(ipk_version "$TMP/fake-ar.ipk")
[ "$v" = "vTEST-tgz" ] \
  && ok "ar-container ipk version read" \
  || bad "ar-container ipk version: got '${v:-none}'"

v=$(ipk_version "$TMP/run_tests.fn")
[ -z "$v" ] \
  && ok "non-ipk input yields empty (caller fails loud)" \
  || bad "non-ipk input yielded '$v'"

echo "suite: assert_venue_config full-fixture drift gate (#299)"

sed -n '/^assert_venue_config()/,/^}/p' scripts/run-local-tests.sh > "$TMP/avc.fn"
# shellcheck source=/dev/null
. "$TMP/avc.fn"

# sshpass double: iptables legs see 0 REJECTs; the jq projection leg
# returns the scripted router-side value (AVC_CUR).
cat > "$TMP/bin/sshpass" <<'EOF'
#!/usr/bin/env bash
case "$*" in
  *iptables*) echo 0 ;;
  *jq*)       echo "${AVC_CUR:-}" ;;
esac
exit 0
EOF
chmod +x "$TMP/bin/sshpass"

configure_mint() { echo "reasserted" >> "${CFGREC:?}"; }

export MINT_URL="http://10.99.99.2:8383"
export CFGREC="$TMP/configure_mint.calls"
expected=$(jq -nc "[{url:\"${MINT_URL}\",min_balance:0,balance_tolerance_percent:0,price_per_step:1,price_unit:\"sats\",purchase_min_steps:1}]")

: > "$CFGREC"
AVC_CUR="" assert_venue_config "t"
[ -s "$CFGREC" ] \
  && ok "unreadable/empty config drifts and re-asserts" \
  || bad "empty-config drift was NOT caught"

: > "$CFGREC"
extra_cur=$(jq -nc "${expected}[0:1] + [{url:\"https://other.example\",min_balance:0,balance_tolerance_percent:0,price_per_step:1,price_unit:\"sats\",purchase_min_steps:1}]")
AVC_CUR="$extra_cur" assert_venue_config "t"
[ -s "$CFGREC" ] \
  && ok "extra-mint append drift caught (the #299 shape: local mint first)" \
  || bad "extra-mint drift passed the gate"

: > "$CFGREC"
price_cur=$(jq -nc "${expected} | map(.price_per_step = 2)")
AVC_CUR="$price_cur" assert_venue_config "t"
[ -s "$CFGREC" ] \
  && ok "pricing-field drift caught" \
  || bad "pricing drift passed the gate"

: > "$CFGREC"
AVC_CUR="$expected" assert_venue_config "t"
[ ! -s "$CFGREC" ] \
  && ok "the singleton fixture passes with no re-assert (no false positive)" \
  || bad "false-positive drift on the exact fixture"

echo "suite: venue-virtual flow under the #286 trap (pgrep 0 + exit 1)"

# ssh double: dispatches on the remote command text.
cat > "$TMP/bin/ssh" <<'EOF'
#!/usr/bin/env bash
{ echo "SSH: $*"; } >> "${SSHREC:?}"
case "$*" in
  *'busy=0'*)          exit 1 ;;   # venue free
  *'pgrep -c qemu-system'*)
                       echo 0; exit 1 ;;  # #286: count 0 AND rc 1
  *dnsmasq*)           exit 1 ;;
  *'virtual-lab.py start-poc'*)
                       echo "lab up (double)"; exit 0 ;;
  *'virtual-lab.py stop-poc'*)
                       exit 0 ;;
  *'rev-parse HEAD'*)  echo "${FAKE_SHA:?}"; exit 0 ;;
  *'status --porcelain'*) exit 0 ;;
  *)                   exit 0 ;;
esac
EOF
chmod +x "$TMP/bin/ssh"

export SSHREC="$TMP/sshrec.args"
export PYREC="$TMP/pyrec3.args"
FAKE_SHA=$(git rev-parse HEAD)
export FAKE_SHA
: > "$SSHREC"; : > "$PYREC"
RES_BEFORE="$TMP/results-before.txt"; snapshot_results > "$RES_BEFORE"
# default IPK path must not exist under the $HOME double → deploy-local fallback
BRINGUP=1 TOLLGATE_VENUE=virtual TIERS="smoke" \
  TOLLGATE_PYTHON="$TMP/bin/py-rec" \
  bash scripts/extensive-test.sh > "$TMP/ext2.log" 2>&1
rc=$?
clean_new_results "$RES_BEFORE"

# #286: "0\n0" failed the -lt comparison and SKIPPED bring-up; the fix must
# REACH the bring-up call under the same trap conditions.
grep -q 'virtual-lab.py start-poc' "$SSHREC" \
  && ok "#286 trap: lab bring-up reached despite pgrep '0 + exit 1'" \
  || bad "#286 trap: bring-up was skipped (integer-expression class)"

# #290: the revision verification fired (rev-parse + porcelain)
grep -q 'rev-parse HEAD' "$SSHREC" \
  && ok "#290: remote revision check fired before tier delegation" \
  || bad "#290: no rev-parse verification call recorded"

# tier delegation happened (the tier ssh carries run-local-tests.sh)
grep -q 'run-local-tests.sh tests/' "$SSHREC" \
  && ok "tier delegated to run-local-tests.sh on the (doubled) lab host" \
  || bad "tier delegation not observed"

echo "suite: physical deploy shape (#288 fail-fast coupling, #289 lease wrap)"

cat > "$TMP/bin/scp" <<'EOF'
#!/usr/bin/env bash
{ echo "SCP: $*"; } >> "${SSHREC:?}"
exit 0
EOF
chmod +x "$TMP/bin/scp"

# python3 double for the hw-bench-lease call: records argv, exits 0
# (the lease is simulated — we assert the SHAPE of the wrapped command).
cat > "$TMP/bin/python3" <<'EOF'
#!/usr/bin/env bash
{ echo "LEASE:"; for a in "$@"; do printf 'L %s\n' "$a"; done; } >> "${SSHREC:?}"
exit 0
EOF
chmod +x "$TMP/bin/python3"

: > "$SSHREC"
: > "$TMP/fake-deploy.ipk"
RES_BEFORE="$TMP/results-before.txt"; snapshot_results > "$RES_BEFORE"
TOLLGATE_VENUE=physical TOLLGATE_SSH_HOST=127.0.0.1 \
  TOLLGATE_IPK="$TMP/fake-deploy.ipk" TIERS="" \
  TOLLGATE_PYTHON=/bin/true \
  bash scripts/extensive-test.sh > "$TMP/ext3.log" 2>&1
rc=$?
clean_new_results "$RES_BEFORE"
[ "$rc" -eq 0 ] \
  && ok "physical venue with doubles exits 0" \
  || bad "physical venue exited ${rc}: $(tail -2 "$TMP/ext3.log" | tr '\n' ' ')"

lease_seen=$(grep -c '^LEASE:$' "$SSHREC")
lease_cmd=$(grep '^L ' "$SSHREC" | sed 's/^L //' | tr '\n' ' ')
[ "$lease_seen" -ge 1 ] \
  && ok "#289: deploy ran inside hw-bench-lease exec" \
  || bad "no hw-bench-lease invocation recorded"
case "$lease_cmd" in
  *hw-bench-lease*--purpose*extensive-test_physical_deploy*--*bash*) \
    ok "#289: lease purpose + exec shape correct" ;;
  *) bad "lease call shape wrong: ${lease_cmd:0:160}" ;;
esac
case "$lease_cmd" in
  *'opkg install --force-overwrite /tmp/tollgate.ipk'*'&&'*'rm -f /etc/tollgate/wallet.db'*'&&'*) \
    ok "#288: opkg→wipe→restart chain is &&-coupled (fail-fast)" ;;
  *) bad "physical deploy chain not coupled: ${lease_cmd:0:200}" ;;
esac

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "extensive-test offline suite: ALL PASS"
  exit 0
fi
echo "extensive-test offline suite: ${FAILURES} FAILURE(S)"
exit 1
