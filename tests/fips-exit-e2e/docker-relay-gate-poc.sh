#!/bin/bash
# FIPS TollGate Relay Gate — Docker Proof of Concept
#
# Tests the core concept: relay node gates transit traffic based on payment state.
# Three containers simulate the architecture:
#
#   [customer] --net--> [relay/tollgate] --net--> [exit/NAT] --> internet
#
# Payment gate: relay uses nftables to allow/drop forwarding from customer.
# When UNPAID: customer cannot reach internet through relay.
# When PAID: customer can reach internet through relay.
#
# This proves the concept. Real FIPS integration puts the gate inside
# handle_session_datagram() in forwarding.rs, checking src_addr against
# a payment state map before calling find_next_hop().

set -euo pipefail

NETWORK="fips-tollgate-test"
SUBNET="10.99.0.0/24"
CUSTOMER_IP="10.99.0.10"
RELAY_IP="10.99.0.20"
EXIT_IP="10.99.0.30"
TARGET="1.1.1.1"

cleanup() {
    docker rm -f fips-customer fips-relay fips-exit 2>/dev/null || true
    docker network rm "$NETWORK" 2>/dev/null || true
}

trap cleanup EXIT
cleanup

echo "=== Creating network ==="
docker network create --subnet="$SUBNET" "$NETWORK"

echo "=== Starting exit node (NAT to internet) ==="
docker run -d --name fips-exit --network "$NETWORK" --ip "$EXIT_IP" \
    --cap-add NET_ADMIN \
    --sysctl net.ipv4.ip_forward=1 \
    debian:trixie-slim sleep infinity

# Set up MASQUERADE on exit
docker exec fips-exit apt-get update -qq
docker exec fips-exit apt-get install -y -qq iptables curl iputils-ping >/dev/null 2>&1
docker exec fips-exit iptables -t nat -A POSTROUTING -s "$SUBNET" -o eth0 -j MASQUERADE
docker exec fips-exit iptables -A FORWARD -i eth0 -o eth0 -j ACCEPT
echo "Exit node MASQUERADE configured"

echo "=== Starting relay node (TollGate) ==="
docker run -d --name fips-relay --network "$NETWORK" --ip "$RELAY_IP" \
    --cap-add NET_ADMIN \
    --sysctl net.ipv4.ip_forward=1 \
    debian:trixie-slim sleep infinity

docker exec fips-relay apt-get update -qq
docker exec fips-relay apt-get install -y -qq iptables nftables curl iputils-ping iproute2 >/dev/null 2>&1
# Default route through exit node (replace Docker bridge gateway)
docker exec fips-relay ip route del default 2>/dev/null || true
docker exec fips-relay ip route add default via "$EXIT_IP"

# START UNPAID: drop forwarding from customer
docker exec fips-relay nft add table inet tollgate
docker exec fips-relay nft 'add chain inet tollgate forward { type filter hook forward priority 0 ; }'
docker exec fips-relay nft add rule inet tollgate forward ip saddr "$CUSTOMER_IP" drop
echo "Relay configured. Customer UNPAID (traffic blocked)."

echo "=== Starting customer node ==="
docker run -d --name fips-customer --network "$NETWORK" --ip "$CUSTOMER_IP" \
    --cap-add NET_ADMIN \
    debian:trixie-slim sleep infinity

docker exec fips-customer apt-get update -qq
docker exec fips-customer apt-get install -y -qq curl iputils-ping iproute2 >/dev/null 2>&1
# Default route through relay (replace Docker bridge gateway)
docker exec fips-customer ip route del default 2>/dev/null || true
docker exec fips-customer ip route add default via "$RELAY_IP"
echo "Customer configured. Default route via relay."

echo ""
echo "========================================"
echo "TEST 1: UNPAID — customer should FAIL"
echo "========================================"
if docker exec fips-customer curl -s --connect-timeout 5 "https://$TARGET" -o /dev/null -w "HTTP %{http_code}" 2>/dev/null; then
    echo ""
    echo "FAIL: Customer reached internet while UNPAID!"
    exit 1
else
    echo "PASS: Customer blocked (unpaid). No internet."
fi

echo ""
echo "========================================"
echo "TEST 2: PAID — customer should SUCCEED"
echo "========================================"

# Simulate payment: flush and accept
docker exec fips-relay nft flush table inet tollgate
docker exec fips-relay nft 'add chain inet tollgate forward { type filter hook forward priority 0 ; }'
docker exec fips-relay nft add rule inet tollgate forward ip saddr "$CUSTOMER_IP" accept
echo "Payment received! Customer NOW PAID."

sleep 1
HTTP_CODE=$(docker exec fips-customer curl -s --connect-timeout 5 "https://$TARGET" -o /dev/null -w "%{http_code}" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" != "000" ]; then
    echo "PASS: Customer reached internet (HTTP $HTTP_CODE) while PAID."
else
    echo "FAIL: Customer could not reach internet while PAID."
    echo "--- Debug ---"
    docker exec fips-relay ip route 2>/dev/null || true
    docker exec fips-exit iptables -t nat -L -v 2>/dev/null || true
    docker exec fips-relay cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
    exit 1
fi

echo ""
echo "========================================"
echo "TEST 3: Payment EXPIRED — blocked again"
echo "========================================"

# Simulate payment expiry
docker exec fips-relay nft flush table inet tollgate
docker exec fips-relay nft 'add chain inet tollgate forward { type filter hook forward priority 0 ; }'
docker exec fips-relay nft add rule inet tollgate forward ip saddr "$CUSTOMER_IP" drop
echo "Payment EXPIRED. Customer blocked."

sleep 1
if docker exec fips-customer curl -s --connect-timeout 5 "https://$TARGET" -o /dev/null -w "HTTP %{http_code}" 2>/dev/null; then
    echo "FAIL: Customer reached internet after payment EXPIRED!"
    exit 1
else
    echo "PASS: Customer blocked (payment expired)."
fi

echo ""
echo "========================================"
echo "ALL 3 TESTS PASSED"
echo "========================================"
echo ""
echo "Concept proven: relay node gates transit traffic by payment state."
echo ""
echo "Architecture mapping:"
echo "  customer container  = FIPS mesh peer (customer phone)"
echo "  relay container     = FIPS relay node (tollgate phone)"
echo "  exit container      = VPS1 exit node"
echo "  nftables gate       = forwarding.rs handle_session_datagram()"
echo "  payment simulation  = Cashu token verification"
echo ""
echo "Next step: implement payment gate inside FIPS daemon"
echo "(check src_addr against payment map before find_next_hop)"
