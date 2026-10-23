#!/bin/sh
# tg-reopen-diag.sh -- a client paid, the balance came back, the gate did not.
#
# Also covers: captive-portal detection not firing for a client that has already been
# through a paid session, and :8090 serving LuCI instead of the TollGate admin board.
#
# READ-ONLY by default. Nothing is restarted, deauthed or changed unless you export
# TG_FIX=1 (prints the repair commands first either way).
#
# Usage:
#   ssh root@<router> 'sh -s' < <(curl -fsSL <url>)
#   ssh root@<router> 'sh -s <client-mac>' < <(curl -fsSL <url>)     # pin the client
#   TG_FIX=1 ... (same) to also APPLY the two documented repairs at the end
#
# Exit 0 always: this is a report.

MAC="${1:-}"
OK=0; BAD=0
pass()  { OK=$((OK+1));  printf ' PASS %s\n' "$*"; }
fail()  { BAD=$((BAD+1)); printf ' FAIL %s\n' "$*"; }
say()   { printf '%s\n' "$*"; }
sec()   { printf '\n===== %s =====\n' "$*"; }
redact(){ sed -E 's/(seed|mnemonic|privkey|private_key|nsec|token|password|passwd)([=": ])[^ "]+/\1\2<redacted>/Ig'; }

# a fetch helper whose flags differ per box
fetch() {  # $1=url
  if command -v curl >/dev/null 2>&1; then curl -sS -m 6 "$1" 2>&1
  elif command -v uclient-fetch >/dev/null 2>&1; then uclient-fetch -q -T 6 -O - "$1" 2>&1
  else wget -qO- -T 6 "$1" 2>&1; fi
}

sec "0. identity"
say " date      : $(date)"
say " uptime    : $(cat /proc/uptime)  ($(uptime | sed 's/^ *//'))"
say " package   : $(apk list --installed 2>/dev/null | grep tollgate-wrt || echo MISSING)"
say " uci-defs  : $(ls /etc/uci-defaults/ 2>/dev/null | tr '\n' ' ')(empty = already consumed)"
say " socket    : $([ -S /var/run/tollgate.sock ] && echo present || echo MISSING)"
say " API :2121 : $(netstat -tln 2>/dev/null | grep -c ':2121 ')"

sec "1. the client under test"
[ -n "$MAC" ] || MAC=$(ndsctl clients 2>/dev/null | sed -n 's/^mac=//p' | tail -1)
say " client mac: ${MAC:-<none found>}"
if [ -n "$MAC" ]; then
  say " --- nodogsplash's view:"
  ndsctl clients 2>/dev/null | tr '\n' '|' | sed 's/|Client/\n/g' | grep -i -A0 "$MAC" | tr '|' '\n' | sed 's/^/    /'
  say " --- (all clients are printed in section 2; this is the subset matching $MAC)"
fi

sec "2. nodogsplash status"
ndsctl status 2>/dev/null | sed 's/^/    /'
say " ---- ndsctl clients (all):"
ndsctl clients 2>/dev/null | sed 's/^/    /'

sec "3. what the firewall is doing to this client"
say " ---- the guard chain, WITH COUNTERS (packets grew = something is being dropped/rejected here):"
nft list chain inet fw4 nds_enforce_forward 2>/dev/null | sed 's/^/    /'
say " ---- rules or sets that mention the client's MAC:"
if [ -n "$MAC" ]; then
  nft -a list ruleset 2>/dev/null | grep -i -B2 -A2 "$MAC" | head -30 | sed 's/^/    /'
  say " ---- sets containing it:"
  for s in $(nft list sets 2>/dev/null | awk '/set /{print $2}'); do
    if nft list set inet fw4 "$s" 2>/dev/null | grep -qi "$MAC"; then
      say "      in set: $s"; nft list set inet fw4 "$s" 2>/dev/null | grep -i "$MAC" | head -3 | sed 's/^/        /'
    fi
  done
fi
say " ---- tollgate/meter-related ruleset lines:"
nft list ruleset 2>/dev/null | grep -iE 'tollgate|meter|authoriz|authed' | head -25 | sed 's/^/    /'

sec "4. the module's own state"
say " GET /          : $(fetch http://127.0.0.1:2121/ | head -c 200)"
say " balance CLI    : $(tollgate wallet balance 2>&1 | head -3 | tr '\n' ' ')"
say " CLI subcommands: $(tollgate --help 2>&1 | sed -n '2,12p' | tr '\n' ' ')"
say " /etc/tollgate  : $(ls /etc/tollgate/ 2>/dev/null | tr '\n' ' ')"
if [ -n "$MAC" ]; then say " sessions (cli) : $(tollgate sessions 2>&1 | head -12 | tr '\n' ' ')"; fi

sec "4b. THE SECOND PURCHASE — did the money actually arrive?"
say " wallet (total)  : $(tollgate wallet balance 2>&1 | tr '\n' ' ')"
say " wallet subcmds  : $(tollgate wallet --help 2>&1 | sed -n '1,14p' | tr '\n' ' ')"
say " sessions        : $(tollgate sessions 2>&1 | head -16 | tr '\n' ' ')"
say " /etc/tollgate files + mtimes (a real purchase must touch at least one):"
ls -l /etc/tollgate/ 2>/dev/null | sed 's/^/    /'
say " API /balance    : $(fetch http://127.0.0.1:2121/balance | head -c 300)"
say " API /usage      : $(fetch http://127.0.0.1:2121/usage | head -c 300)"
say " ---- payment-related log lines:"
logread 2>/dev/null | grep -iE 'invoice|redeem|receiv|melt|proof|cashu|1022|session|topup|allot|payment|swap' | tail -50 | sed 's/^/    /'
say " ---- how many payment/session events the log holds in total:"
say "      $(logread 2>/dev/null | grep -icE 'invoice|redeem|session event|kind:1022')"
say " ---- per-mint balances (the payout lines above print them every minute):"
logread 2>/dev/null | grep -i 'Skipping payout' | tail -12 | sed 's/^/    /'

sec "5. the admin board on :8090 (why LuCI instead of TollGate Admin?)"
say " ---- uci show uhttpd:"
uci show uhttpd 2>/dev/null | sed 's/^/    /'
say " ---- what :8090 ACTUALLY serves:"
fetch http://127.0.0.1:8090/ | head -c 300 | sed 's/^/    /'
say ""
say " ---- HTTP status/redirect for http://127.0.0.1:8090/ :"
if command -v curl >/dev/null 2>&1; then
  curl -s -o /dev/null -w '    code=%{http_code} redirect=%{redirect_url}\n' -m 5 http://127.0.0.1:8090/
else
  uclient-fetch -q -T 5 -O /dev/null http://127.0.0.1:8090/ 2>&1 | sed 's/^/    /'
fi
say " ---- admin SPA present? /www/tollgate:"
ls -l /www/tollgate 2>/dev/null | head -6 | sed 's/^/    /'
say " ---- setup script still on disk anywhere?"
find /etc /www /usr/lib /root /tmp -maxdepth 5 -name '92-tollgate-admin-setup*' 2>/dev/null | head -5 | sed 's/^/    /' 

sec "6. DNS + how the operator reaches it"
say " nslookup tollgate.lan: $(nslookup tollgate.lan 127.0.0.1 2>&1 | tr '\n' ' ')"
say " dhcp/tollgate uci   : $(uci show dhcp 2>/dev/null | grep -iE 'tollgate|domain|lan\.' | head -8 | tr '\n' ' ')"

sec "7. logs around the payment"
logread 2>/dev/null | grep -iE 'tollgate|nodogsplash|ndsctl|session|allot|meter|payment|cashu|invoice' | tail -40 | sed 's/^/    /'

sec "8. the two repairs (workaround, not the fix)"
cat <<'REPAIR'
  A) ADMIN BOARD (restores what 92-tollgate-admin-setup writes):
       uci set uhttpd.admin.home='/www/tollgate'
       uci -q delete uhttpd.admin.cgi_prefix
       uci -q delete uhttpd.admin.lua_prefix
       uci commit uhttpd && /etc/init.d/uhttpd restart
       # then: http://<router>:8090/ must answer with <title>TollGate Admin</title>
       # NOTE: run this AFTER capturing section 5 above — the stale config is the evidence.

  HOW THE GATE ACTUALLY WORKS (packaging/files/etc/nftables.d/20-nds-enforce.nft):
    NoDogSplash writes a mark in mangle PREROUTING, and the firewall only READS it:
      0x10000 = pre-authenticated -> DROP
      0x20000 = trusted           -> accept
      0x30000 = authenticated     -> accept
      unmarked                    -> reject (icmp port-unreachable)
    So "the gate" is NDS's mark for that MAC. A balance in the module does NOT
    open it by itself: something must make NDS mark the client 0x30000.

  B) THE STUCK CLIENT — the decisive experiment (run it in TWO steps, report both):
       B1  ndsctl clients                       # what state is YOUR mac in?
       B2  ndsctl auth <client-mac>             # force NDS to authorise it
           then browse on the client WITHOUT paying again.
       OUTCOMES:
        - B1 shows the mac Preauthenticated while the module says the balance is
          restored, and B2 restores the internet
             => PROVEN: the top-up grants balance but never re-authorises the client
                in NDS. The gate is shut because nobody called ndsctl auth. Report verbatim.
        - B1 shows Authenticated and there is still no internet
             => the block is NOT the mark; it is upstream of it — capture the guard
                chain counters from section 3 and the module's session state.
        - B2 changes nothing AND B1 shows nothing registered
             => NDS has no client entry for the mac (see section 2): the client is
                unmarked, which is the "reject" rule, and NDS is not intercepting it.
REPAIR

if [ "${TG_FIX:-0}" = "1" ]; then
  sec "9. APPLYING the repairs (TG_FIX=1)"
  uci set uhttpd.admin.home='/www/tollgate'
  uci -q delete uhttpd.admin.cgi_prefix
  uci -q delete uhttpd.admin.lua_prefix
  uci commit uhttpd
  /etc/init.d/uhttpd restart
  sleep 2
  say " after uhttpd restart, :8090 serves: $(fetch http://127.0.0.1:8090/ | head -c 120)"
  [ -n "$MAC" ] && { ndsctl deauth "$MAC"; say " deauthed $MAC (reload the portal on the client)"; }
fi

say ""
say "=== verdict: $OK pass / $BAD fail ==="
say "Send the WHOLE output, plus these FOUR facts only you have:"
say "  1. did you restart the service (or reboot) between the failed and the working :2121?"
say "     (the box says the socket was created at a time well after boot — that matters)"
say "  2. on the portal: what did the SECOND purchase say it charged (sats) and from which mint,"
say "     and what did the wallet say immediately BEFORE vs AFTER it?"
say "  3. did the client's OS ever show a sign-in prompt, or did it silently have no internet?"
say "  4. for :8090 — from a terminal on your laptop, NOT a browser:"
say "       curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://tollgate.lan:8090/"
say "       curl -s http://tollgate.lan:8090/ | grep -o '<title>[^<]*'"
say "     (a browser can keep a cached LuCI redirect or force HTTPS; curl cannot)"
exit 0
