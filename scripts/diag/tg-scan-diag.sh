#!/usr/bin/env bash
# tg-scan-diag.sh — reproduce the tollgate-installer's router discovery on THIS host,
# then show what the installer does NOT look at (its blind spot).
#
# Why: the wizard reports "Scan failed. Check that the wizard can reach your
# network." That message names no address it tried. This script prints every
# address it would try, the result of every probe, and — when nothing answers —
# the addresses your machine's own routing table says the router must be on.
#
# Usage:  bash tg-scan-diag.sh [extra-ip ...]
# Exit:   0 = a router was found, 1 = nothing answered, 2 = no usable interface

set -uo pipefail

COMMON_IPS=(192.168.1.1 192.168.8.1 192.168.0.1 192.168.2.1 10.47.41.1 192.168.21.1)
SSH_TIMEOUT=2   # installer uses 2s for :22
HTTP_TIMEOUT=1  # installer uses 1s for :80/:443/:8080

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m%s\033[0m\n' "$*"; }

# --- minimal tcp probe (no nc dependency; works on busybox+glibc hosts) -------
tcp_probe() {
  local ip="$1" port="$2" tmo="$3"
  if command -v nc >/dev/null 2>&1; then
    nc -z -w "$tmo" "$ip" "$port" >/dev/null 2>&1
    return $?
  fi
  timeout "$tmo" bash -c "exec 3<>/dev/tcp/${ip}/${port}" 2>/dev/null
}

probe_router() {
  local ip="$1" ssh=no http=""
  tcp_probe "$ip" 22 "$SSH_TIMEOUT" && ssh=yes
  for p in 80 443 8080; do
    if tcp_probe "$ip" "$p" "$HTTP_TIMEOUT"; then http="$p"; break; fi
  done
  if [[ "$ssh" == yes || -n "$http" ]]; then
    ok "  FOUND   $ip   ssh=$ssh http=${http:-none}"
    return 0
  fi
  printf '  no      %s   (ssh/timeout, http/timeout)\n' "$ip"
  return 1
}

bold "=== 1. interfaces (the installer probes from HERE) ==="
printf '%-18s %-10s %-6s %s\n' IFACE STATE CARRIER ADDRESSES
while read -r iface state; do
  [[ "$iface" == "lo" ]] && continue
  carrier=$(cat "/sys/class/net/$iface/carrier" 2>/dev/null || echo '?')
  addrs=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | paste -sd, -)
  printf '%-18s %-10s %-6s %s\n' "$iface" "$state" "$carrier" "${addrs:-—}"
  if [[ "$carrier" == "0" ]]; then
    warn "    ^ no link (cable unplugged / router powered off / wrong port)"
  elif [[ "$state" == "UP" || "$state" == "UNKNOWN" ]] && [[ -n "$carrier" ]] && [[ -z "$addrs" ]]; then
    warn "    ^ link up but NO IPv4 — the router is not handing out DHCP on this port"
  fi
done < <(ip -br link 2>/dev/null | awk '{print $1, $2}' | grep -Ev '^(lo|docker|br-|veth|mon|fips|wt)' || true)

echo
bold "=== 2. routing (where probes to 192.168.x.x actually go) ==="
ip route show 2>/dev/null | sed 's/^/  /' || echo "  (no routes)"
GW=$(ip route show default 2>/dev/null | awk '/^default/{print $3; exit}')
echo "  default gateway: ${GW:-none}"

echo
bold "=== 3. neighbour table (the installer's second source of candidates) ==="
NEIGH=$(ip neigh show 2>/dev/null | grep -v '^fe80' || true)
if [[ -z "$NEIGH" ]]; then
  warn "  EMPTY — this host has never exchanged traffic with a router."
  warn "  The installer's ARP source contributes nothing; only its 6 hard-coded IPs remain."
else
  echo "$NEIGH" | sed 's/^/  /'
fi

echo
bold "=== 4. what the installer probes (6 hard-coded IPs + ARP entries) ==="
FOUND=0
CANDIDATES=("${COMMON_IPS[@]}")
while read -r ip; do
  [[ -z "$ip" ]] && continue
  for c in "${CANDIDATES[@]}"; do [[ "$c" == "$ip" ]] && continue 2; done
  CANDIDATES+=("$ip")
done < <(echo "$NEIGH" | awk '{print $1}' | grep -E '^[0-9]+\.')

for ip in "${CANDIDATES[@]}"; do
  probe_router "$ip" && FOUND=1
done

echo
bold "=== 5. the installer's BLIND SPOT — addresses your own routing table proves ==="
DERIVED=()
if [[ -n "${GW:-}" ]]; then
  DERIVED+=("$GW   <- default route gateway (DHCP told you this is your router)")
fi
while read -r cidr iface; do
  [[ -z "$cidr" ]] && continue
  case "$iface" in lo|docker*|br-*|veth*|wt0|fips*) continue ;; esac
  base=$(echo "$cidr" | cut -d/ -f1)
  net1=$(echo "$base" | awk -F. '{print $1"."$2"."$3".1"}')
  net254=$(echo "$base" | awk -F. '{print $1"."$2"."$3".254"}')
  [[ "$net1" == "$base" ]] && continue
  DERIVED+=("$net1   <- subnet .1 of $iface ($cidr)")
  DERIVED+=("$net254   <- subnet .254 of $iface ($cidr)")
done < <(ip -4 -o addr show 2>/dev/null | awk '{print $4, $2}')

if [[ ${#DERIVED[@]} -eq 0 ]]; then
  echo "  (none — no IPv4 anywhere except loopback/containers)"
else
  declare -A SEEN=()
  for d in "${DERIVED[@]}"; do
    ip="${d%% *}"
    [[ -n "${SEEN[$ip]:-}" ]] && continue
    SEEN[$ip]=1
    if tcp_probe "$ip" 22 "$SSH_TIMEOUT" || tcp_probe "$ip" 80 "$HTTP_TIMEOUT" || tcp_probe "$ip" 8080 "$HTTP_TIMEOUT"; then
      ok "  ANSWERED  $d"
      FOUND=1
    else
      printf '  silent    %s\n' "$d"
    fi
  done
fi

for extra in "$@"; do
  probe_router "$extra" && FOUND=1
done

echo
bold "=== verdict ==="
if [[ $FOUND -eq 1 ]]; then
  ok "A router answered. Use its address in the installer's headless form:"
  echo "  bash <(curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh) <IP> '' <you@coinos.io>"
  exit 0
fi

warn "Nothing answered on :22/:80/:443/:8080 — the wizard is right, and here is why:"
echo
echo "  Check, in this order:"
echo "  1. GL-MT3000 ports: 2.5 GbE = WAN, 1 GbE = LAN.  Use the LAN port."
echo "     Plugged into WAN you get no DHCP lease and no route → every probe dies."
echo "  2. Router powered and booted (fresh flash needs 60-120 s; LED steady)."
echo "  3. Link and lease on the wired interface, from section 1 above:"
echo "       carrier=1 AND an IPv4 in the router's subnet."
echo "       No IPv4 -> force one:  sudo dhclient -v <iface>   (or reconnect in the desktop UI)"
echo "  4. No tunnel (VPN/mesh) owning the 192.168.x route — section 2."
echo "  5. Vanilla OpenWrt ships no LuCI: :80/:8080 stay closed, only :22 answers."
echo "  6. If the router's LAN is a derived 10.x TollGate address, it is outside the"
echo "     installer's hard-coded list and only reachable via ARP — section 5 shows it."
echo
echo "  Then hand the installer the address directly (skips discovery entirely):"
echo "    bash <(curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh) <ROUTER-IP> '' <you@coinos.io>"
exit 1
