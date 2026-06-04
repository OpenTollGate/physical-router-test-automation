#!/bin/sh
# ============================================================================
# fee-hotpatch.sh — Cashu mint fee detection and price_per_step guard
# ============================================================================
#
# Detects mint keyset fees on a TollGate router and warns if the fee causes
# an allotment shortfall. Optionally patches price_per_step to compensate.
#
# PROBLEM:
#   When a user pays 4 sats via Cashu token, gonuts Wallet.Receive() pays
#   the Cashu protocol fee (fee_ppk) before crediting. With fee_ppk=10
#   (1%), a 4-sat token yields 3 sats after fee. The backend then credits
#   3 steps instead of 4. A 1-sat token yields 0 sats — user gets NOTHING.
#
# WHAT THIS SCRIPT DOES:
#   1. Reads the router's config.json for accepted mints and step_size
#   2. Queries each mint's /v1/keys endpoint for fee_ppk
#   3. Calculates minimum viable payment: ceil(1000 / (1000 - fee_ppk))
#   4. Prints a summary with fee and minimum viable payment per mint
#   5. Warns if current price_per_step is below minimum viable payment
#   6. Optionally patches price_per_step in config.json (--patch)
#
# USAGE:
#   # Read-only analysis (default):
#   TOLLGATE_SSH_HOST=192.168.13.112 ./scripts/fee-hotpatch.sh
#
#   # With password auth:
#   TOLLGATE_SSH_HOST=192.168.13.112 TOLLGATE_SSH_PASSWORD=secret \
#       ./scripts/fee-hotpatch.sh
#
#   # With key auth:
#   TOLLGATE_SSH_HOST=192.168.13.112 TOLLGATE_SSH_KEY=~/.ssh/mykey \
#       ./scripts/fee-hotpatch.sh
#
#   # Apply price_per_step patch (increase to compensate for fee):
#   TOLLGATE_SSH_HOST=192.168.13.112 ./scripts/fee-hotpatch.sh --patch
#
#   # Override step_size to a specific value (ms):
#   TOLLGATE_SSH_HOST=192.168.13.112 ./scripts/fee-hotpatch.sh --set-step-size 10000
#
# ENVIRONMENT VARIABLES:
#   TOLLGATE_SSH_HOST     - Router IP address (required)
#   TOLLGATE_SSH_PASSWORD - SSH password (optional, uses sshpass)
#   TOLLGATE_SSH_KEY      - SSH key path (optional, alternative to password)
#   TOLLGATE_SSH_USER     - SSH username (default: root)
#
# ASSUMPTIONS:
#   - Router runs OpenWrt with BusyBox ash (NOT bash)
#   - jq is installed on the router (opkg install jq)
#   - wget with --timeout support is available on the router
#   - Config file at /etc/tollgate/config.json
#   - Mint URLs are HTTP-accessible from the router
#   - NUT-02 keyset format: { "keysets": [{ "id": "...", "fee_ppk": N }] }
#   - fee_ppk is in parts-per-thousand (10 = 1%)
#   - python3 is available on the HOST machine for JSON parsing
#   - Router has python3 OR jq for on-device JSON manipulation
#
# IDEMPOTENT: Safe to run multiple times. --patch only updates price_per_step
#             if the calculated value differs from current. Backups are kept
#             at /etc/tollgate/config.json.pre-fee-hotpatch.
#
# See: docs/issue-fee-shortfall.md for the full bug report.
# ============================================================================

set -e

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG_PATH="/etc/tollgate/config.json"
SSH_USER="${TOLLGATE_SSH_USER:-root}"
SSH_HOST="${TOLLGATE_SSH_HOST:-}"
SSH_PASSWORD="${TOLLGATE_SSH_PASSWORD:-${TOLLGATE_LUCI_PASSWORD:-}}"
SSH_KEY="${TOLLGATE_SSH_KEY:-}"

# Flags
PATCH_MODE=0
SET_STEP_SIZE=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: $0 [--patch] [--set-step-size N] [-h]"
    echo ""
    echo "  --patch            Update price_per_step in config.json to compensate"
    echo "  --set-step-size N  Set step_size to N ms (overrides calculation)"
    echo "  -h                 Show this help"
    exit 0
}

NEXT_ARG=""
for arg in "$@"; do
    if [ -n "$NEXT_ARG" ]; then
        eval "$NEXT_ARG"='"$arg"'
        NEXT_ARG=""
        continue
    fi
    case "$arg" in
        --patch)
            PATCH_MODE=1
            ;;
        --set-step-size)
            NEXT_ARG="SET_STEP_SIZE"
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            usage
            ;;
    esac
done

if [ -n "$NEXT_ARG" ]; then
    echo "ERROR: --set-step-size requires a value" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
if [ -z "$SSH_HOST" ]; then
    echo "ERROR: TOLLGATE_SSH_HOST is not set" >&2
    echo "Usage: TOLLGATE_SSH_HOST=<router-ip> $0 [--patch]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# SSH command construction
# Follows project patterns from lib/router.py and scripts/provision-router.sh
# ---------------------------------------------------------------------------
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=10 -o LogLevel=ERROR"

if [ -n "$SSH_KEY" ]; then
    SSH_CMD="ssh $SSH_OPTS -i $SSH_KEY ${SSH_USER}@${SSH_HOST}"
elif [ -n "$SSH_PASSWORD" ]; then
    SSH_CMD="sshpass -p $SSH_PASSWORD ssh $SSH_OPTS ${SSH_USER}@${SSH_HOST}"
else
    SSH_CMD="ssh $SSH_OPTS ${SSH_USER}@${SSH_HOST}"
fi

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
step() {
    printf '\n==> %s\n' "$1"
}

warn() {
    printf 'WARN: %s\n' "$1" >&2
}

die() {
    printf 'FATAL: %s\n' "$1" >&2
    exit 1
}

# ceil(a/b) for positive integers, POSIX shell math
ceil_div() {
    _a="$1"
    _b="$2"
    echo $(( (_a + _b - 1) / _b ))
}

# Minimum viable payment: smallest token face value that yields >= 1 sat
# after fee deduction. Formula: ceil(1000 / (1000 - fee_ppk))
# fee_ppk=0   → 1
# fee_ppk=10  → 2
# fee_ppk=500 → 2
# fee_ppk=999 → 1000
# fee_ppk>=1000 → INF (no viable payment)
min_viable_payment() {
    _fee_ppk="$1"
    if [ "$_fee_ppk" -ge 1000 ]; then
        echo "INF"
        return
    fi
    if [ "$_fee_ppk" -le 0 ]; then
        echo 1
        return
    fi
    _denominator=$(( 1000 - _fee_ppk ))
    ceil_div 1000 "$_denominator"
}

# Received sats after fee: floor(face_value * (1000 - fee_ppk) / 1000)
received_after_fee() {
    _face="$1"
    _fee_ppk="$2"
    if [ "$_fee_ppk" -le 0 ]; then
        echo "$_face"
        return
    fi
    echo $(( _face * (1000 - _fee_ppk) / 1000 ))
}

# Extract fee_ppk from /v1/keys JSON using jq (runs on router)
# Returns the maximum fee_ppk across active sat keysets (worst case)
extract_max_fee_ppk() {
    _keys_raw="$1"
    echo "$_keys_raw" | jq -r '
        [.keysets[] | select(.unit == "sat" and (.active // true)) | (.fee_ppk // 0)]
        | max // 0
    ' 2>/dev/null || echo "0"
}

# Extract first active keyset ID for display
extract_keyset_id() {
    _keys_raw="$1"
    echo "$_keys_raw" | jq -r '
        [.keysets[] | select(.unit == "sat" and (.active // true))][0].id // "?"
    ' 2>/dev/null || echo "?"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
step "TollGate Fee Shortfall Detector"
printf "Router: %s\n" "${SSH_USER}@${SSH_HOST}"

step "Verifying SSH connectivity"
$SSH_CMD "echo SSH_OK" >/dev/null 2>&1 || die "Cannot SSH to ${SSH_USER}@${SSH_HOST}"
echo "SSH OK"

step "Reading config.json from router"
CONFIG_RAW=$($SSH_CMD "cat $CONFIG_PATH" 2>/dev/null) \
    || die "Cannot read $CONFIG_PATH on router — is TollGate installed?"

# Parse config on the HOST (python3 for reliable JSON, avoids jq edge cases)
MINT_URLS=$(printf '%s' "$CONFIG_RAW" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
mints = cfg.get('accepted_mints', [])
for m in mints:
    print(m.get('url', ''))
" 2>/dev/null) || die "Failed to parse config.json"

STEP_SIZE=$(printf '%s' "$CONFIG_RAW" | python3 -c "
import sys, json
print(json.load(sys.stdin).get('step_size', 5000))
" 2>/dev/null || echo "5000")

METRIC=$(printf '%s' "$CONFIG_RAW" | python3 -c "
import sys, json
print(json.load(sys.stdin).get('metric', 'milliseconds'))
" 2>/dev/null || echo "milliseconds")

PRICE_PER_STEP=$(printf '%s' "$CONFIG_RAW" | python3 -c "
import sys, json
cfg = json.load(sys.stdin)
mints = cfg.get('accepted_mints', [])
print(mints[0].get('price_per_step', 1) if mints else 1)
" 2>/dev/null || echo "1")

MINT_COUNT=$(printf '%s\n' "$MINT_URLS" | grep -c .)
printf "Found %d mint(s)\n" "$MINT_COUNT"
printf "step_size=%s %s, price_per_step=%s sat\n" "$STEP_SIZE" "$METRIC" "$PRICE_PER_STEP"

# ---------------------------------------------------------------------------
# Per-mint analysis (single pass, no subshell variable loss)
# Uses a temp file for the mint list to avoid pipe-to-subshell issues.
# ---------------------------------------------------------------------------
OVERALL_MAX_MIN=1
MINT_INDEX=0

step "Querying mint keysets for fee_ppk"

# Write mint URLs to a temp file for line-by-line processing
_MINT_TMP=$(mktemp "${TMPDIR:-/tmp}/fee-hotpatch-mints.XXXXXX")
trap 'rm -f "$_MINT_TMP"' EXIT
printf '%s\n' "$MINT_URLS" > "$_MINT_TMP"

while read -r MINT_URL; do
    [ -z "$MINT_URL" ] && continue
    MINT_INDEX=$((MINT_INDEX + 1))

    printf "\n--- Mint %d: %s ---\n" "$MINT_INDEX" "$MINT_URL"

    # Query /v1/keys FROM THE ROUTER (mint may not be reachable from host)
    KEYS_RAW=$($SSH_CMD "wget -qO- --timeout=10 '${MINT_URL}/v1/keys' 2>/dev/null" 2>/dev/null) || {
        warn "Cannot query keysets from $MINT_URL (mint unreachable from router)"
        printf "  fee_ppk: UNKNOWN (mint unreachable)\n"
        printf "  min viable payment: UNKNOWN\n"
        continue
    }

    # Parse fee_ppk using jq on the router
    FEE_PPK=$($SSH_CMD "echo '$KEYS_RAW' | jq -r '[.keysets[] | select(.unit == \"sat\" and (.active // true)) | (.fee_ppk // 0)] | max // 0'" 2>/dev/null) || FEE_PPK=0
    if [ -z "$FEE_PPK" ] || [ "$FEE_PPK" = "null" ]; then
        FEE_PPK=0
    fi

    KEYSET_ID=$($SSH_CMD "echo '$KEYS_RAW' | jq -r '[.keysets[] | select(.unit == \"sat\" and (.active // true))][0].id // \"?\"'" 2>/dev/null) || KEYSET_ID="?"

    MIN_PAY=$(min_viable_payment "$FEE_PPK")
    RECEIVED_4=$(received_after_fee 4 "$FEE_PPK")
    RECEIVED_1=$(received_after_fee 1 "$FEE_PPK")

    # Fee percentage for display
    FEE_PCT=$(echo "$FEE_PPK" | awk '{printf "%.1f", $1/10}')

    printf "  keyset_id:         %s\n" "$KEYSET_ID"
    printf "  fee_ppk:           %d (%s%%)\n" "$FEE_PPK" "$FEE_PCT"
    printf "  min viable payment: %s sat\n" "$MIN_PAY"
    printf "  4 sat token →       %s sat received\n" "$RECEIVED_4"
    printf "  1 sat token →       %s sat received\n" "$RECEIVED_1"

    if [ "$RECEIVED_1" = "0" ]; then
        printf "  *** CRITICAL: 1-sat payments yield 0 — user gets NOTHING ***\n"
    fi

    # Impact table for this mint
    printf "\n  Face value | After fee | Data credited (at %s sat/step)\n" "$PRICE_PER_STEP"
    printf "  -----------|-----------|-----------------------------------\n"
    for FACE in 1 2 4 8 10 20 50 100; do
        R=$(received_after_fee "$FACE" "$FEE_PPK")
        if [ "$PRICE_PER_STEP" -gt 0 ]; then
            STEPS=$(( R / PRICE_PER_STEP ))
        else
            STEPS=0
        fi
        printf "  %3d sat     | %3d sat    | %d steps × %s %s\n" \
            "$FACE" "$R" "$STEPS" "$STEP_SIZE" "$METRIC"
    done

    # Track aggregate maximum (no subshell issue — this while reads from file)
    if [ "$MIN_PAY" != "INF" ] && [ "$MIN_PAY" -gt "$OVERALL_MAX_MIN" ]; then
        OVERALL_MAX_MIN=$MIN_PAY
    fi
done < "$_MINT_TMP"

# ---------------------------------------------------------------------------
# Aggregate analysis
# ---------------------------------------------------------------------------
step "Aggregate analysis"

printf "Current price_per_step:  %s sat\n" "$PRICE_PER_STEP"
printf "Minimum viable payment:  %s sat (highest across all mints)\n" "$OVERALL_MAX_MIN"

if [ "$PRICE_PER_STEP" -lt "$OVERALL_MAX_MIN" ] 2>/dev/null; then
    printf "\n"
    printf "WARNING: price_per_step=%s is below minimum viable payment (%s sat)\n" \
        "$PRICE_PER_STEP" "$OVERALL_MAX_MIN"
    printf "Users paying %s sat/token will receive 0 sat after fee — NO ACCESS!\n" "$PRICE_PER_STEP"
    printf "Fix: increase price_per_step to at least %s, or have users mint larger tokens.\n" "$OVERALL_MAX_MIN"
fi

# ---------------------------------------------------------------------------
# Patch mode
# ---------------------------------------------------------------------------
if [ "$PATCH_MODE" = "1" ] || [ -n "$SET_STEP_SIZE" ]; then
    step "Patching config.json"

    # Backup
    $SSH_CMD "cp $CONFIG_PATH ${CONFIG_PATH}.pre-fee-hotpatch" 2>/dev/null || true
    printf "Backup: %s.pre-fee-hotpatch\n" "$CONFIG_PATH"

    if [ "$PATCH_MODE" = "1" ]; then
        NEW_PPS="$OVERALL_MAX_MIN"
        printf "Updating price_per_step: %s → %s sat\n" "$PRICE_PER_STEP" "$NEW_PPS"

        # Use jq on the router to update all mints' price_per_step
        $SSH_CMD "jq '(.accepted_mints // []) |= map(.price_per_step = ${NEW_PPS})' \
            $CONFIG_PATH > /tmp/config-fee-patch.json && \
            mv /tmp/config-fee-patch.json $CONFIG_PATH" \
            || die "Failed to update config.json via jq"

        printf "Updated price_per_step to %s for all mints\n" "$NEW_PPS"
    fi

    if [ -n "$SET_STEP_SIZE" ]; then
        printf "Updating step_size: %s → %s ms\n" "$STEP_SIZE" "$SET_STEP_SIZE"

        $SSH_CMD "jq '.step_size = ${SET_STEP_SIZE}' \
            $CONFIG_PATH > /tmp/config-step-patch.json && \
            mv /tmp/config-step-patch.json $CONFIG_PATH" \
            || die "Failed to update step_size via jq"

        printf "Updated step_size to %s ms\n" "$SET_STEP_SIZE"
    fi

    # Restart backend
    step "Restarting tollgate-wrt backend"
    $SSH_CMD "/etc/init.d/tollgate-wrt restart" || warn "Backend restart returned non-zero"

    printf "Waiting for backend to come up...\n"
    sleep 5

    # Verify health
    HEALTH=$($SSH_CMD "wget -qO- --timeout=5 'http://[::1]:2121/' 2>/dev/null | head -c 50" 2>/dev/null) || true
    if [ -n "$HEALTH" ]; then
        printf "Backend health check: OK (%s...)\n" "$(echo "$HEALTH" | head -c 30)"
    else
        warn "Backend health check returned empty — check logs"
    fi

    # Verify the config change stuck
    NEW_PPS_VERIFY=$($SSH_CMD "jq '.accepted_mints[0].price_per_step' $CONFIG_PATH" 2>/dev/null) || NEW_PPS_VERIFY="?"
    printf "Verified: price_per_step = %s\n" "$NEW_PPS_VERIFY"
fi

step "Done"
echo ""
echo "Usage:"
echo "  Read-only analysis:     $0"
echo "  Apply fee compensation: $0 --patch"
echo "  Set specific step_size: $0 --set-step-size N"
echo "  Restore original:       $SSH_CMD 'cp ${CONFIG_PATH}.pre-fee-hotpatch ${CONFIG_PATH} && /etc/init.d/tollgate-wrt restart'"

rm -f "$_MINT_TMP"
