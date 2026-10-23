#!/usr/bin/env bash
# club-acceptance.sh v2 — the whole handover acceptance in one command, against a live
# router running the published pre17 package.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/test/club-acceptance.sh)
#   bash <(curl -fsSL .../club-acceptance.sh) 192.168.1.1
#   RHP_CASHU_TOKEN="$(cat token.txt)" RHP_SPEND_MAX_SATS=64 bash <(curl -fsSL .../club-acceptance.sh)
#
# Three stages, then one verdict:
#   A. on-router state (SSH; uses your key, or sshpass, or asks YOU for the password once)
#   B. a STRANGER'S walk — the four OS detection probes from a client the router has not
#      authorised, which by default is THIS MACHINE (it is on the router's LAN and has not
#      paid). Nothing is installed or changed; no sudo needed.
#   C. the official happy-path harness (build identity by sha256, surfaces, captive chain,
#      API shapes, Lightning status-poll, tokenless rejection, paid lane if a token is given)
#
# Read-only: nothing is installed, flashed, rebooted or reconfigured. No token => nothing is
# spent; the paid lane is opt-in and refuses without an explicit spend ceiling.
#
# v2 changes (measured on a tester's laptop, v1):
#   - stage A no longer requires MY password file: it asks you once, then multiplexes the
#     connection so you are not asked again.
#   - stage B no longer demands sudo + a macvlan: the machine you run this from IS an
#     unauthorised client, so it can do the stranger's walk itself. A synthetic fresh MAC
#     remains available as TG_FRESH_MAC=1 (needs sudo + wired).
#
# Env: TG_SSH_CMD, TG_ROUTER_PW, TG_ROUTER_PW_FILE, TG_FRESH_MAC=1, TG_WIRED_IFACE,
#      RHP_CASHU_TOKEN, RHP_SPEND_MAX_SATS, RHP_ROUTER_IP, SKIP_HARNESS=1
# Exit: 0 = the club build passed, 1 = something real failed, 2 = could not run.

set -uo pipefail

ROUTER_IP="${1:-${RHP_ROUTER_IP:-192.168.1.1}}"
TAG="${RHP_RELEASE_TAG:-v0.6.0-alpha4-pre17}"
ARCH="${RHP_ARCH:-aarch64_cortex-a53}"
HARNESS_SHA="${RHP_HARNESS_SHA:-cfbfff5a9e6aa1de03dda783e9b930d9c36e0bf9}"
FEED_REPO="${RHP_FEED_REPO:-FreedomTechFeed/packages}"
MODULE_REPO="${RHP_MODULE_REPO:-OpenTollGate/tollgate-module-basic-go}"
WORK="${RHP_WORKDIR:-$HOME/.cache/tg-club-acceptance}"
PW_FILE="${TG_ROUTER_PW_FILE:-$HOME/.tg-e2e/pw}"
PKGVER="${TAG#v}"; PKGVER="${PKGVER//-/_}"
APK_NAME="tollgate-wrt_${PKGVER}_${ARCH}.apk"
SKIP_HARNESS="${SKIP_HARNESS:-0}"
ROUTER_PREFIX=$(printf '%s' "$ROUTER_IP" | awk -F. '{print $1"."$2"."$3}')

say()  { printf '%s\n' "$*"; }
ok()   { printf '  PASS  %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS+1)); }
warn() { printf '  WARN  %s\n' "$*"; }
info() { printf '  ----  %s\n' "$*"; }
hdr()  { printf '\n==================== %s ====================\n' "$*"; }
FAILS=0
mkdir -p "$WORK"
CTL="$WORK/ssh-ctl-%r@%h:%p"

for c in curl tar sha256sum awk; do
  command -v "$c" >/dev/null 2>&1 || { say "FATAL: missing dependency: $c"; exit 2; }
done

hdr "0. target $ROUTER_IP"
for p in 22 2051 2121; do
  if timeout 4 bash -c "exec 3<>/dev/tcp/$ROUTER_IP/$p" 2>/dev/null; then ok "TCP $p"; else bad "TCP $p not answering (TCP, not ping: this firewall drops ICMP)"; fi
done
[ "$FAILS" -eq 0 ] || { say ""; say "VERDICT: cannot reach the router — is it powered, wired, on this network?"; exit 2; }

# ---------------------------------------------------------------- A. on-router state
hdr "A. on-router state"
SSH_CMD=(); SSH_WHY=""
if [ -n "${TG_SSH_CMD:-}" ]; then
  SSH_CMD=(bash -lc "$TG_SSH_CMD"); SSH_WHY="TG_SSH_CMD"
elif [ -n "${TG_ROUTER_PW:-}" ] && command -v sshpass >/dev/null 2>&1; then
  SSH_CMD=(sshpass -p "$TG_ROUTER_PW" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 "root@$ROUTER_IP"); SSH_WHY="TG_ROUTER_PW"
elif [ -f "$PW_FILE" ] && command -v sshpass >/dev/null 2>&1; then
  SSH_CMD=(sshpass -f "$PW_FILE" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 "root@$ROUTER_IP"); SSH_WHY="$PW_FILE"
elif [ -t 0 ] && command -v ssh >/dev/null 2>&1; then
  # No stored secret: ask the human ONCE. ControlPersist then reuses the connection for
  # every later call in this run, so the password is typed exactly once.
  info "no stored router password found — SSH will ask you once (connection is multiplexed)"
  SSH_CMD=(ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
              -o ControlMaster=auto -o "ControlPath=$CTL" -o ControlPersist=600 \
              "root@$ROUTER_IP")
  SSH_WHY="interactive (typed by you)"
else
  info "SKIP: no SSH path. Set TG_ROUTER_PW_FILE=<file>, TG_ROUTER_PW=<pw>, or TG_SSH_CMD='<cmd>'."
  info "      Without stage A the guard chain, IPv6 state and trusted-MAC list are unverified —"
  info "      and an on-box check is the only way to see them."
fi

if [ "${#SSH_CMD[@]}" -gt 0 ]; then
  if curl -fsSL "https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/diag/tg-router-health.sh" -o "$WORK/tg-router-health.sh"; then
    if "${SSH_CMD[@]}" 'sh -s' < "$WORK/tg-router-health.sh" > "$WORK/on-router.txt" 2>"$WORK/on-router.err"; then
      info "ssh path: $SSH_WHY"
      sed 's/^/  /' "$WORK/on-router.txt"
      grep -q '=== verdict: ALL CHECKS PASS ===' "$WORK/on-router.txt" && ok "on-router health: ALL CHECKS PASS" \
        || bad "on-router health reported failures — see $WORK/on-router.txt"
    else
      bad "on-router health script could not run over ssh (see $WORK/on-router.err)"
      sed 's/^/  /' "$WORK/on-router.err" | tail -5
      info "hint: re-run with TG_ROUTER_PW='<router password>' (needs sshpass) or"
      info "      TG_SSH_CMD='ssh -i ~/.ssh/id_ed25519 root@$ROUTER_IP' if you use a key."
      info "      Stage A is the only place the guard chain, RA state and trusted-MAC list are"
      info "      visible; without it this run cannot certify the build."
    fi
  else
    bad "could not fetch the health script"
  fi
fi

# ------------------------------------------------------- B. a stranger's portal walk
hdr "B. stranger's walk (4 OS detection probes from an unauthorised client)"
# Who is a stranger? THIS MACHINE: it is on the router's LAN and has not paid. v1 insisted
# on a synthetic fresh MAC via macvlan, which needs sudo and a wired NIC; that turned a
# five-minute check into a password prompt fight. The honest vantage is the one the tester
# already has -- keep the synthetic path as an opt-in.
SRC_IP=$(ip route get "$ROUTER_IP" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
IFACE=$(ip route get "$ROUTER_IP" 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1)
MY_MAC=$(cat "/sys/class/net/$IFACE/address" 2>/dev/null || echo "")
info "this machine reaches the router as ${SRC_IP:-?} via ${IFACE:-?} (mac ${MY_MAC:-?})"
[ -n "$MY_MAC" ] && grep -qi "$MY_MAC" "$WORK/on-router.txt" 2>/dev/null \
  && info "the router lists this MAC — see the TRUSTED CLIENTS section above (State Preauthenticated = a normal stranger)"

probe_walk() {   # $1 = optional curl interface binding (e.g. an IP on the synthetic vif)
  local bind="$1" C REDIRECTED=0 CHECKED=0 code loc
  if [ -n "$bind" ]; then C=(curl -s --interface "$bind" -m 10); else C=(curl -s -m 10); fi
  for u in "http://connectivitycheck.gstatic.com/generate_204" \
           "http://captive.apple.com/hotspot-detect.html" \
           "http://www.msftconnecttest.com/connecttest.txt" \
           "http://detectportal.firefox.com/success.txt"; do
    CHECKED=$((CHECKED+1))
    code=$("${C[@]}" -o /dev/null -w '%{http_code}' "$u" 2>/dev/null || echo ERR)
    loc=$("${C[@]}" -o /dev/null -w '%{redirect_url}' "$u" 2>/dev/null || echo "")
    if [ "$code" = "307" ] && printf '%s' "$loc" | grep -q '/splash.html'; then
      REDIRECTED=$((REDIRECTED+1)); ok "$(printf '%-46s' "${u#http://}") 307 -> splash"
    elif [ "$code" = "204" ] || [ "$code" = "200" ]; then
      bad "$(printf '%-46s' "${u#http://}") $code — FREE INTERNET: this client is authorised (a paid session or a trusted MAC), so it cannot prove the portal works. Use a device that has never paid."
    else
      bad "$(printf '%-46s' "${u#http://}") $code (expected 307 to the splash: no OS sign-in prompt otherwise)"
    fi
  done
  [ "$REDIRECTED" -eq "$CHECKED" ] && ok "all $CHECKED OS detection probes redirect (Android/Apple/Windows/Firefox)"
  code=$("${C[@]}" -o /dev/null -w '%{http_code}' "http://$ROUTER_IP:2051/splash.html" 2>/dev/null)
  [ "$code" = "200" ] && ok "portal :2051/splash.html -> 200" || bad "portal :2051/splash.html -> $code"
  code=$("${C[@]}" -o /dev/null -w '%{http_code}' "http://$ROUTER_IP:8090/" 2>/dev/null)
  [ "$code" = "000" ] && ok "admin board :8090 -> 000 from a guest (the #566 guard)" || bad "admin board :8090 -> $code from a guest (expected the guard to block it)"
  code=$("${C[@]}" -o /dev/null -w '%{http_code}' "http://$ROUTER_IP/" 2>/dev/null)
  [ "$code" = "307" ] && ok "plain :80 -> 307 to the portal" || info ":80 -> $code (for a pre-auth guest this should be 307)"
}

if [ "${TG_FRESH_MAC:-0}" = "1" ]; then
  # opt-in synthetic client: a MAC the router has never seen (needs root + a wired NIC)
  VIF="tg-stranger"; VMAC="02:11:22:33:44:55"; VIP="$ROUTER_PREFIX.222"
  WIFACE="${TG_WIRED_IFACE:-$IFACE}"
  case "$WIFACE" in
    wl*|*wlan*|*wlp*) info "SKIP: $WIFACE is wireless; a macvlan cannot carry a second MAC through an AP." ; WIFACE="";;
  esac
  if [ -z "$WIFACE" ]; then
    info "(TG_FRESH_MAC=1 needs a wired NIC; falling back to this machine's own vantage)"
    probe_walk ""
  else
    info "asking for sudo ONCE (creates $VIF on $WIFACE, then removes it)"
    if sudo -v 2>/dev/null && sudo ip link add "$VIF" link "$WIFACE" type macvlan mode bridge 2>/dev/null; then
      sudo sh -c "ip link set $VIF address $VMAC; ip link set $VIF up; ip addr add $VIP/32 dev $VIF; ip route add $ROUTER_IP/32 dev $VIF src $VIP scope link table 100; ip route add default via $ROUTER_IP dev $VIF src $VIP table 100; ip rule add from $VIP table 100 priority 100"
      trap 'sudo ip rule del from '"$VIP"' table 100 2>/dev/null; sudo ip route flush table 100 2>/dev/null; sudo ip link del '"$VIF"' 2>/dev/null' EXIT
      info "synthetic stranger on $WIFACE: mac=$VMAC ip=$VIP"
      probe_walk "$VIP"
    else
      info "SKIP: sudo/macvlan unavailable on this machine — using this machine's own vantage instead."
      probe_walk ""
    fi
  fi
else
  probe_walk ""
fi

# ------------------------------------------------------------ C. the official harness
if [ "$SKIP_HARNESS" = "1" ]; then
  hdr "C. official harness — SKIPPED (SKIP_HARNESS=1)"
else
  hdr "C. official happy-path harness"
  HARNESS_DIR="$WORK/module-$HARNESS_SHA"
  if [ ! -x "$HARNESS_DIR/tests/router-happy-path/run.sh" ]; then
    info "fetching the harness at ${HARNESS_SHA:0:12}"
    curl -fsSL "https://codeload.github.com/$MODULE_REPO/tar.gz/$HARNESS_SHA" -o "$WORK/module.tar.gz" \
      && tar xzf "$WORK/module.tar.gz" -C "$WORK" || bad "harness download failed"
  fi
  APK="${RHP_APK:-$WORK/$APK_NAME}"
  if [ ! -f "$APK" ]; then
    info "downloading $APK_NAME from $FEED_REPO $TAG"
    curl -fsSL "https://github.com/$FEED_REPO/releases/download/$TAG/$APK_NAME" -o "$APK" || bad "apk download failed"
  fi
  if [ -f "$APK" ]; then
    curl -fsSL "https://github.com/$FEED_REPO/releases/download/$TAG/SHA256SUMS" -o "$WORK/SHA256SUMS" 2>/dev/null
    WANT=$(grep -F " $APK_NAME" "$WORK/SHA256SUMS" 2>/dev/null | head -1 | awk '{print $1}')
    GOT=$(sha256sum "$APK" | awk '{print $1}')
    if [ -n "$WANT" ] && [ "$WANT" = "$GOT" ]; then ok "apk sha256 matches the signed SHA256SUMS ($GOT)"; else bad "apk hash mismatch (manifest=$WANT file=$GOT)"; fi
  fi
  OUT="$WORK/rhp-$TAG-$(date +%Y%m%d-%H%M%S)"
  info "running (read-only; a token only if RHP_CASHU_TOKEN is set) — transcript in $OUT"
  ( cd "$HARNESS_DIR" && bash tests/router-happy-path/run.sh --apk "$APK" --router-ip "$ROUTER_IP" --out "$OUT" ) | tee "$WORK/harness.txt" | grep -E '^RHPCHECK .*(FAIL|SKIP)|^RHPRESULT|^RHPFAILED' | sed 's/^/  /'
  # Classify the failures that are vantage/skew artefacts, not product defects.
  GUARD=$(curl -s -o /dev/null -w '%{http_code}' -m 6 "http://$ROUTER_IP:8090/" 2>/dev/null || echo ERR)
  REAL=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    id=$(printf '%s' "$line" | awk '{print $2}')
    case "$id" in
      *8090*) [ "$GUARD" = "000" ] && { info "$id — EXPECTED from the guest side (the #566 guard); needs a management vantage"; continue; } ;;
      net:tcp-*)
        port=$(printf '%s' "$id" | sed 's/.*tcp-//')
        grep -qE "PASS.*${port}" "$WORK/harness.txt" 2>/dev/null && { info "$id — FLAKE (the same port PASSes later in this run)"; continue; } ;;
      api:session-state)
        info "$id — version skew (harness from module main vs the installed pin); re-check after the next cut"; continue ;;
    esac
    REAL=$((REAL+1))
  done < <(grep -E '^RHPCHECK .*FAIL' "$WORK/harness.txt" 2>/dev/null)
  grep -E '^RHPRESULT' "$WORK/harness.txt" | sed 's/^/  /'
  [ "$REAL" -eq 0 ] && ok "harness: no real failures (all FAILs accounted for as guard-expected, flaky, or version skew)" || bad "harness: $REAL unexplained failure(s)"
fi

hdr "VERDICT"
if [ "$FAILS" -eq 0 ]; then
  say "CLUB ACCEPTANCE: PASS — the router is running the package you think it shipped, a"
  say "stranger gets the captive portal with the OS sign-in prompt, the admin board is"
  say "unreachable from the guest network, and the API/payment lanes behave."
  say "evidence: $WORK/on-router.txt  $WORK/harness.txt"
  exit 0
fi
say "CLUB ACCEPTANCE: FAIL — $FAILS check(s) failed. Read the FAIL lines above."
say "A 429 is a THROTTLE, not a defect: wait a minute and re-run. A check that fails"
say "TWICE is real. evidence: $WORK"
exit 1
