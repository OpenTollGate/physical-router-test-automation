# Multi-Provider VM Architecture

## Overview

physical-router-test-automation supports running test VMs on **GCP** (default) or **SHC** (Sovereign Hybrid Compute). A provider abstraction layer handles VM lifecycle while the test pipeline remains identical.

## Quick Start

### GCP (default — unchanged)
```bash
python3 scripts/cloud-lab.py up --vm-name my-test
python3 scripts/cloud-lab.py submit --pr 42
python3 scripts/cloud-lab.py down --vm-name my-test
```

### SHC
```bash
export SHC_API_KEY="shc_live_..."
python3 scripts/cloud-lab.py up --cloud shc --vm-name my-test
# → Creates SHC Dev VPS Standard (2C/8GB/16GB), provisions in ~64s
# → Applies your ~/.ssh/id_rsa.pub to the VM
# → Prints: ssh debian@<ip>

# SSH in and run tests manually:
ssh debian@<ip>
cd /opt/tollgate-test && pytest tests/api -m api

python3 scripts/cloud-lab.py down --cloud shc --vm-name my-test
```

## Provider Architecture

```
TOLLGATE_VM_PROVIDER=gcloud (default)     TOLLGATE_VM_PROVIDER=shc
         │                                        │
         ▼                                        ▼
   GCPProvider                              SHCProvider
  (wraps gcp.py)                         (wraps shc-toolkit)
         │                                        │
         ▼                                        ▼
   gcloud CLI                             SHC API v2 + SSH
         │                                        │
         └─────────────┬────────────────────────┘
                       ▼
              VMInfo dataclass
              {name, service_id, ip, ...}
                       │
                       ▼
              worker/ pipeline
         (provider-agnostic — QEMU,
          test execution, result publishing)
```

## Supported Commands per Provider

| Command | GCP | SHC |
|---------|-----|-----|
| `up` | ✅ Creates from snapshot | ✅ Orders Dev VPS, provisions, applies SSH key |
| `down` | ✅ Deletes VM | ✅ Cancels VM (immediate, with refund) |
| `status` | ✅ Shows VM status | ✅ Lists all SHC VMs with IPs |
| `ssh` | ✅ Connects via gcloud compute ssh | ✅ Connects via standard SSH |
| `cleanup-stale` | ✅ Deletes old VMs | ✅ Cancels old SHC VMs |
| `cleanup-all` | ✅ Deletes all | ✅ Cancels all |
| `submit` | ✅ Full CI (JIT runner) | ❌ Not yet (use up + manual SSH) |
| `status-run` | ✅ Via run-id | ❌ N/A (no run-id system on SHC) |

## Files

```
lib/cloud_lab/
  provider.py     ← VMProvider interface + GCPProvider + SHCProvider
  shc.py          ← SHC API v2 client (confirmation flow, idempotency, ssh_key fix)
  gcp.py          ← GCP VM lifecycle (unchanged, wrapped by GCPProvider)
  worker/         ← Test pipeline (provider-agnostic, runs inside VM)
  constants.py    ← Shared constants
```

## SHC-Specific Notes

- **Machine types:** SHC Dev VPS Standard (pkg 81) ≈ GCP n2-standard-2 (2C/8GB)
- **Cost:** ~$0.49/day, prorata refund on cancel (60-second test costs $0.00)
- **Provisioning:** 22-64 seconds (vs GCP snapshot boot ~10s)
- **SSH user:** `debian` (not `root` — use `sudo -i` for root)
- **Nested KVM:** ✅ Available on all Dev VPS plans

## Adding a New Provider

1. Implement `VMProvider` in a new file (e.g., `lib/cloud_lab/aws.py`)
2. Register in `provider.py`:
   ```python
   _PROVIDERS["aws"] = AWSProvider
   ```
3. Add to `--cloud` choices in `scripts/cloud-lab.py`
4. Test: `python3 scripts/cloud-lab.py up --cloud aws --vm-name test`
