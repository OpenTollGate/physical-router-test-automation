#!/bin/sh
# tg-router-health.sh — run ON the TollGate router. POSIX/busybox-ash safe.
#
# HOW TO RUN (from the laptop, one line — the < <(...) form is the LAPTOP's bash,
# not the router's shell):
#
#   ssh root@tollgate.lan 'sh -s' < <(curl -fsSL https://raw.githubusercontent.com/felixfelix-bot/physical-router-test-automation/main/scripts/diag/tg-router-health.sh)
#
# or two steps, works everywhere:
#   curl -fsSL <same url> -o /tmp/tg-router-health.sh
#   ssh root@tollgate.lan 'sh -s' < /tmp/tg-router-health.sh
#
# DO NOT use `bash <(curl ...)` for this file: bash would EXECUTE the script on the
# laptop and redirect its OUTPUT into the file you then send to the router.
#
# Exit: 0 = all checks pass, 1 = at least one FAIL.

fail=0
pass() { echo "  PASS  $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
info() { echo "  ----  $*"; }
hdr()  { echo ""; echo "=== $* ==="; }

hdr "identity"
[ -r /etc/openwrt_release ] && . /etc/openwrt_release && info "openwrt : $DISTRIB_ID $DISTRIB_RELEASE ($DISTRIB_TARGET)"
info "uptime  : $(uptime 2>/dev/null)"
PKG=$(apk list -I tollgate-wrt 2>/dev/null | head -1)
[ -n "$PKG" ] || PKG=$(opkg list-installed tollgate-wrt 2>/dev/null)
info "package : ${PKG:-NOT INSTALLED}"
info "payload sha256(16): $(sha256sum /usr/bin/tollgate-wrt 2>/dev/null | cut -c1-16)"
[ -f /etc/tollgate/install.json ] && info "install.json: $(cat /etc/tollgate/install.json)"

hdr "0. uci-defaults still present? (they self-delete after a successful boot run)"
if [ -d /etc/uci-defaults ]; then
    N=$(ls /etc/uci-defaults 2>/dev/null | wc -l)
    info "/etc/uci-defaults holds $N script(s): $(ls /etc/uci-defaults 2>/dev/null | tr '\n' ' ')"
    [ "$N" -eq 0 ] && info "(empty = both the install-time run and the boot run consumed them; the config they wrote is what you have now)"
else
    info "/etc/uci-defaults absent"
fi
[ -f /tmp/tollgate-setup.log ] && { echo "  ----  /tmp/tollgate-setup.log (last 25):"; tail -25 /tmp/tollgate-setup.log | sed 's/^/        /'; }

hdr "1. firewall guard chains"
if nft list chain inet fw4 nds_enforce_forward >/dev/null 2>&1; then
    pass "nds_enforce_forward exists"
    nft list chain inet fw4 nds_enforce_forward 2>/dev/null | grep -E 'counter|drop|reject|accept' | sed 's/^/        /'
else
    bad "nds_enforce_forward MISSING (20-nds-enforce.nft not loaded)"
fi
for ch in admin_board_input_guard; do
    if nft list chain inet fw4 "$ch" >/dev/null 2>&1; then pass "$ch exists"; else bad "$ch MISSING (31-admin-board-not-guest-reachable.nft not loaded)"; fi
done

hdr "2. nodogsplash (needed for OS captive-portal detection)"
NDPID=$(pidof nodogsplash 2>/dev/null)
if [ -n "$NDPID" ]; then pass "nodogsplash running (pid $NDPID)"; else bad "nodogsplash NOT running -> no HTTP interception -> no OS portal detection"; fi
info "uci enabled: $(uci -q get nodogsplash.@nodogsplash[0].enabled 2>/dev/null)"
info "uci gatewayport: $(uci -q get nodogsplash.@nodogsplash[0].gatewayport 2>/dev/null)"
NDC=$(nft list ruleset 2>/dev/null | grep -c 'nds')
[ "$NDC" -ge 1 ] && pass "nds* rules/chain matches in ruleset: $NDC" || bad "no nds* chain in the live ruleset"
if command -v ndsctl >/dev/null 2>&1; then echo "  ----  ndsctl status:"; ndsctl status 2>/dev/null | head -15 | sed 's/^/        /'; fi

hdr "3. IPv6 on LAN (must be off: #148 — Android validates over IPv6 and bypasses the portal)"
RA=$(uci -q get dhcp.lan.ra 2>/dev/null)
D6=$(uci -q get dhcp.lan.dhcpv6 2>/dev/null)
info "uci dhcp.lan.ra=${RA:-unset} dhcpv6=${D6:-unset}"
[ "$RA" = "disabled" ] && pass "RA disabled in uci" || bad "RA=${RA:-unset} (expected disabled)"
GLB=$(ip -6 addr show br-lan 2>/dev/null | grep -c 'scope global')
[ "$GLB" -eq 0 ] && pass "no global IPv6 on br-lan" || bad "$GLB global IPv6 address(es) on br-lan — clients can bypass the portal over IPv6"

hdr "4. uhttpd instances (:8090 admin board, :2051 portal, :8080 LuCI)"
for s in main admin portal net4sats; do
    H=$(uci -q get "uhttpd.$s.home" 2>/dev/null)
    L=$(uci -q get "uhttpd.$s.listen_http" 2>/dev/null | tr '\n' ' ')
    if [ -n "$H$L" ]; then info "uhttpd.$s  home=${H:-unset}  listen_http=${L:-unset}"; else info "uhttpd.$s  (absent)"; fi
done
T8090=$(uclient-fetch -q -O - -T 5 http://127.0.0.1:8090/ 2>/dev/null | grep -o '<title>[^<]*</title>' | head -1)
[ -n "$T8090" ] || T8090=$(wget -q -O - -T 5 http://127.0.0.1:8090/ 2>/dev/null | grep -o '<title>[^<]*</title>' | head -1)
[ -n "$T8090" ] || T8090=$(curl -s -m 5 http://127.0.0.1:8090/ 2>/dev/null | grep -o '<title>[^<]*</title>' | head -1)
case "$T8090" in
  *LuCI*|*OpenWrt*) bad ":8090 serves LuCI ($T8090) — expected the TollGate admin board";;
  "") bad ":8090 did not answer (expected the TollGate admin board)";;
  *) pass ":8090 serves $T8090";;
esac
C2051=$(uclient-fetch -q -O /dev/null -T 5 http://127.0.0.1:2051/splash.html 2>/dev/null; echo $?)
[ "$C2051" = "0" ] && pass ":2051 answers (captive-portal SPA)" || bad ":2051 did not answer"

hdr "5. listening sockets"
netstat -ltn 2>/dev/null | grep -E ':(22|80|443|2050|2051|2121|8080|8090|8443)[[:space:]]' | sed 's/^/        /'

hdr "6. TRUSTED CLIENTS — a tester whose MAC is exempt never sees the portal"
TM=$(uci -q get nodogsplash.@nodogsplash[0].trustedmac 2>/dev/null)
if [ -z "$TM" ]; then
    pass "nodogsplash trustedmac is empty (every client must authenticate)"
else
    bad "nodogsplash trustedmac is NOT empty: $TM"
    info "these clients bypass the captive portal completely: no HTTP interception, so no"
    info "OS sign-in prompt and free internet. A bench management-keepalive script (our"
    info "99z-mgmt-keepalive template) writes exactly this and is easy to leave behind."
    info "Before handing a router to a tester or the review club:"
    info "  for m in \$(uci get nodogsplash.@nodogsplash[0].trustedmac); do uci del_list nodogsplash.@nodogsplash[0].trustedmac=\"\$m\"; done"
    info "  uci commit nodogsplash; /etc/init.d/nodogsplash restart"
fi
LINGER=$(ndsctl clients 2>/dev/null | grep -cE '^client_id')
if [ "${LINGER:-0}" -gt 0 ]; then
    info "$LINGER client(s) still registered with nodogsplash:"
    ndsctl clients 2>/dev/null | grep -E '^(ip|mac|state)=' | sed 's/^/        /'
    info "deauth them with: ndsctl deauth <mac>   — a lingering authorised session keeps a"
    info "tester's own machine online without payment."
else
    pass "no clients registered with nodogsplash (clean slate)"
fi

hdr "7. gateway service + wallet"
/etc/init.d/tollgate-wrt status 2>/dev/null || info "tollgate-wrt has no status verb"
info "wallet: $(tollgate wallet balance 2>&1 | head -3)"

echo ""
if [ "$fail" -eq 0 ]; then
    echo "=== verdict: ALL CHECKS PASS ==="
else
    echo "=== verdict: FAILURES ABOVE ==="
    echo "A FAIL that disappears after a reboot still proves the install-path defect."
    echo "A FAIL that SURVIVES a reboot is the more interesting case: the config was never"
    echo "written (not merely not applied) — report /etc/uci-defaults/ and /tmp/tollgate-setup.log."
fi
exit $fail
