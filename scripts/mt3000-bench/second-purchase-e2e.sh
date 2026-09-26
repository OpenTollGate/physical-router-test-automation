#!/usr/bin/env bash
#
# second-purchase-e2e.sh — does a SECOND purchase re-open the gate on the bench MT3000?
#
# The reported product bug: a client buys, exhausts the allotment, and the gate never
# re-opens for a second purchase. This is the clean, reproducible reproduction: a fresh
# MAC (never seen by nodogsplash) buys, exhausts the allotment by downloading THROUGH the
# router, the post-exhaustion state is captured, the `ndsctl deauth` discriminator runs,
# and a second token is spent.
#
# DRY RUN BY DEFAULT. Nothing is purchased, no interface is created and no bench lock is
# taken until you pass --purchase (or PURCHASE=1). A dry run needs no router at all.
#
# THE TRAP THAT MAKES EVERY OTHER ATTEMPT INVALID
#   The module authorises the MAC of the REQUESTING SOCKET, not a `?mac=` parameter. A
#   purchase POSTed from the bench host with `?mac=<other>` authenticates the HOST. So every
#   purchase is issued THROUGH the client's own interface (`curl --interface <client-ip>`),
#   and the client's routes live in a separate policy table so the host's own management
#   path to the router survives the run.
#
# USAGE
#   scripts/mt3000-bench/second-purchase-e2e.sh                     # dry run: print the plan
#   scripts/mt3000-bench/second-purchase-e2e.sh --purchase          # the real, paid run
#   TOKEN_1=... TOKEN_2=... scripts/mt3000-bench/second-purchase-e2e.sh --purchase
#
#   # the same thing, the way the kit runs bench work (single owner, named holder):
#   bench-with-lock.sh --purpose "second purchase e2e" -- \
#     scripts/mt3000-bench/second-purchase-e2e.sh --purchase
#   # ...or let the script take the lock itself (it re-execs under `bench-lock.sh exec`).
#
# TOKENS ARE SINGLE-USE
#   Both tokens are NUT-07-verified UNSPENT immediately before the paid phases; the run
#   fails closed (exit 8) if the mint cannot answer. Mint/verify with `bench-token.py`
#   from this directory.
#
# EXIT CODES
#   0  ran to the end; every assertion held
#   2  usage / preflight failed (missing token file, degraded mode, no wired NIC)
#   3  the bench lock is held by another window (refused, holder named)  [bench-lock's own code]
#   4  not inside a bench window (bench-lock require failed)            [bench-lock's own code]
#   5  bench-lock refused a STALE holder line — its owner died; recovery is explicit only
#      (`bench-lock.sh take --reclaim-stale`), and only an operator clears an orphan
#   10 the FIRST purchase did not open the gate (setup problem, not the bug)
#   11 the allotment never exhausted inside the budget (INCONCLUSIVE — not a pass)
#   12 the SECOND purchase did not re-open the gate (the reported bug reproduced)
#   13 a phase assertion failed (see ASSERT FAIL lines in the transcript)
#   14 the tokens are not spendable (NUT-07 says spent/pending, or the mint is unreachable)
#
# ENV (all overridable; no user-specific path is baked in)
#   ROUTER_IP         192.168.1.1                 the bench router
#   BENCH_NIC         unset -> auto-detect        the WIRED host NIC on the router's /24
#   CLIENT_VIF        tg-club                     the macvlan interface to create
#   CLIENT_MAC        02:11:22:33:44:55           a MAC the router has never seen
#   CLIENT_IP         <router>/24 + .222          the client's source address
#   POLICY_TABLE      100                         policy-routing table for the client
#   POLICY_PRIORITY   100                         ip-rule priority
#   API_BASE          http://$ROUTER_IP:2121       the tollgate backend
#   PROBE_URL         connectivitycheck.gstatic.com/generate_204
#   EGRESS_URL        speed.cloudflare.com __down?bytes=2000000
#   TOKEN_1, TOKEN_2  REQUIRED for --purchase     paths to two cashu token files
#   LOG_DIR           ~/.tg-e2e/second-purchase   transcripts + download logs
#   LOG_TO_FILE       1                           transcript to $LOG_DIR/... (0 = stdout only)
#   BURN_ROUNDS       12                          exhaustion rounds
#   BURN_PARALLEL     6                           parallel downloads per round
#   BURN_URLS         a 6-URL default list        space-separated large-file URLs
#   WATCH_TRIES       6                           gate polls per round (15 s apart)
#   GATE_STRIKES      2                           consecutive non-204/200 probes = "closed"
#   PROBE_TRIES       12                          gate polls after a purchase (10 s apart)
#   ROUTER_PW_FILE    ~/.tg-e2e/pw                for router-snapshot.sh
#
set -uo pipefail

EX_OK=0
EX_USAGE=2
EX_LOCK=3
EX_NO_GATE=10
EX_NO_EXHAUST=11
EX_NO_REOPEN=12
EX_ASSERT=13
EX_TOKEN=14

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
HERE="$(cd "$(dirname "$SELF")" && pwd)"
SNAP="$HERE/router-snapshot.sh"
TOKENTOOL="$HERE/bench-token.py"

# ---------------------------------------------------------------- configuration

ROUTER_IP="${ROUTER_IP:-192.168.1.1}"
BENCH_NIC="${BENCH_NIC:-}"
CLIENT_VIF="${CLIENT_VIF:-tg-club}"
CLIENT_MAC="${CLIENT_MAC:-02:11:22:33:44:55}"
CLIENT_IP="${CLIENT_IP:-${ROUTER_IP%.*}.222}"
POLICY_TABLE="${POLICY_TABLE:-100}"
POLICY_PRIORITY="${POLICY_PRIORITY:-100}"
API_BASE="${API_BASE:-http://$ROUTER_IP:2121}"
PROBE_URL="${PROBE_URL:-http://connectivitycheck.gstatic.com/generate_204}"
EGRESS_URL="${EGRESS_URL:-https://speed.cloudflare.com/__down?bytes=2000000}"
TOKEN_1="${TOKEN_1:-}"
TOKEN_2="${TOKEN_2:-}"
LOG_DIR="${LOG_DIR:-${BENCH_LOG_DIR:-$HOME/.tg-e2e/second-purchase}}"
LOG_TO_FILE="${LOG_TO_FILE:-1}"
BURN_ROUNDS="${BURN_ROUNDS:-12}"
BURN_PARALLEL="${BURN_PARALLEL:-6}"
WATCH_TRIES="${WATCH_TRIES:-6}"
GATE_STRIKES="${GATE_STRIKES:-2}"
PROBE_TRIES="${PROBE_TRIES:-12}"
BURN_URLS="${BURN_URLS:-https://ash-speed.hetzner.com/100MB.bin https://fsn1-speed.hetzner.com/100MB.bin https://proof.ovh.net/files/100Mb.dat http://ipv4.download.thinkbroadband.com/100MB.zip http://speedtest.tele2.net/100MB.zip https://speed.cloudflare.com/__down?bytes=104857600}"

PURCHASE="${PURCHASE:-0}"
DETACHED="${DETACHED:-0}"

die() { printf 'second-purchase-e2e: %s\n' "$*" >&2; exit "${EX_USAGE}"; }
say() { printf '\n########## %s ##########\n' "$*"; date -u '+%Y-%m-%dT%H:%M:%SZ'; }

# The usage text is the file's own header comment: it cannot drift from the code.
usage() {
  grep -E '^#' "$SELF" | grep -v '^#!' | sed 's/^# \{0,1\}//'
}

FAILED=0
assert_eq() {   # $1 desc  $2 expected  $3 actual
  if [ "$2" = "$3" ]; then
    printf 'ASSERT PASS  %s (=%s)\n' "$1" "$3"
  else
    printf 'ASSERT FAIL  %s: expected %s, got %s\n' "$1" "$2" "$3"
    FAILED=$((FAILED + 1))
  fi
}
assert_contains() {   # $1 desc  $2 needle  $3 haystack
  case "$3" in
    *"$2"*) printf 'ASSERT PASS  %s\n' "$1" ;;
    *) printf 'ASSERT FAIL  %s: output does not contain %s\n' "$1" "$2"
       printf '%s\n' "$3" | sed 's/^/      | /'
       FAILED=$((FAILED + 1)) ;;
  esac
}

# ---------------------------------------------------------------- args

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit "$EX_OK" ;;
    --purchase) PURCHASE=1; shift ;;
    --detached) DETACHED=1; shift ;;
    --log-dir) [ $# -ge 2 ] || die "--log-dir needs a value"; LOG_DIR="$2"; shift 2 ;;
    --burn-rounds) [ $# -ge 2 ] || die "--burn-rounds needs a value"; BURN_ROUNDS="$2"; shift 2 ;;
    --client-mac) [ $# -ge 2 ] || die "--client-mac needs a value"; CLIENT_MAC="$2"; shift 2 ;;
    --client-ip) [ $# -ge 2 ] || die "--client-ip needs a value"; CLIENT_IP="$2"; shift 2 ;;
    --nic) [ $# -ge 2 ] || die "--nic needs a value"; BENCH_NIC="$2"; shift 2 ;;
    -*) die "unknown option '$1' (see --help)" ;;
    *) die "unexpected argument '$1' (see --help)" ;;
  esac
done

TS="$(date -u '+%Y%m%dT%H%M%SZ')"

# ---------------------------------------------------------------- helpers

norm_probe() { case "$1" in 204|200) printf 'open' ;; *) printf '%s' "$1" ;; esac; }

detect_nic() {   # $1 = first three octets of the router address
  # The client must be a MACVLAN on a WIRED NIC: a macvlan on Wi-Fi cannot present a second
  # MAC through an AP association (every probe returns 000). Never pick the default-route
  # interface — on this host that is Wi-Fi.
  local want3="$1" line iface cidr
  while read -r line; do
    [ -n "$line" ] || continue
    iface="$(printf '%s' "$line" | awk '{print $2}')"
    cidr="$(printf '%s' "$line" | awk '{for (i=1;i<=NF;i++) if ($i=="inet") print $(i+1)}')"
    iface="${iface%%@*}"
    [ "$iface" = "lo" ] && continue
    [ "$iface" = "$CLIENT_VIF" ] && continue
    case "${cidr%.*}" in
      "$want3") printf '%s\n' "$iface"; return 0 ;;
    esac
  done < <(ip -o -4 addr show 2>/dev/null)
  return 1
}

json_field() { printf '%s' "$2" | sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" | head -1; }

# ---------------------------------------------------------------- plan (always printed)

printf 'second-purchase-e2e  mode=%s\n' "$([ "$PURCHASE" = 1 ] && printf 'PURCHASE' || printf 'DRY-RUN')"
printf '  router            %s  (%s)\n' "$ROUTER_IP" "$API_BASE"
printf '  client            %s on vif %s, mac %s, policy table %s (priority %s)\n' \
  "$CLIENT_IP" "$CLIENT_VIF" "$CLIENT_MAC" "$POLICY_TABLE" "$POLICY_PRIORITY"
printf '  host NIC          %s\n' "${BENCH_NIC:-<auto-detect: first wired NIC on the router /24>}"
printf '  tokens            TOKEN_1=%s TOKEN_2=%s\n' "${TOKEN_1:-<unset>}" "${TOKEN_2:-<unset>}"
printf '  exhaustion        %s rounds x %s parallel downloads\n' "$BURN_ROUNDS" "$BURN_PARALLEL"
printf '  log dir           %s\n' "$LOG_DIR"
printf '  phases            0 fresh-MAC baseline -> 1 buy#1 -> 2 exhaust -> 3 post-exhaustion\n'
printf '                    -> 4 ndsctl deauth discriminator -> 5 buy#2 (does the gate re-open?)\n'

if [ "$PURCHASE" != 1 ]; then
  printf '\nDRY-RUN: nothing was purchased, no interface was created, no bench lock was taken.\n'
  printf 'Re-run with --purchase (or PURCHASE=1) to spend TOKEN_1 and TOKEN_2.\n'
  exit "$EX_OK"
fi

# ---------------------------------------------------------------- preflight (no bench lock yet)

for t in curl ip awk sed mktemp python3; do
  command -v "$t" >/dev/null 2>&1 || die "missing required tool: $t"
done
[ -x "$SNAP" ] || die "missing helper: $SNAP"
[ -f "$TOKENTOOL" ] || die "missing helper: $TOKENTOOL"
[ -n "$TOKEN_1" ] || die "TOKEN_1 is required for --purchase (path to a cashu token file)"
[ -n "$TOKEN_2" ] || die "TOKEN_2 is required for --purchase (both tokens are spent; they must differ)"
[ "$TOKEN_1" != "$TOKEN_2" ] || die "TOKEN_1 and TOKEN_2 are the same file; the second purchase needs a fresh token"
for t in "$TOKEN_1" "$TOKEN_2"; do
  [ -s "$t" ] || die "token file is missing or empty: $t"
done

if [ -z "$BENCH_NIC" ]; then
  BENCH_NIC="$(detect_nic "${ROUTER_IP%.*}")" \
    || die "no wired NIC on ${ROUTER_IP%.*}.0/24 — set BENCH_NIC explicitly (Wi-Fi cannot carry a macvlan)"
fi
[ -d "/sys/class/net/$BENCH_NIC" ] || die "BENCH_NIC=$BENCH_NIC does not exist"
[ "$CLIENT_MAC" != "$(cat "/sys/class/net/$BENCH_NIC/address" 2>/dev/null || true)" ] \
  || die "CLIENT_MAC equals the host NIC's MAC — the run would authenticate the host"

# ---------------------------------------------------------------- take the bench lock
#
# Before the lock is taken: LOCAL checks only (tools, token files, the wired NIC). The
# lock comes next so that "another window owns the bench" is refused before we probe or
# touch anything, and so the state we then read is a state we own.

if [ "${BENCH_LOCK_HELD:-0}" != "1" ]; then
  printf 'preflight: taking the single-owner bench lock (re-exec under bench-lock.sh exec)\n'
  exec "$HERE/bench-lock.sh" exec --purpose "second-purchase e2e" -- \
    "$SELF" --purchase --log-dir "$LOG_DIR" --burn-rounds "$BURN_ROUNDS" \
    --client-mac "$CLIENT_MAC" --client-ip "$CLIENT_IP" --nic "$BENCH_NIC"
fi
"$HERE/bench-lock.sh" require || exit "$EX_LOCK"

# Router liveness is a TCP/HTTP read — this box DROPS ICMP, so never ping it.
STATUS_JSON="$(curl -s -m 6 "$API_BASE/" 2>/dev/null || true)"
case "$STATUS_JSON" in
  *'"kind":10021'*) printf 'preflight: backend reports kind:10021 (FULL mode)\n' ;;
  *kind*) die "backend is NOT in full mode (degraded/other): $(printf '%s' "$STATUS_JSON" | head -c 200)" ;;
  *) die "no answer from $API_BASE/ — is the bench up on $ROUTER_IP? (do not trust ping: it drops ICMP)" ;;
esac

# ---------------------------------------------------------------- transcript

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/e2e-$TS.log"
DL_LOG="$LOG_DIR/dl-$TS.log"
BUY_LOG="$LOG_DIR/buy-$TS.log"
if [ "$LOG_TO_FILE" = 1 ]; then
  printf 'second-purchase-e2e: transcript %s (tail -f to follow); download bytes %s\n' "$LOG" "$DL_LOG" >&2
  exec >>"$LOG" 2>&1
fi
if [ "$DETACHED" = 1 ]; then printf 'second-purchase-e2e: detached run, log %s\n' "$LOG" >&2; fi
: > "$DL_LOG"
: > "$BUY_LOG"

# ---------------------------------------------------------------- tokens (NUT-07, fail closed)

say "TOKEN PREFLIGHT (NUT-07 checkstate: an already-spent token burns the whole run)"
for t in "$TOKEN_1" "$TOKEN_2"; do
  out="$("$TOKENTOOL" verify --token-file "$t" 2>&1)"; rc=$?
  printf '%s\n' "$out"
  if [ "$rc" -ne 0 ]; then printf 'FATAL: %s is not spendable (NUT-07)\n' "$t"; exit "$EX_TOKEN"; fi
done

# ---------------------------------------------------------------- client plumbing

teardown_client() {
  sudo ip rule del from "$CLIENT_IP" table "$POLICY_TABLE" 2>/dev/null || true
  sudo ip rule del from "$CLIENT_IP" lookup "$POLICY_TABLE" 2>/dev/null || true
  sudo ip route flush table "$POLICY_TABLE" 2>/dev/null || true
  sudo ip addr del "$CLIENT_IP/32" dev "$CLIENT_VIF" 2>/dev/null || true
  sudo ip addr del "$CLIENT_IP/24" dev "$CLIENT_VIF" 2>/dev/null || true
  sudo ip link set "$CLIENT_VIF" down 2>/dev/null || true
  sudo ip link del "$CLIENT_VIF" 2>/dev/null || true
}

# A NetworkManager profile for the vif with autoconnect=yes re-creates the macvlan the
# instant we delete it — with a RANDOM cloned MAC, so the run would silently use the wrong
# identity ("ip link add: File exists"). Release it for the duration and say so loudly.
NM_HAD=0
NM_AC_ORIG=""
nm_release() {
  # plain `nmcli` is not allowed to deactivate a connection here ("Not authorized"); sudo is.
  if sudo nmcli -g connection.uuid con show "$CLIENT_VIF" >/dev/null 2>&1; then
    NM_HAD=1
    NM_AC_ORIG="$(sudo nmcli -g connection.autoconnect con show "$CLIENT_VIF" 2>/dev/null)"
    printf -- '--- NetworkManager owns %s (autoconnect=%s) -> disabling + deactivating\n' \
      "$CLIENT_VIF" "$NM_AC_ORIG"
    sudo nmcli con mod "$CLIENT_VIF" connection.autoconnect no || true
    sudo nmcli con down "$CLIENT_VIF" || true
    sleep 3
  else
    printf -- '--- no NetworkManager profile for %s\n' "$CLIENT_VIF"
  fi
}

cleanup() {
  local rc=$?
  teardown_client
  if [ "$NM_HAD" = 1 ]; then
    printf 'NOTE: NM profile %s was left DISABLED (autoconnect=no) so it cannot recreate\n' "$CLIENT_VIF"
    printf '      a stale wrong-MAC macvlan. Restore: nmcli con mod %s connection.autoconnect yes\n' "$CLIENT_VIF"
  fi
  say "torn down (rc=$rc, log ${LOG:-<none>})"
  exit "$rc"
}
trap cleanup EXIT

say "SETUP fresh-MAC client $CLIENT_MAC on $CLIENT_VIF (link $BENCH_NIC)"
# Leftover macvlan / ip-rule / ip-route state from a killed run makes the next run die with
# "RTNETLINK answers: File exists" — delete first, tolerate everything.
nm_release
teardown_client
i=1
while [ "$i" -le 5 ]; do
  ip -br link show "$CLIENT_VIF" >/dev/null 2>&1 || break
  printf '  waiting for stale %s to disappear (try %s)\n' "$CLIENT_VIF" "$i"
  sleep 2
  i=$((i + 1))
done
if ip -br link show "$CLIENT_VIF" >/dev/null 2>&1; then
  die "stale $CLIENT_VIF will not delete; a straggler or NetworkManager still owns it"
fi

sudo ip link add "$CLIENT_VIF" link "$BENCH_NIC" type macvlan mode bridge || die "ip link add failed"
sudo ip link set "$CLIENT_VIF" address "$CLIENT_MAC" || die "ip link set address failed"
sudo ip link set "$CLIENT_VIF" up || die "ip link set up failed"
sudo ip addr add "$CLIENT_IP/32" dev "$CLIENT_VIF" || die "ip addr add $CLIENT_IP/32 failed"
sudo ip route add "$ROUTER_IP/32" dev "$CLIENT_VIF" src "$CLIENT_IP" scope link table "$POLICY_TABLE" 2>/dev/null \
  || printf '  (policy host route already present)\n'
sudo ip route add default via "$ROUTER_IP" dev "$CLIENT_VIF" src "$CLIENT_IP" table "$POLICY_TABLE" 2>/dev/null \
  || printf '  (policy default route already present)\n'
sudo ip rule add from "$CLIENT_IP" table "$POLICY_TABLE" priority "$POLICY_PRIORITY" 2>/dev/null \
  || printf '  (policy rule already present)\n'
ip -br addr show "$CLIENT_VIF"
ip rule show | grep "$CLIENT_IP" || true
ip route show table "$POLICY_TABLE"

assert_eq "client MAC is the requested fresh MAC" "$CLIENT_MAC" \
  "$(cat "/sys/class/net/$CLIENT_VIF/address" 2>/dev/null || true)"
printf -- '--- host management path must still leave by %s: %s\n' "$BENCH_NIC" "$(ip route get "$ROUTER_IP" | head -1)"

# ---------------------------------------------------------------- probes / purchases

# --interface is not decoration: it is what makes the router resolve the client's MAC.
probe() { curl -s --interface "$CLIENT_IP" -m 8 -o /dev/null -w '%{http_code}' "$PROBE_URL"; }
probe_verbose() {
  curl -s --interface "$CLIENT_IP" -m 8 -o /dev/null \
    -w 'probe code=%{http_code} redirect=%{redirect_url}\n' "$PROBE_URL"
}
egress() {
  curl -s --interface "$CLIENT_IP" -m 20 -o /dev/null -w 'egress code=%{http_code} bytes=%{size_download}\n' "$EGRESS_URL"
}
balance() { curl -s --interface "$CLIENT_IP" -m 6 "$API_BASE/balance" || true; }

buy() {   # $1 token file  $2 label
  local body http
  say "PURCHASE $2 ($1)"
  body="$(curl -s --interface "$CLIENT_IP" -m 30 -X POST --data-binary "@$1" \
    -H 'Content-Type: text/plain' -w '\nHTTP=%{http_code}' "$API_BASE/")"
  http="$(printf '%s' "$body" | sed -n 's/^HTTP=//p' | tail -1)"
  printf '%s\n' "$body" | head -c 500
  printf '\n'
  { printf -- '--- buy %s %s\n' "$2" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; printf '%s\n' "$body"; } >> "$BUY_LOG"
  LAST_HTTP="$http"
  LAST_BODY="$body"
}

snap() { "$SNAP" snapshot --label "$1" --out "$LOG_DIR/snapshot-$TS.log"; }
run_on_router() { "$SNAP" run "$1" --out "$LOG_DIR/onrouter-$TS.log"; }

# The decisive module-log greps, read through the transport (one place, both phases).
router_log_grep() {   # $1 = extra grep -E pattern for the payload
  local sh out
  sh="$(mktemp "${TMPDIR:-/tmp}/loggrep.XXXXXX")"
  cat > "$sh" <<EOF
logread 2>/dev/null | grep -iE "$1" | tail -40
EOF
  out="$(run_on_router "$sh" 2>&1)"
  rm -f "$sh"
  printf '%s\n' "$out"
}

# ---------------------------------------------------------------- PHASE 0

say "PHASE 0 fresh-client baseline (expect 307 -> splash, and NO egress)"
probe_verbose
egress
printf -- '--- balance as seen BY THE CLIENT MAC: %s\n' "$(balance)"
snap "phase0-fresh-client-baseline"

# ---------------------------------------------------------------- PHASE 1

say "PHASE 1 FIRST PURCHASE"
buy "$TOKEN_1" "buy#1"
assert_eq "buy#1 answered HTTP 200" "200" "$LAST_HTTP"
assert_contains "buy#1 returned kind:1022" '"kind":1022' "$LAST_BODY"
ALLOTMENT_1="$(json_field allotment "$LAST_BODY")"
printf -- '--- allotment#1 = %s bytes\n' "${ALLOTMENT_1:-<none>}"

sleep 5
GATE=0
i=1
while [ "$i" -le "$PROBE_TRIES" ]; do
  p="$(probe)"
  printf '  gate probe %s: %s\n' "$i" "$p"
  case "$p" in 204|200) GATE=1; break ;; esac
  sleep 10
  i=$((i + 1))
done
if [ "$GATE" != 1 ]; then
  printf '!!! buy#1 did NOT open the gate — the setup/rail is broken, not the product\n'
  printf -- '--- balance by client MAC: %s\n' "$(balance)"
  snap "first-purchase-no-gate"
  exit "$EX_NO_GATE"
fi
probe_verbose
printf -- '--- balance as seen BY THE CLIENT MAC: %s\n' "$(balance)"
snap "after-first-purchase"

# ---------------------------------------------------------------- PHASE 2

say "PHASE 2 EXHAUST the allotment THROUGH the router"
# shellcheck disable=SC2086
set -- $BURN_URLS
urls=("$@")
if [ "${#urls[@]}" -eq 0 ] || [ -z "${urls[0]}" ]; then die "BURN_URLS is empty"; fi
printf -- '--- %s rounds x up to %s parallel downloads through the client interface\n' \
  "$BURN_ROUNDS" "$BURN_PARALLEL"
downloaded_b() { awk -F'bytes=' '{ gsub(/ .*/, "", $2); b += $2 } END { printf "%d", b + 0 }' "$DL_LOG"; }

CLOSED_AT=""
round=1
while [ "$round" -le "$BURN_ROUNDS" ]; do
  printf -- '--- round %s start bal=%s downloaded=%s B\n' "$round" "$(balance)" "$(downloaded_b)"
  pids=""
  i=0
  while [ "$i" -lt "$BURN_PARALLEL" ]; do
    url="${urls[$((i % ${#urls[@]}))]}"
    ( curl -s --interface "$CLIENT_IP" -m 300 -o /dev/null \
        -w "r$round s$i code=%{http_code} bytes=%{size_download}\n" "$url" >> "$DL_LOG" 2>&1 ) &
    pids="$pids $!"
    i=$((i + 1))
  done
  # shellcheck disable=SC2086
  wait $pids

  STRIKES=0
  t=1
  while [ "$t" -le "$WATCH_TRIES" ]; do
    sleep 15
    p="$(probe)"
    printf '  r%s t=%ss probe=%s bal=%s\n' "$round" "$((t * 15))" "$p" "$(balance)"
    case "$p" in
      204|200) STRIKES=0 ;;
      *) STRIKES=$((STRIKES + 1)); printf '  >>> strike %s/%s (probe=%s)\n' "$STRIKES" "$GATE_STRIKES" "$p" ;;
    esac
    if [ "$STRIKES" -ge "$GATE_STRIKES" ]; then
      CLOSED_AT="round=$round t=$((t * 15))s"
      printf '  >>> GATE CLOSED (%s)\n' "$CLOSED_AT"
      break
    fi
    t=$((t + 1))
  done
  if [ -n "$CLOSED_AT" ]; then break; fi
  round=$((round + 1))
done

TOTAL="$(awk -F'bytes=' '{ gsub(/ .*/, "", $2); b += $2 } END { printf "%.1f", (b + 0) / 1048576 }' "$DL_LOG")"
printf 'downloaded through the router: %s MiB\n' "$TOTAL"
if [ -z "$CLOSED_AT" ]; then
  printf '!!! HONEST RESULT: the gate never closed inside the budget (%s rounds) — INCONCLUSIVE\n' "$BURN_ROUNDS"
  snap "never-exhausted"
  exit "$EX_NO_EXHAUST"
fi
EXHAUST_LOG="$(router_log_grep 'allotment|closed gate|Removed expired session')"
printf '%s\n' "$EXHAUST_LOG"
assert_contains "module logged the allotment being reached" "allotment reached" "$EXHAUST_LOG"

# ---------------------------------------------------------------- PHASE 3

say "PHASE 3 POST-EXHAUSTION (the reported symptom: no internet, no prompt)"
probe_verbose
probe_verbose
printf -- '--- balance BY THE CLIENT MAC: %s\n' "$(balance)"
printf -- '--- balance from the box: %s\n' "$(curl -s -m 6 "$API_BASE/balance" || true)"
snap "post-exhaustion"

# ---------------------------------------------------------------- PHASE 4

say "PHASE 4 DISCRIMINATOR: ndsctl deauth $CLIENT_MAC (is a stale nodogsplash session keeping it shut?)"
DEAUTH_SH="$(mktemp "${TMPDIR:-/tmp}/deauth.XXXXXX")"
cat > "$DEAUTH_SH" <<EOF
echo "-- before"
ndsctl json 2>&1 | head -c 400; echo
ndsctl deauth $CLIENT_MAC; echo "rc=\$?"
sleep 3
echo "-- after"
ndsctl json 2>&1 | head -c 400; echo
EOF
DEAUTH_OUT="$(run_on_router "$DEAUTH_SH" 2>&1)"
rm -f "$DEAUTH_SH"
printf '%s\n' "$DEAUTH_OUT"
# "Client not found" (rc=1) is the finding: no stale nodogsplash session kept the gate shut.
assert_contains "deauth discriminator found no stale NDS session" "Client not found" "$DEAUTH_OUT"
sleep 4
probe_verbose
probe_verbose

# ---------------------------------------------------------------- PHASE 5

say "PHASE 5 SECOND PURCHASE (the money path: does the gate re-open?)"
buy "$TOKEN_2" "buy#2"
assert_eq "buy#2 answered HTTP 200" "200" "$LAST_HTTP"
assert_contains "buy#2 returned kind:1022" '"kind":1022' "$LAST_BODY"
ALLOTMENT_2="$(json_field allotment "$LAST_BODY")"
printf -- '--- allotment#2 = %s bytes (allotment#1 was %s)\n' "${ALLOTMENT_2:-<none>}" "${ALLOTMENT_1:-<none>}"

sleep 8
printf -- '--- DECISIVE PROBES (204/200 = gate re-opened, 307/000 = the bug is reproduced)\n'
P1="$(probe)"
P2="$(probe)"
printf '  probe1=%s probe2=%s\n' "$P1" "$P2"
egress
snap "after-second-purchase"
assert_eq "post-buy#2 probe 1 re-opened the gate" "open" "$(norm_probe "$P1")"
assert_eq "post-buy#2 probe 2 re-opened the gate" "open" "$(norm_probe "$P2")"

say "MODULE LOG (decisive greps)"
router_log_grep 'baseline|allotment|closed gate|raced|restore|unconfirmed|grant|authoriz'

# ---------------------------------------------------------------- verdict

say "VERDICT"
printf 'allotment#1=%s  allotment#2=%s  closed_at=%s  downloaded=%s MiB\n' \
  "${ALLOTMENT_1:-<none>}" "${ALLOTMENT_2:-<none>}" "$CLOSED_AT" "$TOTAL"
printf 'log=%s\n' "$LOG"
if [ "$(norm_probe "$P1")" != "open" ]; then
  printf 'RESULT: SECOND PURCHASE DID NOT RE-OPEN THE GATE (probe=%s) — bug reproduced.\n' "$P1"
  exit "$EX_NO_REOPEN"
fi
if [ "$FAILED" -ne 0 ]; then
  printf 'RESULT: assertions failed (%s) — see ASSERT FAIL lines above.\n' "$FAILED"
  exit "$EX_ASSERT"
fi
printf 'RESULT: PASS — in THIS configuration the second purchase re-opened the gate.\n'
printf 'NOTE: this proves the cashu-token lane only. A different lane (portal Lightning\n'
printf '      invoice), a one-step allotment and a Wi-Fi client remain untested — do NOT\n'
printf '      read this as "the operator report is fixed".\n'
exit "$EX_OK"
