# PLAN: Hetzner Cloud Lab Provider

Ephemeral, fire-and-forget cloud test runner using the Hetzner Cloud API (curl-based). Mirrors the GCP provider pattern but cheaper (~€0.02/run vs ~$0.50/run).

## Roadmap Context

| Priority | Provider | Status |
|----------|----------|--------|
| 1 | GCP | Production (existing) |
| 2 | VPS persistent | Implemented, blocked by KVM on current VPS |
| 3 | **Hetzner (this plan)** | **In progress** |
| 4 | Sovereign Hybrid Compute | Roadmap — will use VPS provider when KVM is enabled |

## Architecture

```
Local machine → HetznerProvider.submit_run()
  ├── curl POST /v1/servers (create from snapshot, ~30s)
  ├── poll GET /v1/servers/{id} until SSH ready (~60s)
  ├── SCP config JSON + suite overlay
  └── SSH nohup worker

Hetzner CX32 (ephemeral, KVM enabled)
  ├── Worker runs 9-step pipeline (same as GCP/VPS)
  └── delete_self(): curl DELETE /v1/servers/{id}
```

## Hetzner Cloud API Endpoints

| Operation | Method | Endpoint |
|-----------|--------|----------|
| Create server | POST | `/v1/servers` |
| Get server | GET | `/v1/servers/{id}` |
| List servers | GET | `/v1/servers?label_selector=...` |
| Delete server | DELETE | `/v1/servers/{id}` |
| List images/snapshots | GET | `/v1/images?type=snapshot` |
| List SSH keys | GET | `/v1/ssh_keys` |

All requests: `Authorization: Bearer $HETZNER_API_TOKEN`

## Files to Create

- [ ] `lib/cloud_lab/hetzner.py` — `HetznerProvider(CloudProvider)` using curl
- [ ] `lib/cloud_lab/hetzner_snapshot.py` — One-time snapshot baker script

## Files to Modify

- [ ] `lib/cloud_lab/constants.py` — Add Hetzner constants
- [ ] `lib/cloud_lab/worker.py` — Add `hetzner_api_token` to WorkerConfig, hetzner branch in `delete_self()`
- [ ] `scripts/cloud-lab.py` — Add `--provider hetzner` choice
- [ ] `docs/vps-cloud-lab.md` — Add Hetzner usage section

## Implementation Checklist

### Phase 1: Constants + Worker

- [ ] Add Hetzner constants to `constants.py`
- [ ] Add `hetzner_api_token` to `WorkerConfig`
- [ ] Add hetzner branch to `delete_self()`

### Phase 2: Hetzner Provider

- [ ] Create `_run_hcloud()` curl wrapper with retry
- [ ] Implement `vm_up()` — create server from snapshot
- [ ] Implement `vm_down()` — delete server
- [ ] Implement `vm_status()` — get server status
- [ ] Implement `vm_external_ip()` — get public IP
- [ ] Implement `submit_run()` — full fire-and-forget flow
- [ ] Implement `status_run()`, `cleanup_stale()`, `cleanup_all()`
- [ ] Implement `ssh_command()`

### Phase 3: Snapshot Baker

- [ ] Create snapshot baker script
- [ ] Bake snapshot from Debian 12 + cloud_lab_runner role

### Phase 4: CLI + Docs

- [ ] Add `--provider hetzner` to `scripts/cloud-lab.py`
- [ ] Update docs with Hetzner usage

### Phase 5: Testing

- [ ] Syntax check all files
- [ ] Verify GCP path unchanged
- [ ] Test Hetzner provider with real API (requires API token)

## Design Decisions

1. **curl not SDK**: Mirrors `gcp.py` pattern with `_run_gcloud()`. No new pip dependency.
2. **SSH trigger not cloud-init**: Reuses VPS provider's SCP+nohup pattern. Simpler, already debugged.
3. **Suite overlay**: Always upload cloud_lab module files (same as VPS provider).
4. **Self-deletion**: Worker calls `curl DELETE` in `delete_self()` (same pattern as GCP's `gcloud delete`).
5. **Snapshot**: Pre-baked with QEMU, images, venv. Startup script is safety net only.

## Cost

| Item | Cost |
|------|------|
| CX32 per hour | €0.021 |
| Snapshot storage (40GB) | ~€0.50/month |
| Per test run (20 min) | ~€0.007 |
| 50 runs/month | ~€1.35 total |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HETZNER_API_TOKEN` | (empty) | Hetzner Cloud API token |
| `HETZNER_SERVER_TYPE` | `cx32` | Server type for test runs |
| `HETZNER_SNAPSHOT_NAME` | `tollgate-runner-baked` | Snapshot to boot from |
| `HETZNER_SSH_KEY_ID` | (empty) | Hetzner SSH key resource ID |
| `HETZNER_SSH_KEY` | `~/.ssh/id_ed25519` | Local SSH key path |
| `HETZNER_LOCATION` | `fsn1` | Hetzner datacenter (fsn1=nuremberg) |

## Prerequisites (one-time setup)

1. Create Hetzner Cloud account
2. Generate API token at https://console.hetzner.cloud
3. Register SSH key in Hetzner Cloud console (note the key ID)
4. Run snapshot baker: `python3 -m lib.cloud_lab.hetzner_snapshot --bake`
