#!/usr/bin/env bash
# Phase S: Spilman signer sidecar on the router (labgrid vehicle)
#
# Proves the signer sidecar works ON an OpenWrt router, not just on the host that
# compiled it. Motivated by AGENTS.md's 81-kicad-headless lesson: a green host
# suite and a passing syntax check shipped a role that installed the wrong major
# version and could never upgrade. Cross-compiling a static musl binary for a
# device with a different libc is exactly the class where "cargo test passed on
# x86_64" is not evidence for the device.
#
# HONEST SCOPE: this proves the SIGNER SEGMENT on the router — the binary starts,
# enforces key custody, binds its socket, authenticates a client, and produces a
# tweaked signature that verifies against an independently derived key. It does
# NOT prove a purchase, a swap, a mint call, or settlement. The Go module has no
# /spilman/close route yet, so nothing here settles a channel.
#
# Artifact binding: the sha256 of the deployed binary is asserted against the
# sha256 of the built artifact. A "same source" claim is not accepted.
#
# Usage:
#   bash scripts/0.6.0-validation/phase-s-signer-sidecar.sh \
#        --sidecar-bin PATH --probe-bin PATH [--router HOST]
#
# Exit: 0 pass, 1 fail, 77 skip (no target or artifact) — a skip is NOT a pass.
set -uo pipefail

PASS=0
FAIL=0
ok()  { echo "PASS: $*"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL+1)); }
log() { echo "  ..   $*"; }
skip() { echo "SKIP: $*"; }

EV=${EV:-/tmp/phase-s}
mkdir -p "$EV"

SIDECAR_BIN=${SPILMAN_SIGNER_BIN:-}
PROBE_BIN=${SPILMAN_PROBE_BIN:-}
ROUTER=${SPILMAN_ROUTER_HOST:-10.99.99.1}
REMOTE_DIR=/tmp/spilman-signer-phase-s
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

while [ $# -gt 0 ]; do
    case "$1" in
        --sidecar-bin) SIDECAR_BIN=$2; shift 2 ;;
        --probe-bin)   PROBE_BIN=$2; shift 2 ;;
        --router)      ROUTER=$2; shift 2 ;;
        -h|--help)     sed -n '2,26p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

# ── 0. Credentials ──────────────────────────────────────────────────
# The virtual lab generates a per-run password. It lives in the harness's
# credentials file, which is gitignored — so it is absent from a fresh worktree
# by design. Resolve it from the PRTA checkout that owns this script, and never
# echo it. A physical bench injects a key instead and needs none of this.
find_creds() {
    local c
    for c in "${PRTA_CREDENTIALS:-}" \
             "$HERE/../../credentials/virtual-lab-credentials.json" \
             "$(git -C "$HERE/../.." rev-parse --git-common-dir 2>/dev/null | xargs -r dirname)/credentials/virtual-lab-credentials.json" \
             "$HOME/repos/physical-router-test-automation/credentials/virtual-lab-credentials.json"; do
        [ -n "$c" ] && [ -f "$c" ] && { printf %s "$c"; return 0; }
    done
    return 1
}
if [ -z "${TG_SHIM_PW:-}" ]; then
    if CREDS=$(find_creds); then
        TG_SHIM_PW=$(python3 -c "import json;print(json.load(open('$CREDS'))['password'])" 2>/dev/null)
    fi
fi

RSSH() {
    if [ -n "${TG_SHIM_PW:-}" ] && command -v sshpass >/dev/null 2>&1; then
        sshpass -p "$TG_SHIM_PW" ssh -o BatchMode=no -o StrictHostKeyChecking=no \
            -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 \
            "root@$ROUTER" "$@"
    else
        ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
            "root@$ROUTER" "$@"
    fi
}
RCOPY() {
    if [ -n "${TG_SHIM_PW:-}" ] && command -v sshpass >/dev/null 2>&1; then
        sshpass -p "$TG_SHIM_PW" scp -O -q -o StrictHostKeyChecking=no \
            -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$@"
    else
        scp -O -q -o BatchMode=yes "$@"
    fi
}

# ── 1. Target identity ──────────────────────────────────────────────
echo "=== target ==="
if ! ARCH=$(RSSH 'uname -m' 2>/dev/null); then
    skip "cannot reach $ROUTER (bench down?) — no router evidence is claimed"
    exit 77
fi
REL=$(RSSH '. /etc/openwrt_release 2>/dev/null; printf %s "${DISTRIB_RELEASE:-unknown}"')
ERA=$(RSSH 'command -v apk >/dev/null 2>&1 && echo apk || echo not-apk')
log "arch=$ARCH release=$REL pkgmgr=$ERA"

# Read the arch instead of asserting one. A lane that hardcodes aarch64 can only
# ever run on one board; this must cover the x86-64 VM lane and the aarch64 bench.
case "$ARCH" in
    aarch64|arm64) TARGET=aarch64-unknown-linux-musl ;;
    x86_64|amd64)  TARGET=x86_64-unknown-linux-musl ;;
    *) bad "unsupported target arch: $ARCH"; echo; echo "RESULT: FAIL"; exit 1 ;;
esac
ok "target arch $ARCH -> $TARGET"

if [ "$ERA" = "apk" ]; then
    ok "apk era (25.x, the only feature target)"
else
    # 24.10/ipk is the frozen compat lane: security and stop-ship only.
    bad "not the feature-target era ($ERA) — a new sidecar is a feature, not a stop-ship fix"
fi

# ── 2. Artifact ─────────────────────────────────────────────────────
echo "=== artifact ==="
if [ -z "$SIDECAR_BIN" ] || [ ! -f "$SIDECAR_BIN" ]; then
    skip "no --sidecar-bin (build with: cargo build --release --target $TARGET --bin spilman-signer-sidecar)"
    exit 77
fi
LOCAL_SHA=$(sha256sum "$SIDECAR_BIN" | cut -d' ' -f1)
log "local sha256=$LOCAL_SHA"

RSSH "rm -rf $REMOTE_DIR; mkdir -p $REMOTE_DIR" || { bad "cannot prepare $REMOTE_DIR"; exit 1; }
RCOPY "$SIDECAR_BIN" "root@$ROUTER:$REMOTE_DIR/spilman-signer-sidecar" \
    || { bad "scp of sidecar failed"; exit 1; }
RSSH "chmod 755 $REMOTE_DIR/spilman-signer-sidecar"
REMOTE_SHA=$(RSSH "sha256sum $REMOTE_DIR/spilman-signer-sidecar" | cut -d' ' -f1)
[ "$LOCAL_SHA" = "$REMOTE_SHA" ] \
    && ok "deployed binary byte-identical (sha256 ${REMOTE_SHA:0:16}…)" \
    || bad "sha256 MISMATCH: local=$LOCAL_SHA remote=$REMOTE_SHA"

# ── 3. Key custody on the target filesystem ─────────────────────────
echo "=== key custody ==="
RSSH "printf '{\"secret_hex\":\"%s\",\"mac_key_hex\":\"%s\"}\n' \
  '0101010101010101010101010101010101010101010101010101010101010101' \
  '0202020202020202020202020202020202020202020202020202020202020202' \
  > $REMOTE_DIR/k.json && chmod 644 $REMOTE_DIR/k.json"
RSSH "$REMOTE_DIR/spilman-signer-sidecar --socket $REMOTE_DIR/x.sock --key-file $REMOTE_DIR/k.json" \
    > "$EV/loose-key.txt" 2>&1
LOOSE_RC=$?
[ $LOOSE_RC -ne 0 ] && ok "a 0644 key file is refused (exit $LOOSE_RC)" \
                    || bad "a 0644 key file was ACCEPTED"
grep -q 'chmod 600' "$EV/loose-key.txt" \
    && log "refusal names the remedy" \
    || bad "refusal does not name the remedy (undiagnosable)"

RSSH "chmod 600 $REMOTE_DIR/k.json"
PUB=$(RSSH "$REMOTE_DIR/spilman-signer-sidecar --key-file $REMOTE_DIR/k.json --print-pubkey")
[ -n "$PUB" ] && ok "--print-pubkey works (${PUB:0:16}…)" || bad "--print-pubkey failed"

# ── 4. Protocol + signing, driven by a CGO-free Go client on the DUT ─
echo "=== protocol + signing on target ==="
if [ -z "$PROBE_BIN" ] || [ ! -f "$PROBE_BIN" ]; then
    bad "no --probe-bin; the protocol segment cannot be claimed"
    echo
    echo "=== summary ==="
    echo "$PASS passed, $FAIL failed  (evidence: $EV)"
    echo "RESULT: FAIL (incomplete — the protocol segment is unproven)"
    exit 1
fi
RCOPY "$PROBE_BIN" "root@$ROUTER:$REMOTE_DIR/ontarget-probe" || { bad "scp of probe failed"; exit 1; }
RSSH "chmod 755 $REMOTE_DIR/ontarget-probe"

RSSH "set -u
  D=$REMOTE_DIR
  rm -f \$D/s.sock
  \$D/spilman-signer-sidecar --socket \$D/s.sock --key-file \$D/k.json &
  SRV=\$!
  i=0; while [ ! -S \$D/s.sock ] && [ \$i -lt 60 ]; do sleep 1; i=\$((i+1)); done
  ls -l \$D/s.sock | cut -c1-10
  SIDECAR_SOCKET=\$D/s.sock SIDECAR_KEY_FILE=\$D/k.json \$D/ontarget-probe
  echo \"PROBE_EXIT=\$?\"
  kill \$SRV 2>/dev/null
" | tee "$EV/ontarget.log"
PROBE_RC=$(sed -n 's/^PROBE_EXIT=\([0-9]*\)$/\1/p' "$EV/ontarget.log" | tail -1)
[ "${PROBE_RC:-1}" = "0" ] && ok "on-target probe exit 0" || bad "on-target probe exit ${PROBE_RC:-?}"
grep -q '^PASS: verifies under tweaked key' "$EV/ontarget.log" \
    && ok "signature verifies against an independently derived tweaked key" \
    || bad "tweaked-key verification missing"
grep -q 'does NOT verify under untweaked key' "$EV/ontarget.log" \
    && ok "signature does not verify under the untweaked key" \
    || bad "tweak-application not proven"

# ── 5. Cleanup ──────────────────────────────────────────────────────
RSSH "rm -rf $REMOTE_DIR" >/dev/null 2>&1
log "test key material removed from the target"

echo
echo "=== summary ==="
echo "$PASS passed, $FAIL failed  (evidence: $EV)"
if [ $FAIL -eq 0 ]; then
    echo "RESULT: PASS"
    echo "SCOPE: signer segment on the router only. No purchase, no settlement,"
    echo "       no mint call — see the header."
    exit 0
fi
echo "RESULT: FAIL"
exit 1
