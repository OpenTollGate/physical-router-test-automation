# conwrt Tests

Tests for [conwrt](https://github.com/amperstrand/conwrt) router configuration
use cases against real OpenWrt systems (physical routers or QEMU VMs).

## Prerequisites

- OpenWrt router accessible via SSH (physical or QEMU VM)
- conwrt repo checked out locally
- `iperf3` installed on router and client (for bufferbloat tests)

## Configuration

Set environment variables (or add to `.env`):

```bash
CONWRT_ROUTER_HOST=192.168.1.1       # Router IP
CONWRT_ROUTER_KEY=~/.ssh/id_ed25519  # SSH key (optional)
CONWRT_ROUTER_PORT=22                # SSH port
CONWRT_CLIENT_HOST=192.168.1.100     # Client for iperf3 tests (optional)
CONWRT_REPO=~/src/conwrt             # Path to conwrt checkout
```

## Running Tests

```bash
# From the physical-router-test-automation repo root
source ~/.tollgate-test-venv/bin/activate

# Run all conwrt tests
pytest conwrt/ -v

# Run only SQM config tests (no client needed)
pytest conwrt/test_sqm_functional.py -v -k "not bufferbloat"

# Run full bufferbloat test (needs client host)
pytest conwrt/test_sqm_functional.py::test_sqm_reduces_bufferbloat -v
```

## Cloud Lab (SHC/GCP)

```bash
# Submit conwrt SQM test to SHC cloud
./scripts/cloud-lab.py submit --cloud shc \
  --suite conwrt \
  --branch main \
  --publish
```

## Test Inventory

| Test | What it verifies | Needs client? |
|------|-----------------|---------------|
| `test_router_running_openwrt` | Target is OpenWrt | No |
| `test_sqm_scripts_installed` | sqm-scripts package present | No |
| `test_conwrt_configure_applies_sqm` | conwrt configure creates correct UCI state | No |
| `test_sqm_service_running` | SQM service enabled and active | No |
| `test_tc_qdisc_has_cake` | tc qdisc shows CAKE/fq_codel | No |
| `test_sqm_reduces_bufferbloat` | Latency under load < 50ms | Yes |
