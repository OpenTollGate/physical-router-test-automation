# Test Report Publishing Pipeline

Test results are published through Blossom + Nostr. The dashboard at
[tests.tollgate.me](https://tests.tollgate.me) is a client-side SPA that
reads kind 30078 events from Nostr relays and fetches artifacts from Blossom
servers — no static HTML is pushed anywhere.

## Architecture

```
pytest suites → collect_and_render → publish_to_nostr (Blossom + kind 30078)
                                     → verify_nostr_publish (relay query + blob fetch)
```

### Blossom + Nostr

`publish_to_nostr` in `report.py` calls `lib.result_publisher` which:

1. Scans all result files for secrets (blocks files containing private keys, tokens, etc.)
2. Uploads clean files to Blossom (`blossom.psbt.me`)
3. Emits a Nostr kind 30078 parameterized replaceable event with all file URLs + JSON summary

The kind 30078 event is published to `relay.cashu.email`.
The event contains:
- `d` tag: run ID (makes it replaceable)
- `t` tag: `test-run`
- `file` tags: Blossom URLs for each published file
- Content: JSON summary with run_id, timestamp, file list, scan results, metadata

After publishing, `verify_nostr_publish` queries the relay with `nak fetch` for
the kind 30078 event and fetches one Blossom URL to confirm the blob is live.
This runs inside the cloud lab VM as part of the pipeline.

### Dashboard SPA

The dashboard at `tests.tollgate.me` is a static SPA served from the `docs/`
directory on the `main` branch via GitHub Pages. It:
- Connects to Nostr relay (`wss://relay.cashu.email`)
- Fetches kind 30078 + kind 1063 events from the bot's npub
- Renders a sidebar of test runs with pass/fail/skip stats
- Shows screenshots and file links fetched directly from Blossom URLs

Files: `docs/index.html`, `docs/app.js`, `docs/style.css`.

### Requirements for Nostr publishing

| Requirement | How it's met |
|-------------|--------------|
| nak CLI | Installed during pipeline outer-deps step (v0.16.2 from fiatjaf/nak) |
| NSEC file | Checked at `NSEC_FILE` env → `~/nsec` → `/root/nsec` → `/home/macbook/nsec` |
| Blossom server | Defaults to `https://blossom.psbt.me` (override via `BLOSSOM_SERVER` env) |
| Nostr relay | Defaults to `wss://relay.cashu.email` (override via `NOSTR_RELAYS` env) |

If nak or NSEC is missing, Nostr publishing is silently skipped (non-fatal).

### Provisioning NSEC on cloud VMs

The NSEC is not baked into the VM snapshot (security: secrets should not be in images). To enable Nostr publishing:

**During GitHub Actions runs:** Set `BOT_NSEC_HEX` as a repository secret on physical-router-test-automation. The pipeline reads it from the environment and writes it to `~/nsec`.

**During manual cloud-lab runs:** Copy the NSEC file after VM creation:
```bash
cloud-lab.py submit --branch main --smoke --publish --lease 30
# Wait for VM to boot (30s)
gcloud compute scp ~/.config/prta/nsec <vm-name>:~/nsec --zone=us-central1-a --project=tollgate-test-lab
```

## Artifact collection

The pipeline collects the following artifacts into `results_dir/raw/`:

| Artifact | When | Source |
|----------|------|--------|
| worker.log | Always | Pipeline log (`/var/log/tollgate-run.log`) |
| openwrt-syslog.log | Cloud runs only (`TOLLGATE_VIRTUAL_LAB=1`) | SSH to OpenWrt VM, `logread` |
| tollgate-service.log | Cloud runs only | SSH to OpenWrt VM, `/tmp/tollgate-debug.log` |
| cdk-v2.log | Always | Local CDK V2 mint log |
| nutshell-v2.log | Always | Local Nutshell V2 mint log |
| nutshell-v1.log | Always | Local Nutshell V1 mint log |
| visual/output.log | Always | Visual runner pytest output |
| smoke-api/output.log | Always | Smoke-API runner pytest output |
| *.png | Per test | Screenshots captured during tests |
| *.webm | Per test | Video recordings of visual tests |

Syslog and service log collection is gated on `TOLLGATE_VIRTUAL_LAB` because physical router runs don't have SSH-accessible syslog from the test host.

## Test modes

| Mode | Flag | Duration | Coverage |
|------|------|----------|----------|
| Quick | `--quick` | ~5 min | Visual happy path only |
| Smoke | `--smoke` | ~8 min | Visual + API smoke (health, wallet, version, payment) |
| Complete | `--complete` | ~45 min | Adds degraded mode, recovery, multi-mint, Lightning |

Runners in complete mode:
1. `visual` (sequential gate — must pass before others start)
2. `api` (parallel — all API tests)
3. `vl-scenarios` (sequential, destructive — two-router scenarios)

## Related

- [nested-kvm-router-testing.md](../hackathon-tooling/patterns/testing/nested-kvm-router-testing.md) — cloud lab architecture pattern
- [tollgate-module-basic-go reference](../hackathon-tooling/references/tollgate-module-basic-go.md) — project overview
