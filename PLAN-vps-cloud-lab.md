# PLAN: VPS Cloud Lab Provider

Replace Google Cloud Platform infrastructure with a generic SSH-based VPS provider for the cloud lab, while preserving full GCP backward compatibility.

## Architecture

- **Persistent VPS** (shared with tollgate-infrastructure-kit services)
- **Generic SSH-based** (no provider-specific API)
- **Provider abstraction**: `CloudProvider` base class with `GCPProvider` and `VPSProvider` implementations
- **Config delivery**: JSON file via SCP (VPS) vs instance metadata API (GCP)
- **Self-cleanup**: Stop QEMU + remove config (VPS) vs delete entire VM (GCP)
- **Concurrency**: Single run at a time via lock file on the VPS

## Key Design Decisions

| Decision | GCP (existing) | VPS (new) |
|----------|---------------|-----------|
| VM lifecycle | Create/delete VMs via `gcloud` | Persistent VPS, QEMU process management |
| Config delivery | GCP instance metadata API | JSON file via SCP to `/tmp/tollgate-worker-config.json` |
| Self-deletion | `gcloud compute instances delete` | Stop QEMU, clean overlays, remove lock |
| Worker trigger | GCP startup script | SSH + `nohup` |
| Snapshot/image | `tollgate-runner-baked-v2` | Pre-baked by Ansible role on VPS |
| Firewall | GCP firewall rules | UFW (already configured by Ansible) |
| Concurrent runs | 1 per VM (unlimited VMs) | 1 (single VPS, lock file) |

## Files to Create

- [x] `lib/cloud_lab/provider.py` — Abstract `CloudProvider` base class
- [x] `lib/cloud_lab/vps.py` — `VPSProvider` implementation (SSH-based)
- [x] `tollgate-infrastructure-kit/ansible/roles/cloud_lab_runner/` — Ansible role to prepare VPS

## Files to Modify

- [x] `lib/cloud_lab/constants.py` — Add VPS-specific constants, keep GCP constants
- [x] `lib/cloud_lab/gcp.py` — Refactor into `GCPProvider(CloudProvider)`, keep module-level wrappers
- [x] `lib/cloud_lab/worker.py` — Add `load_config_from_file()`, make `delete_self()` provider-aware
- [x] `lib/cloud_lab/__init__.py` — Update docstring, export provider classes
- [x] `scripts/cloud-lab.py` — Add `--provider gcp|vps` flag, route through provider
- [x] `tollgate-infrastructure-kit/ansible/playbooks/setup-all.yml` — Add `cloud_lab_runner` role

## Implementation Checklist

### Phase 1: Provider Abstraction Layer

- [x] Create `lib/cloud_lab/provider.py` with `CloudProvider` ABC
- [x] Refactor `lib/cloud_lab/gcp.py` into `GCPProvider` class (backward-compatible wrappers)
- [x] Update `lib/cloud_lab/constants.py` with VPS constants

### Phase 2: VPS Provider

- [x] Create `lib/cloud_lab/vps.py` with `VPSProvider`
- [x] Add SSH-based VM lifecycle (up/down/status/ip)
- [x] Add `submit_run()` with JSON config + nohup worker
- [x] Add lock file mechanism for concurrency control
- [x] Add cleanup_stale/cleanup_all

### Phase 3: Worker Updates

- [x] Add `load_config_from_file()` to `worker.py`
- [x] Make `delete_self()` provider-aware (no-op for VPS)
- [x] Update `main()` to support `--from-file` flag
- [x] Keep `--from-metadata` for GCP unchanged

### Phase 4: CLI Integration

- [x] Add `--provider` flag to `scripts/cloud-lab.py`
- [x] Route all subcommands through selected provider
- [x] Update `lib/cloud_lab/__init__.py`

### Phase 5: Ansible Role

- [x] Create `cloud_lab_runner` Ansible role
- [x] Install QEMU/KVM, bridge-utils, python3-venv, sshpass
- [x] Download OpenWrt + Debian images
- [x] Optionally bake Debian Playwright overlay
- [x] Add role to `setup-all.yml`

### Phase 6: Testing & Cleanup

- [x] Run lint/typecheck on all modified files
- [x] Verify GCP path still works (no regressions)
- [x] Test VPS path end-to-end (pipeline runs, overlay applies, QEMU launches)
- [x] Identified: VPS requires KVM support — software emulation too slow for OpenWrt boot

## Bugs Found and Fixed During Testing

1. **GH_TOKEN export bug**: Was using command substitution `$(token)` instead of literal value. Fixed in `vps.py`.
2. **Log redirection**: nohup was redirecting to `/dev/null`. Fixed to redirect to `/var/log/tollgate-run.log`.
3. **Suite overlay**: VPS couldn't reach local commit SHAs. Added `_build_suite_overlay()` to always upload cloud_lab module files.
4. **KVM flag**: Worker hardcoded `-enable-kvm`. Made conditional on `/dev/kvm` existence.
5. **Boot timeout**: Software emulation needs 300s+ for OpenWrt serial boot. Increased from 90s to 300s.
6. **Syntax error**: `cmd.extend([` list wasn't properly closed with `])`.

## VPS Requirements for Production Use

The VPS **must** have KVM/nested virtualization support (`egrep -c '(vmx|svm)' /proc/cpuinfo` > 0). Without KVM, OpenWrt takes >10 minutes to boot in QEMU software emulation, making the test pipeline impractical.

Recommended VPS types:
- Hetzner VDS (dedicated vCPU with nested virt)
- Bare metal / dedicated servers
- Any VPS with explicit nested virtualization support

## Backward Compatibility Guarantees

1. `scripts/cloud-lab.py` defaults to `--provider gcp`
2. All existing `gcloud` commands work unchanged
3. Module-level functions in `gcp.py` preserved as thin wrappers
4. Worker `--from-metadata` path unchanged
5. No changes to test execution logic, QEMU setup, or gh-pages publishing
