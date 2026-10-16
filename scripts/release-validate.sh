#!/usr/bin/env bash
# release-validate — one command from a feed tag to a validated verdict.
#
# Codifies the 2026-10-13/14 campaign loop: resolve the newest
# FreedomTechFeed/packages release (runbook §0), resolve its source commit
# (the feed ipk's binary version stamp), build x86_64 from that commit,
# ensure the lean venue (br-laneA on ai-legion-small — wired client, no lab
# WiFi per the hard rule), deploy with the health gate, and run the payment
# rejection suite.
#
# Usage:
#   scripts/release-validate.sh                  # newest feed tag, full loop
#   scripts/release-validate.sh --tag v0.6.0-rc5
#   scripts/release-validate.sh --ipk PATH       # skip build+resolve, deploy given ipk
#   scripts/release-validate.sh --skip-suite     # deploy + gate only
#
# Notes:
# - Self-launches under a transient systemd --user unit so the run survives
#   the invoking session (the nohup-reaping lesson, 2026-10-14).
# - The venue is Ethernet-only: the paying client is the lab host itself on
#   the DUT bridge. Lab host WiFi is management and is NEVER touched.
set -uo pipefail

REPO_ROOT=$(git -C "$(dirname -- "$0")" rev-parse --show-toplevel)
cd "$REPO_ROOT"

if [ "${TOLLGATE_RV_IN_SYSTEMD:-}" != "1" ] && [ -n "${XDG_RUNTIME_DIR:-}" ] \
    && command -v systemd-run >/dev/null 2>&1; then
    exec systemd-run --user --collect --same-dir --pipe --wait \
        --setenv=TOLLGATE_RV_IN_SYSTEMD=1 \
        --unit="release-validate-$(date +%s)" "$0" "$@"
fi

FEED_API="https://api.github.com/repos/FreedomTechFeed/packages/releases?per_page=30"
TMBG="${TOLLGATE_TMBG_CHECKOUT:-$HOME/src/tollgate-module-basic-go}"
NODE_BIN="${TOLLGATE_NODE_BIN:-$HOME/laneA/tools/node-v22.17.0-linux-x64/bin}"
GO_PIN="${TOLLGATE_GO_PIN:-go1.26.8}"
VENUE_HOST="${TOLLGATE_VENUE_HOST:-ai-legion-small}"
VENUE_SUBNET="${TOLLGATE_VENUE_SUBNET:-10.99.150}"
MINT_URL="${TOLLGATE_TEST_MINT_URL:-http://192.168.13.221:8383}"
VENV_PY="${TOLLGATE_PYTHON_VENV:-$HOME/.tollgate-test-venv/bin/python}"

TAG=""
IPK=""
SKIP_SUITE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --ipk) IPK="$2"; shift 2 ;;
        --skip-suite) SKIP_SUITE=true; shift ;;
        *) echo "unknown: $1" >&2; exit 1 ;;
    esac
done

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$REPO_ROOT/results/release-validate-$STAMP"
mkdir -p "$OUT"
log() { echo "[rv $(date +%H:%M:%S)] $*" | tee -a "$OUT/validate.log"; }
fail() { echo "[rv] FAIL: $*" | tee -a "$OUT/validate.log" >&2; exit 1; }

# ── Phase 1: resolve tag and source commit ────────────────────────────────
if [ -n "$IPK" ]; then
    [ -f "$IPK" ] || fail "no ipk at $IPK"
    log "using provided ipk: $IPK"
else
    if [ -z "$TAG" ]; then
        log "resolving newest feed tag (§0 — never assume)"
        TAG=$(curl -fsS --max-time 20 "$FEED_API" | python3 -c \
            'import sys,json; rs=[r for r in json.load(sys.stdin) if not r.get("draft")]; print(max(rs, key=lambda r: r["published_at"])["tag_name"])' \
            2>/dev/null) || fail "feed resolve failed"
        [ -n "$TAG" ] || fail "empty tag from feed"
    fi
    log "tag: $TAG"

    log "resolving source commit from the feed's aarch64 ipk stamp"
    PROBE=$(mktemp -d)
    VER=$(printf '%s' "$TAG" | sed -e 's/^v//' -e 's/-/_/g')
    AASSET=$(curl -fsS --max-time 20 "$FEED_API" | python3 -c "
import json,sys
rs=[r for r in json.load(sys.stdin) if r.get('tag_name')=='$TAG']
for r in rs:
    for a in r.get('assets',[]):
        n=a['name']
        if 'aarch64_cortex-a53' in n and n.endswith('.ipk'):
            print(a['browser_download_url']); raise SystemExit
" 2>/dev/null) || true
    [ -n "$AASSET" ] || fail "no aarch64_cortex-a53 ipk asset found for $TAG"
    curl -fsSL --max-time 60 -o "$PROBE/p.ipk" "$AASSET" \
        || fail "feed ipk download failed"
    (cd "$PROBE" && tar xzf p.ipk ./data.tar.gz 2>/dev/null \
        && tar xzf data.tar.gz ./usr/bin/tollgate-wrt 2>/dev/null) \
        || fail "ipk extraction failed"
    STAMPED=$(strings "$PROBE/usr/bin/tollgate-wrt" \
        | grep -m1 -oE 'v[0-9][a-z0-9.-]*-g[0-9a-f]{7,10}' || true)
    COMMIT=$(printf '%s' "$STAMPED" | grep -oE 'g[0-9a-f]{7,10}' | tr -d g || true)
    [ -n "$COMMIT" ] || fail "no version stamp found in feed ipk"
    log "feed binary stamped: ${STAMPED} → commit ${COMMIT}"
    echo "$TAG $STAMPED $COMMIT" > "$OUT/provenance.txt"
    rm -rf "$PROBE"

    log "building x86_64 from $COMMIT (Node pin + $GO_PIN)"
    git -C "$TMBG" fetch origin main -q || fail "tmbg fetch failed"
    git -C "$TMBG" checkout -q "$COMMIT" || fail "checkout $COMMIT failed"
    (cd "$TMBG" && export PATH="$NODE_BIN:$PATH" \
        && make portal-build \
        && ARCH=x86_64 GOTOOLCHAIN="$GO_PIN" PKG_VERSION="$TAG" \
           bash packaging/local-build-ipk.sh) >> "$OUT/build.log" 2>&1 \
        || fail "build failed (see $OUT/build.log)"
    IPK=$(ls -t "$TMBG"/packaging/tollgate-wrt_*_x86_64.ipk | head -1)
    log "built: $IPK"
fi

# ── Phase 2: ensure the venue (wired-only, on the lab host) ────────────────
log "ensuring venue on $VENUE_HOST (${VENUE_SUBNET}.0/24)"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$VENUE_HOST" "sudo bash -s" <<'VENUE' >> "$OUT/venue.log" 2>&1
set -e
ip link show br-laneA >/dev/null 2>&1 || {
    ip link add br-laneA type bridge
    ip addr add 10.99.150.1/24 dev br-laneA
    ip link set br-laneA up
    ip tuntap add dev laneA0 mode tap user "$(logname 2>/dev/null || echo ubuntu)"
    ip link set laneA0 master br-laneA
    ip link set laneA0 up
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
    iptables -t nat -A POSTROUTING -s 10.99.150.0/24 -j MASQUERADE
    iptables -A FORWARD -i br-laneA -j ACCEPT
    iptables -A FORWARD -o br-laneA -j ACCEPT
    echo venue-fabric-created
}
ufw status 2>/dev/null | grep -q "8383.*10.99.150.0/24" || \
    ufw allow from 10.99.150.0/24 to any port 8383 proto tcp comment "laneA venue mint" >/dev/null 2>&1 || true
systemctl is-active laneA-vm >/dev/null 2>&1 || {
    systemd-run --unit=laneA-vm --working-directory="$HOME/laneA" -- \
        qemu-system-x86_64 -enable-kvm -cpu host -m 512 -smp 2 \
        -drive file="$HOME/laneA/openwrt.img",format=raw,if=virtio \
        -netdev tap,id=n0,ifname=laneA0,script=no,downscript=no \
        -device virtio-net-pci,netdev=n0 -display none \
        -serial unix:"$HOME/laneA/console.sock",server=on,wait=off \
        -monitor unix:"$HOME/laneA/monitor.sock",server=on,wait=off
    echo venue-vm-started
}
VENUE
for i in $(seq 1 15); do
    ssh -o BatchMode=yes -o ConnectTimeout=4 "$VENUE_HOST" \
        "timeout 3 bash -c 'echo > /dev/tcp/${VENUE_SUBNET}.2/22'" 2>/dev/null && break
    sleep 7
done
ssh -o BatchMode=yes "$VENUE_HOST" \
    "timeout 3 bash -c 'echo > /dev/tcp/${VENUE_SUBNET}.2/22'" 2>/dev/null \
    || fail "venue VM SSH never came up (TCP probe; see $OUT/venue.log)"
log "venue VM up (TCP-probed)"
sudo -n ip route | grep -q "$VENUE_SUBNET.0/24" || \
    sudo -n ip route add "${VENUE_SUBNET}.0/24" via "${TOLLGATE_VENUE_GW:-192.168.13.221}" dev wlp59s0 \
    || log "WARN: could not add local route (are we not on the driver host?)"

# ── Phase 3: deploy + health gate ─────────────────────────────────────────
log "deploying $IPK with health gate"
scp -q "$IPK" "$VENUE_HOST:~/rv.ipk" || fail "transfer to venue host failed"
ssh -o BatchMode=yes "$VENUE_HOST" "sudo env MINT_URL='$MINT_URL' bash -s" <<DEPLOY >> "$OUT/deploy.log" 2>&1
set -e
RV_IPK=/home/ubuntu/rv.ipk
sshpass -p tollgate-lab-admin scp -O -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "\$RV_IPK" root@${VENUE_SUBNET}.2:/tmp/rv.ipk
sshpass -p tollgate-lab-admin ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@${VENUE_SUBNET}.2 "
    opkg install --force-overwrite /tmp/rv.ipk >/dev/null 2>&1
    jq '.accepted_mints = [{\"url\":\"\$MINT_URL\",\"min_balance\":64,\"balance_tolerance_percent\":10,\"payout_interval_seconds\":60,\"min_payout_amount\":128,\"price_per_step\":1,\"price_unit\":\"sat\",\"purchase_min_steps\":1}]' \
        /etc/tollgate/config.json > /tmp/cfg.new && mv /tmp/cfg.new /etc/tollgate/config.json
    rm -f /etc/tollgate/wallet.db
    /etc/init.d/tollgate-wrt restart
    sleep 10
    [ -s /usr/bin/nodogsplash ] || { echo NDS-BINARY-EMPTY; exit 1; }
    ndsctl status >/dev/null 2>&1 || { echo NDSCTL-DEAD; exit 1; }
    wget -q -O- -T 8 http://127.0.0.1:2121/ | head -c 60
"
DEPLOY
[ $? -eq 0 ] || fail "deploy health gate failed (see $OUT/deploy.log)"
log "health gate green"

# ── Phase 4: payment rejection suite ──────────────────────────────────────
if [ "$SKIP_SUITE" = true ]; then
    log "--skip-suite: done"
    log "VERDICT: deploy-green"
    exit 0
fi
log "running payment rejection suite"
# TOLLGATE_SSH_KEY= (empty) matters: a checkout .env can set TOLLGATE_SSH_KEY,
# and Router skips sshpass entirely when an identity file is configured —
# password auth to the venue then fails as bare-ssh askpass.
TOLLGATE_SSH_HOST="${VENUE_SUBNET}.2" \
TOLLGATE_SSH_PASSWORD=tollgate-lab-admin \
TOLLGATE_LUCI_PASSWORD=tollgate-lab-admin \
TOLLGATE_SSH_KEY= \
TOLLGATE_VIRTUAL_LAB=1 \
TOLLGATE_CLIENT_IP="${TOLLGATE_VENUE_GW:-192.168.13.221}" \
TOLLGATE_CLIENT_USER=ubuntu \
TOLLGATE_TEST_MINT_URL="$MINT_URL" \
"$VENV_PY" -m pytest tests/api/test_payment_rejection_semantics.py \
    --no-deploy -v --tb=short 2>&1 | tee "$OUT/suite.log" | tail -3
RC=${PIPESTATUS[0]}
if [ $RC -eq 0 ]; then
    log "VERDICT: GREEN (deploy + suite)"
else
    log "VERDICT: RED (suite rc=$RC — see $OUT/suite.log)"
fi
exit $RC
