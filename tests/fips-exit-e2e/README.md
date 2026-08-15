# FIPS Exit Node E2E Tests

This directory contains end-to-end tests for FIPS exit node functionality, integrated from [felixfelix-bot/fips-exit-e2e](https://github.com/felixfelix-bot/fips-exit-e2e).

## What This Tests

The FIPS exit node tests verify:

1. **FIPS Mesh Exit Node Routing**: Tests that FIPS mesh peers can connect to a VPS exit node and route traffic to the internet
2. **DNS Resolution**: Verifies DNS queries work through the FIPS tunnel
3. **Connectivity**: Tests end-to-end connectivity from mesh peer to internet via the exit node
4. **Payment Gate Logic**: Simulates tollgate-style payment gating using nftables rules (see `docker-relay-gate-poc.sh`)
5. **Dashboard Monitoring**: Playwright tests for the FIPS exit node dashboard

## Architecture

```
[FIPS Mesh Peer] ──UDP :2121──→ [VPS: FIPS Daemon] ──fips0──→ [WireGuard wg0]
                                                          │
                                                     [nftables MASQUERADE]
                                                          │
                                                     [PUBLIC INTERNET]
```

## Test Files

### Playwright Tests
- **`fips-dashboard.spec.mjs`**: Tests the FIPS exit node dashboard loads and displays status

### Shell Tests
- **`docker-relay-gate-poc.sh`**: Docker-based proof of concept for payment gating using nftables
  - Simulates customer → relay → exit topology
  - Tests UNPAID state (traffic blocked)
  - Tests PAID state (traffic allowed)
  - Tests PAYMENT EXPIRED state (traffic blocked again)

### Docker Infrastructure
- **`docker/Dockerfile.fips-node`**: Raw FIPS daemon container definition
- **`docker/entrypoint.sh`**: Generates FIPS config from environment variables
- **`docker-compose.yml`**: Defines test topology with fips-node and probe services
- **`docker/sim/`**: Simulation scripts for tollgate components

### Scripts
- **`scripts/run-node.sh`**: Run FIPS Docker node
- **`scripts/run-and-verify.sh`**: Run node and verify connection to VPS1
- **`scripts/get-vps1-identity.sh`**: Get VPS1 FIPS identity
- **`scripts/build-fips.sh`**: Build FIPS from source
- **`scripts/publish-nostr-announce.sh`**: Publish Nostr announcements

## Running the Tests

### Prerequisites

1. **Docker**: Required for running FIPS node containers
2. **Node.js 18+**: Required for Playwright tests
3. **FIPS binaries**: `fips`, `fipsctl`, `fipstop` binaries must be available

### Environment Setup

Copy the environment template and configure:

```bash
cp .env.template .env
# Edit .env with your FIPS credentials and VPS settings
```

Required environment variables (see `.env.template`):
- `FIPS_NSEC`: FIPS node secret key
- `FIPS_NPUB`: FIPS node public key
- `FIPS_PEER_NPUB`: VPS exit node public key
- `FIPS_PEER_ADDR`: VPS exit node address (default: `66.92.204.38:2121`)
- `FIPS_DASHBOARD_URL`: Dashboard URL for Playwright tests

### Run Playwright Dashboard Tests

```bash
# Install dependencies
npm install

# Run Playwright tests (headed mode)
npx playwright test fips-dashboard.spec.mjs --headed

# Run in headless mode
npx playwright test fips-dashboard.spec.mjs
```

### Run Docker Relay Gate POC

```bash
# This test spins up 3 Docker containers to simulate the payment gate topology
./docker-relay-gate-poc.sh
```

Expected output:
```
TEST 1: UNPAID — customer should FAIL
PASS: Customer blocked (unpaid). No internet.

TEST 2: PAID — customer should SUCCEED
PASS: Customer reached internet (HTTP 200) while PAID.

TEST 3: Payment EXPIRED — blocked again
PASS: Customer blocked (payment expired).

ALL 3 TESTS PASSED
```

### Run FIPS Node with Docker Compose

```bash
# Build and run FIPS node
docker compose build fips-node
docker compose run --rm fips-node

# Run with custom environment variables
FIPS_PEER_NPUB=npub1... FIPS_PEER_ADDR=66.92.204.38:2121 docker compose up
```

### Run Verification Script

```bash
# Run FIPS node and verify connection to VPS1
./scripts/run-and-verify.sh
```

## Integration with Physical Router Test Automation

These tests are designed to complement the existing TollGate physical router tests:

- **Standalone**: The FIPS tests can run independently of TollGate tests
- **No Router Required**: Unlike physical router tests, these use Docker containers
- **Complementary**: Tests mesh networking and exit node functionality, while TollGate tests focus on captive portal and payment flow

## Key Concepts

### FIPS Version Pinning
The tests use FIPS v0.4.0 (pinned), NOT tracking master. This is the last stable release before upstream refactored toward sans-io architecture.

See `fips-pin.txt` in the original repository for version details.

### Payment Gate Architecture
The relay node uses nftables to gate transit traffic based on payment state:
- **UNPAID**: Drop forwarding from customer
- **PAID**: Accept forwarding from customer
- **EXPIRED**: Drop forwarding again

This concept can be integrated into FIPS daemon's `handle_session_datagram()` in `forwarding.rs`.

### Dashboard Monitoring
The FIPS exit node publishes status to:
- Dashboard URL: `https://fips-exit.orangesync.tech`
- nsite gateway: `https://npub1laqt4pmrqsel4ak6z6nazptm99jj28m386zkmsgd9zadt7jq55jq9qfhhe.nsite.lol/`
- Nostr kind 30078 events on relays

## Troubleshooting

### Container Won't Start
Check that `/dev/net/tun` exists on the host:
```bash
ls -l /dev/net/tun
```

If missing, create it:
```bash
sudo mkdir -p /dev/net
sudo mknod /dev/net/tun c 10 200
sudo chmod 666 /dev/net/tun
```

### Docker Compose Network Conflicts
The tests use the `10.203.0.0/24` subnet. If this conflicts with your network, edit `docker-compose.yml`:
```yaml
networks:
  e2e:
    ipam:
      config:
        - subnet: 10.203.0.0/24  # Change this
```

### FIPS Connection Fails
Verify VPS1 is reachable:
```bash
nc -u -v -z 66.92.204.38 2121
```

Check FIPS daemon logs:
```bash
docker logs fips-test-node
```

### Playwright Tests Fail
Ensure Playwright browsers are installed:
```bash
npx playwright install chromium
```

## References

- **Original Repository**: [felixfelix-bot/fips-exit-e2e](https://github.com/felixfelix-bot/fips-exit-e2e)
- **FIPS Upstream**: [jmcorgan/fips](https://github.com/jmcorgan/fips)
- **FIPS Exit Node**: [OpenTollGate/fips-exit-node](https://github.com/OpenTollGate/fips-exit-node)
- **Status & Design**: See `/tmp/fips-exit-e2e/docs/STATUS-AND-DESIGN.md`

## Notes

- These tests do NOT require physical router hardware
- These tests do NOT modify the original fips-exit-e2e repository
- These tests are designed to run without external services (Docker-only)
- The FIPS binaries (`fips`, `fipsctl`, `fipstop`) must be built separately (see `scripts/build-fips.sh`)