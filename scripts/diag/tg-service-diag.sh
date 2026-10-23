#!/bin/sh
# tg-service-diag.sh -- why is the TollGate money path dead on this router?
#
# The guest portal can be perfect while the COMMERCIAL half is gone: the Go
# daemon owns :2121 (the API the portal pays against) and /var/run/tollgate.sock
# (the socket the `tollgate` CLI talks to). If either is missing, the box looks
# healthy from the captive-portal side and cannot sell anything.
#
# Measured case (bench MT3000, pre17, fresh install + reboot): firewall guard
# chains present, nodogsplash up, :2050/:2051/:8090/:8080/:443/:8443 all
# listening -- and NO :2121, NO socket, so `tollgate wallet balance` fails with
# "dial unix /var/run/tollgate.sock: connect: no such file or directory" while
# the init script still reports the service as running.
#
# READ-ONLY except one controlled restart, and only when the API is down (that
# restart is the natural fix; the script re-checks and reports what happened).
# No credentials are printed: uci values are redacted for anything secret-ish.
#
# Run:  ssh root@<router> 'sh -s' < <(curl -fsSL <url-of-this-script>)
# Exit: always 0 (it is a report, not a gate).

redact() { sed -E 's/(seed|mnemonic|privkey|private_key|nsec|token|password|passwd)([= ])[^ ]+/\1\2<redacted>/Ig'; }

API_PORT=2121
OK=0; BAD=0
say()  { printf '%s\n' "$*"; }
pass() { OK=$((OK+1));  printf ' PASS %s\n' "$*"; }
fail() { BAD=$((BAD+1)); printf ' FAIL %s\n' "$*"; }

have_port() { netstat -tln 2>/dev/null | grep -q ":$1 "; }

# --- 1. identity -----------------------------------------------------------
say "=== identity ==="
say " ---- date        : $(date)"
say " ---- /proc/uptime: $(cat /proc/uptime 2>/dev/null)"
say " ---- uptime      : $(uptime 2>/dev/null | sed 's/^ *//')"
say " ---- package     : $(apk list --installed 2>/dev/null | grep tollgate-wrt || echo 'tollgate-wrt NOT installed')"
say " ---- installed at: $(grep -o '"install_time"[^,]*' /etc/tollgate/install.json 2>/dev/null)"
say " ---- payload sha : $(sha256sum /etc/tollgate/*.apk 2>/dev/null | cut -c1-16 || true)"
say " ---- last reboot : $(logread 2>/dev/null | grep -i 'OpenWrt' | tail -1 | cut -c1-60)"

# --- 2. the service -------------------------------------------------------
say ""
say "=== 1. the tollgate-wrt service ==="
if [ -x /etc/init.d/tollgate-wrt ]; then
  st=$(/etc/init.d/tollgate-wrt status 2>&1); rc=$?
  say " ---- status: '$st' rc=$rc"
else
  fail "/etc/init.d/tollgate-wrt is missing"
fi
ps w 2>/dev/null | grep -i '[t]ollgate' | awk '{print " ---- ps: "$0}' | head -6
say " ---- procd instance: $(ubus call service list '{"name":"tollgate-wrt"}' 2>/dev/null | head -c 400)"

# --- 3. runtime socket ----------------------------------------------------
say ""
say "=== 2. control socket (/var/run/tollgate.sock) ==="
if [ -S /var/run/tollgate.sock ]; then
  pass "socket exists: $(ls -l /var/run/tollgate.sock)"
else
  fail "socket MISSING -- every 'tollgate ...' CLI call will fail with 'no such file or directory'"
  say " ---- /var/run contents:"; ls -l /var/run/ 2>/dev/null | head -12
fi

# --- 4. listeners ---------------------------------------------------------
say ""
say "=== 3. listeners (API :$API_PORT is what the portal pays against) ==="
netstat -tln 2>/dev/null | grep LISTEN | sed 's/^/    /'
if have_port $API_PORT; then pass ":$API_PORT listening"; else fail ":$API_PORT NOT listening -- no payment path at all"; fi
for p in 2050 2051 8080 8090 443 8443; do have_port $p && say " ---- :$p listening" || say " ---- :$p absent (may be normal)"; done

# --- 5. local API probe ---------------------------------------------------
say ""
say "=== 4. local API probe ==="
if command -v curl >/dev/null 2>&1; then
  body=$(curl -sS -m 4 "http://127.0.0.1:$API_PORT/" 2>&1 | head -c 300)
elif command -v uclient-fetch >/dev/null 2>&1; then
  body=$(uclient-fetch -q -T 4 -O - "http://127.0.0.1:$API_PORT/" 2>&1 | head -c 300)
else
  body="(no curl and no uclient-fetch on this box)"
fi
say " ---- GET http://127.0.0.1:$API_PORT/ -> $body"
say " ---- wallet CLI: $(tollgate wallet balance 2>&1 | head -3 | tr '\n' ' ')"

# --- 6. logs --------------------------------------------------------------
say ""
say "=== 5. logs (tollgate) ==="
logread 2>/dev/null | grep -iE 'tollgate' | tail -30 | sed 's/^/    /'
say " ---- crash-ish lines:"
logread 2>/dev/null | grep -iE 'segfault|signal [0-9]|panic|out of memory|killed' | tail -8 | sed 's/^/    /'

# --- 7. config (redacted) -------------------------------------------------
say ""
say "=== 6. config (secrets redacted) ==="
uci show tollgate 2>/dev/null | redact | sed 's/^/    /' | head -40
say " ---- enabled services: $(for s in tollgate-wrt nodogsplash firewall dnsmasq uhttpd; do printf '%s=%s ' "$s" "$(/etc/init.d/$s enabled && echo y || echo n)"; done)"

# --- 8. controlled restart (only if the API is down) ----------------------
say ""
say "=== 7. recovery attempt ==="
if have_port $API_PORT && [ -S /var/run/tollgate.sock ]; then
  say " ---- API and socket are up; nothing to recover. No action taken."
else
  say " ---- API/socket down. Attempting: /etc/init.d/tollgate-wrt stop; sleep 1; start"
  /etc/init.d/tollgate-wrt stop 2>&1 | sed 's/^/    stop: /'
  sleep 1
  /etc/init.d/tollgate-wrt start 2>&1 | sed 's/^/    start: /'
  sleep 4
  if have_port $API_PORT; then pass "after restart :$API_PORT is listening"; else fail "after restart :$API_PORT STILL not listening"; fi
  if [ -S /var/run/tollgate.sock ]; then pass "after restart the socket exists"; else fail "after restart the socket is STILL missing"; fi
  say " ---- wallet CLI after restart: $(tollgate wallet balance 2>&1 | head -3 | tr '\n' ' ')"
  say " ---- fresh logs after the restart:"
  logread 2>/dev/null | grep -iE 'tollgate' | tail -12 | sed 's/^/    /'
fi

# --- verdict --------------------------------------------------------------
say ""
if have_port $API_PORT && [ -S /var/run/tollgate.sock ]; then
  say "=== verdict: MONEY PATH UP ($OK pass / $BAD fail) ==="
else
  say "=== verdict: MONEY PATH DOWN ($OK pass / $BAD fail) -- the portal can render, but nothing can be paid ==="
fi

