#!/usr/bin/env bash
# extensive-test — one command for the TollGate extensive-test effort.
#
# Wraps venue bring-up (optional), tiered pytest runs, and artifact
# collection into a single entry point. Behavior maps live in
# docs/user-journeys.md (persona journeys × happy/failure/regression)
# and docs/user-stories.generated.md (story × test map — regenerate
# with scripts/story-coverage.py).
#
# Usage:
#   ./scripts/extensive-test.sh                     # virtual venue, full sweep
#   TOLLGATE_VENUE=physical ./scripts/extensive-test.sh
#   TIERS="smoke critical" ./scripts/extensive-test.sh
#   STORIES="G2 G4" ./scripts/extensive-test.sh     # journey-scoped (OR list)
#   PATHS="failure regression" ./scripts/extensive-test.sh
#
# Env:
#   TOLLGATE_VENUE   virtual (default) | physical | skip
#                    virtual: refuses politely if another lane drives the lab
#                    physical: assumes TOLLGATE_SSH_HOST already deployed
#   TOLLGATE_IPK     optional .ipk to deploy first (physical venue)
#   TIERS            pytest -m tier selector list (default: smoke critical extended)
#   STORIES          pytest --story selector list (optional)
#   PATHS            -m path-class filter: happy|failure|regression (optional)
#   BRINGUP=0        skip venue bring-up entirely (assume it is up)
set -uo pipefail

REPO_ROOT=$(git -C "$(dirname -- "$0")" rev-parse --show-toplevel)
cd "$REPO_ROOT"

VENUE="${TOLLGATE_VENUE:-virtual}"
TIERS="${TIERS:-smoke critical extended}"
STORIES="${STORIES:-}"
PATHS="${PATHS:-}"
PY="${TOLLGATE_PYTHON:-$HOME/.tollgate-test-venv/bin/python}"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="results/extensive-${STAMP}"
mkdir -p "$OUT/raw"

fail() { echo "extensive-test: $*" >&2; exit 1; }

# Read the Version field out of an .ipk's control member. Both container
# formats seen in the wild: the classic ar archive (members control.tar.*)
# and OpenWrt's tar.gz container (./control.tar.gz inside a gzip'd tar).
ipk_version() {
  local ipk="$1" magic v=""
  magic=$(head -c 2 "$ipk" | od -An -tx1 | tr -d ' \n')
  if [ "$magic" = "1f8b" ]; then
    v=$(tar -xzOf "$ipk" ./control.tar.gz 2>/dev/null | tar -xzO ./control 2>/dev/null \
      | sed -n 's/^Version: //p' | head -1)
  else
    v=$(ar p "$ipk" control.tar.gz 2>/dev/null | tar -xzO ./control 2>/dev/null \
      | sed -n 's/^Version: //p' | head -1)
    [ -n "$v" ] || v=$(ar p "$ipk" control.tar 2>/dev/null | tar -xO ./control 2>/dev/null \
      | sed -n 's/^Version: //p' | head -1)
  fi
  printf '%s\n' "$v"
}

tier_expr() {
  # tiers are hierarchical subsets: select the union
  echo "$TIERS"
}

# ---------------------------------------------------------------- venue ----
if [ "${BRINGUP:-1}" = "1" ] && [ "$VENUE" = "virtual" ]; then
  echo "== venue: virtual lab (ai-legion)"
  # Politeness: an ACTIVE omarchy lane (its QEMU, or a serial bridge whose
  # target socket exists) means the lab fabric is contended — yield. An
  # orphaned bridge/dnsmasq (VM gone, socket gone) is a known leftover:
  # warn and proceed (AGENTS 2026-09-28 partial-teardown class).
  # #287: a poc-lane run also drives its own tollgate-poc QEMUs — with no
  # serial-bridge client attached it still owns the poc VMs, and a second
  # run here would reset the shared mint/gateway state under it. Detect
  # the poc QEMU pattern AND the poc VM's ssh port before declaring free.
  if ssh -o BatchMode=yes -o ConnectTimeout=6 ai-legion '
      busy=0
      pgrep -af "qemu.*omarchy-v[m]" | grep -v pgrep >/dev/null && busy=1
      pgrep -af "qemu.*tollgate-po[c]" | grep -v pgrep >/dev/null && busy=1
      # a bridge with live clients = someone is attached to the poc console
      [ "$(ss -tn 2>/dev/null | grep -c ":7301")" -gt 0 ] && busy=1
      # the poc VM answering ssh = an active lane even if its qemu cmdline
      # was renamed or the process list raced
      timeout 6 bash -c "exec 3<>/dev/tcp/10.99.99.1/22" 2>/dev/null && busy=1
      [ "$busy" -eq 1"'; then
    fail "another lane is actively driving the ai-legion lab (omarchy/poc VM, live serial bridge, or poc VM ssh) — coordinate or use TOLLGATE_VENUE=physical|skip"
  fi
  ssh -o BatchMode=yes ai-legion \
      'pgrep -f "dnsmasq.*omarchy-tb" >/dev/null' 2>/dev/null \
      && echo "   note: orphaned omarchy dnsmasq present (no VM) — proceeding"
  # #286: pgrep -c prints 0 AND exits 1 when nothing matches — the old
  # '|| echo 0' appended a second line ("0\n0") and the -lt comparison
  # errored out as non-numeric, skipping bring-up against a dead lab.
  RUNNING=$(ssh -o BatchMode=yes ai-legion 'pgrep -c qemu-system' 2>/dev/null | head -1)
  RUNNING="${RUNNING:-0}"
  # FRESH_DUT=1 purges the DUT overlay before boot (issue #26: stop/start
  # reuses the overlay, so a corrupted DUT survives a restart); the default
  # keeps the reuse behavior for lanes that rely on a provisioned DUT.
  FRESH_FLAG=""
  [ "${FRESH_DUT:-0}" = "1" ] && FRESH_FLAG="--fresh"
  bring_up_lab() {
    echo "== starting lab (start-poc ${FRESH_FLAG:-reuse})…"
    ssh -o BatchMode=yes ai-legion \
      "sudo bash -c \"cd /root/src/physical-router-test-automation && HOME=/root python3 scripts/virtual-lab.py start-poc --host localhost ${FRESH_FLAG}\"" \
      2>&1 | tee "$OUT/bringup.log" | tail -3 \
      || fail "lab bring-up failed — see $OUT/bringup.log (issue #21 tracks known instability)"
  }
  if [ "${RUNNING:-0}" -lt 2 ]; then
    echo "== lab down (${RUNNING} qemu)"
    bring_up_lab
  elif [ -n "$FRESH_FLAG" ]; then
    echo "== FRESH_DUT=1 with the lab up (${RUNNING} qemu) — stopping for a fresh overlay…"
    ssh -o BatchMode=yes ai-legion \
      "sudo bash -c \"cd /root/src/physical-router-test-automation && HOME=/root python3 scripts/virtual-lab.py stop-poc\"" \
      2>&1 | tee "$OUT/bringup.log" | tail -3 \
      || fail "lab stop failed — see $OUT/bringup.log"
    bring_up_lab
  fi

wait_for_vm() {
  echo "== waiting for VM ssh (max 150s)…"
  for _ in $(seq 1 30); do
    if ssh -o BatchMode=yes ai-legion \
        'pgrep -f "qemu.*tollgate-po[c]" >/dev/null && timeout 6 bash -c "exec 3<>/dev/tcp/10.99.99.1/22" 2>/dev/null'; then
      return 0
    fi
    sleep 5
  done
  return 1
}

  wait_for_vm || fail "poc VM came up but SSH never answered (see bringup.log; issue #21)"
  # Fresh overlays are password-only and the baked password has drifted
  # from .env — inject the lab key over the serial console (chunked,
  # echo-verified) so every later step uses key auth.
  keyed=1
  attempt=0
  while [ "$attempt" -lt 3 ]; do
    attempt=$((attempt + 1))
    if ssh -o BatchMode=yes ai-legion \
        'sudo ssh -i /home/ubuntu/.ssh/id_ed25519 -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@10.99.99.1 true' 2>/dev/null; then
      keyed=0; break
    fi
    # console may still be under the provisioner's fingers — retry the
    # chunked key inject until ssh answers (issue #21: fresh overlays
    # also mean fresh host keys, so never pin them here)
    echo "== injecting lab key over serial console (attempt $attempt)…"
    scp -q "$REPO_ROOT/scripts/vm-keyinject.py" ai-legion:/tmp/ \
      && ssh -o BatchMode=yes ai-legion 'sudo python3 /tmp/vm-keyinject.py' | tail -1
    sleep 5
  done
  [ "$keyed" -eq 0 ] || fail "could not establish key auth to the poc VM after 3 attempts (console contention / issue #21)"
  # The ipk path is the deploy default: uci-defaults performs the full
  # NDS+portal setup, exactly how the cloud lab deploys.
  IPK="${TOLLGATE_IPK:-$HOME/src/tollgate-module-basic-go/packaging/tollgate-wrt_v0.6.0-rc1-local_x86_64.ipk}"
  if [ -f "$IPK" ]; then
    echo "== deploying $IPK to the VM"
    # #291: every leg of the deploy used to be unchecked — a failed scp
    # silently reused the previous /tmp ipk, a failed opkg install left
    # the OLD backend in place (the health gate then passed against it),
    # and nothing tied the deployed build to the candidate under test.
    # The chain is now: transfer md5 (build host -> lab -> VM), opkg exit
    # status, service restart, the e798aaab health gate, and an installed-
    # Version assertion against the ipk's own control member. The remote
    # half runs as a generated script file (argv: <md5> <version>) — one
    # quoting layer, no nested-escape blind spots.
    IPK_MD5=$(md5sum "$IPK" | cut -d' ' -f1)
    IPK_VERSION=$(ipk_version "$IPK")
    [ -n "$IPK_VERSION" ] || fail "could not read the Version control member from $IPK"
    scp -q "$IPK" ai-legion:/tmp/tollgate-extensive.ipk \
      || fail "scp of $IPK to ai-legion failed (lab reachable? /tmp full?)"
    REMOTE_MD5=$(ssh -o BatchMode=yes ai-legion \
      'md5sum /tmp/tollgate-extensive.ipk 2>/dev/null | cut -d" " -f1')
    [ "$REMOTE_MD5" = "$IPK_MD5" ] \
      || fail "ipk on ai-legion does not match the local $IPK (md5 ${REMOTE_MD5:-none} vs ${IPK_MD5}) — stale /tmp/tollgate-extensive.ipk? remove it and retry"
    cat > "$OUT/deploy-remote.sh" <<'REMOTE_DEPLOY'
#!/usr/bin/env bash
# Generated by scripts/extensive-test.sh — runs ON ai-legion as root.
# argv: <expected-ipk-md5> <expected-tollgate-wrt-version>
# Fails fast at every leg (#291); exits non-zero on any failure.
set -u
MD5_EXPECTED="${1:?expected md5}"
VERSION_EXPECTED="${2:?expected version}"
K=/home/ubuntu/.ssh/id_ed25519
SSHO=(-i "$K" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

scp -q -O "${SSHO[@]}" /tmp/tollgate-extensive.ipk root@10.99.99.1:/tmp/ \
  || { echo VM-TRANSFER-FAILED; exit 1; }

VM_MD5=$(ssh "${SSHO[@]}" root@10.99.99.1 \
  'md5sum /tmp/tollgate-extensive.ipk 2>/dev/null | cut -d" " -f1')
[ "${VM_MD5:-none}" = "$MD5_EXPECTED" ] \
  || { echo "VM-IPK-MD5-MISMATCH got=${VM_MD5:-none} want=$MD5_EXPECTED"; exit 1; }

# opkg failures must abort the chain: the old '... | tail -2; rm; restart'
# masked a failed install behind a successful restart of the OLD backend.
ssh "${SSHO[@]}" root@10.99.99.1 \
  'opkg update >/dev/null 2>&1; opkg install --force-overwrite /tmp/tollgate-extensive.ipk >/tmp/opkg-extensive.out 2>&1 && tail -2 /tmp/opkg-extensive.out && rm -f /etc/tollgate/wallet.db && /etc/init.d/tollgate-wrt restart && sleep 6 || { echo OPKG-DEPLOY-FAILED; tail -3 /tmp/opkg-extensive.out 2>/dev/null; exit 1; }' \
  || exit 1

# Post-deploy health gate: an aborted opkg transaction orphan-removes
# runtime deps (nodogsplash zeroed — tbmg fork#93 class); without this
# gate the portal is silently dead and every tier fails opaquely.
# Plus the build-identity leg (#291): the INSTALLED version must equal the
# candidate ipk's control version — a healthy OLD backend is not a pass.
ssh "${SSHO[@]}" root@10.99.99.1 '
  [ -s /usr/bin/nodogsplash ] || { echo NDS-BINARY-EMPTY; exit 1; }
  ndsctl status >/dev/null 2>&1 || { echo NDSCTL-DEAD; netstat -tln | grep 2050 || true; exit 1; }
  curl -s -m 5 http://127.0.0.1:2121/ | head -c 120; echo
  ver=$(opkg status tollgate-wrt 2>/dev/null | sed -n "s/^Version: //p" | head -1)
  [ "${ver:-none}" = "'"$VERSION_EXPECTED"'" ] || { echo "VERSION-MISMATCH installed=${ver:-none} expected='"$VERSION_EXPECTED"'"; exit 1; }
' || exit 1

echo DEPLOY-OK
REMOTE_DEPLOY
    scp -q "$OUT/deploy-remote.sh" ai-legion:/tmp/extensive-deploy.sh \
      || fail "could not stage the deploy script on ai-legion"
    ssh -o BatchMode=yes ai-legion \
        "sudo bash /tmp/extensive-deploy.sh '$IPK_MD5' '$IPK_VERSION'" \
      2>&1 | tee "$OUT/deploy.log" | tail -4 \
      || fail "deploy failed (transfer / opkg / restart / health gate / version) — see $OUT/deploy.log (retry with FRESH_DUT=1 for a wedged VM)"
  else
    echo "== no ipk at $IPK — falling back to deploy-local-vm.sh (needs VM key auth)"
    ssh -o BatchMode=yes ai-legion \
      'sudo bash -c "cd /root/src/physical-router-test-automation && bash scripts/deploy-local-vm.sh"' \
        2>&1 | tee "$OUT/deploy.log" | tail -3
  fi
elif [ "$VENUE" = "physical" ]; then
  echo "== venue: physical (${TOLLGATE_SSH_HOST:-TOLLGATE_SSH_HOST unset!})"
  [ -n "${TOLLGATE_SSH_HOST:-}" ] || fail "physical venue needs TOLLGATE_SSH_HOST"
  if [ -n "${TOLLGATE_IPK:-}" ]; then
    echo "== deploying $TOLLGATE_IPK (inside the machine-global bench lease)"
    # #289: this deploy block mutates the DUT and used to run BEFORE any
    # lock — the pytest tiers below take the venue/hardware lock, so a
    # concurrent lab user's router state could be overwritten before the
    # first lock was ever taken. The deploy now takes the SAME hardware
    # lock (scripts/hw-bench-lease — the one machine-global bench lease
    # lib/hardware_lock.py delegates to; exit 2 = BUSY, fail loudly).
    # #288: every leg fails fast — the old 'scp && ssh "install; rm;
    # restart" | tee | tail' masked opkg failures behind the restart and
    # never tested scp/ssh status; tiers then graded a release candidate
    # that was never deployed (the fatal deploy gate existed on the
    # VIRTUAL branch only). The remote chain is &&-coupled so an opkg
    # failure aborts before the wallet wipe / restart.
    python3 "$REPO_ROOT/scripts/hw-bench-lease" exec \
      --purpose "extensive-test_physical_deploy" -- \
      bash -c "scp -O '$TOLLGATE_IPK' 'root@${TOLLGATE_SSH_HOST}:/tmp/tollgate.ipk' \
        && ssh 'root@${TOLLGATE_SSH_HOST}' 'opkg install --force-overwrite /tmp/tollgate.ipk >/tmp/opkg-extensive.out 2>&1 && tail -2 /tmp/opkg-extensive.out && rm -f /etc/tollgate/wallet.db && /etc/init.d/tollgate-wrt restart || { echo OPKG-DEPLOY-FAILED; tail -3 /tmp/opkg-extensive.out 2>/dev/null; exit 1; }' \
        || { echo PHYSICAL-DEPLOY-FAILED; exit 1; }" \
      2>&1 | tee "$OUT/deploy.log" | tail -2 \
      || fail "physical deploy failed (bench lease busy / scp / opkg / restart) — see $OUT/deploy.log"
  fi
fi

# ----------------------------------------------------------------- runs ----
select_args=()
[ -n "$STORIES" ] && select_args+=(--story "$(echo "$STORIES" | tr ' ' ',')")
[ -n "${TIPS:-}" ] && select_args+=(--tip "$(echo "$TIPS" | tr ' ' ',')")
# #292: 'tr ' ' ' or '' maps single characters (space -> 'o'), producing
# 'smoke and (failureoregression)'. Join the parsed words on ' or ' via an
# array loop instead.
PATH_EXPR=""
if [ -n "$PATHS" ]; then
  read -r -a path_words <<< "$PATHS"
  PATH_EXPR=" and (${path_words[0]}"
  for p in "${path_words[@]:1}"; do
    PATH_EXPR+=" or ${p}"
  done
  PATH_EXPR+=")"
fi

run_tier_local() {
  # virtual venue: delegate to run-local-tests.sh ON the lab host — it owns
  # the venue env (fakewallet mint, VM ssh, client MAC). Flags pass through
  # to pytest; its junit lands at /tmp/local-suite-junit.xml.
  local tier="$1"
  echo "== tier: $tier (virtual, via run-local-tests.sh) ${STORIES:+stories=$STORIES }${PATHS:+paths=$PATHS}"
  local rc=0
  # #290: the remote run used to execute whatever checkout happened to sit
  # at /root/src/physical-router-test-automation — a green run then
  # validated STALE framework code, not the revision under review. Verify
  # the remote HEAD is this revision before invoking pytest (and that its
  # tree is clean — a dirty tree is not the revision either). Sync it
  # explicitly before rerunning: sudo git -C <remote> fetch origin && sudo
  # git -C <remote> checkout <sha> (the caller's sha must be pushed first).
  local local_sha remote_sha remote_dirty
  local_sha=$(git rev-parse HEAD)
  remote_sha=$(ssh -o BatchMode=yes ai-legion \
    'sudo git -C /root/src/physical-router-test-automation rev-parse HEAD' 2>/dev/null)
  remote_dirty=$(ssh -o BatchMode=yes ai-legion \
    'sudo git -C /root/src/physical-router-test-automation status --porcelain | head -1' 2>/dev/null)
  [ "$remote_sha" = "$local_sha" ] \
    || fail "remote checkout on ai-legion is ${remote_sha:-unreachable}, this run is ${local_sha} — sync the lab checkout (sudo git -C /root/src/physical-router-test-automation fetch origin && sudo git -C /root/src/physical-router-test-automation checkout ${local_sha}; push the sha first if it is local-only)"
  [ -z "$remote_dirty" ] \
    || fail "remote checkout on ai-legion has a dirty working tree (${remote_dirty}) — the run would test uncommitted state; clean it (sudo git -C /root/src/physical-router-test-automation stash/reset) and rerun"
  # Evidence integrity: a tier that dies before pytest writes junit must not
  # scoop a previous run's file (2026-10-13: week-old junit was presented as
  # current results). Remove before running; warn when no fresh junit lands.
  ssh -o BatchMode=yes ai-legion 'sudo rm -f /tmp/local-suite-junit.xml'
  ssh -o BatchMode=yes ai-legion \
    "sudo bash -c 'cd /root/src/physical-router-test-automation && \
     scripts/run-local-tests.sh tests/ -m \"${tier}${PATH_EXPR}\" ${select_args[*]:-}'" \
      2>&1 | tee "$OUT/raw/output-${tier}.log" | tail -3 || rc=1
  ssh -o BatchMode=yes ai-legion 'sudo cat /tmp/local-suite-junit.xml 2>/dev/null' \
      > "$OUT/raw/junit-${tier}.xml" || true
  [ -s "$OUT/raw/junit-${tier}.xml" ] \
      || echo "extensive-test: WARNING: no fresh junit for tier '$tier' (died before pytest?)" >&2
  return $rc
}

run_tier_physical() {
  local tier="$1"
  echo "== tier: $tier ${STORIES:+stories=$STORIES }${PATHS:+paths=$PATHS}"
  # TOLLGATE_VENUE=physical would pin pytest to the legacy env path — 'auto'
  # lets the venue take the DUT place + hardware lock on configured hosts
  # (lane choice lives in $VENUE above, not in the child env).
  TOLLGATE_VENUE=auto "$PY" -m pytest tests/ -m "${tier}${PATH_EXPR}" \
    "${select_args[@]}" \
    -q --tb=short \
    --junitxml="$OUT/raw/junit-${tier}.xml" \
    2>&1 | tee "$OUT/raw/output-${tier}.log" | tail -2
}

run_tier() {
  # keep going: one tier failing must not hide the others; verdict at the end
  if [ "$VENUE" = "virtual" ]; then run_tier_local "$1" || return 1
  else run_tier_physical "$1" || return 1; fi
}

RC=0
for tier in $TIERS; do run_tier "$tier" || RC=1; done

# ------------------------------------------------------------- artifacts ----
"$PY" scripts/story-coverage.py >/dev/null && cp docs/user-stories.generated.md "$OUT/"
cp docs/user-journeys.md "$OUT/" 2>/dev/null || true

PASS=$(grep -ho 'passed' "$OUT"/raw/output-*.log | wc -l)
echo
echo "== done → $OUT"
echo "   story map + journey map copied; junit + logs under raw/"
echo "   verdict: $([ $RC -eq 0 ] && echo GREEN || echo 'RED (see per-tier logs)')"
exit $RC
