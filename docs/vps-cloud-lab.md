# VPS Cloud Lab

Run TollGate API tests on a persistent VPS using QEMU/KVM nested virtualization, as an alternative to Google Cloud Platform.

## Related Repos

- **Infrastructure Kit** (Ansible deployment): [ngit](nostr://npub12m5exm2uk3xa674cc5r0hlyvccs5xxn7qv83ezuteefv5972nquq4j4szl/ngit.orangesync.tech/tollgate-infrastructure-kit) | [GitHub](https://github.com/OpenTollGate/tollgate-infrastructure-kit)

## Overview

The VPS cloud lab runs the same 9-step test pipeline as GCP, but on a persistent VPS that you control. The VPS boots QEMU VMs (OpenWrt + Debian) for each test run, then cleans up when done.

| | GCP | VPS |
|---|---|---|
| Cost per run | ~$0.50 | $0 (included in VPS) |
| VM isolation | Full GCE VM | QEMU processes |
| Concurrent runs | Unlimited | 1 at a time |
| Setup | Automatic (snapshot) | One-time Ansible role |

## Prerequisites

- A VPS with KVM support (VDS, bare metal, or nested-virt VPS)
- The VPS must already be running the [tollgate-infrastructure-kit](nostr://npub12m5exm2uk3xa674cc5r0hlyvccs5xxn7qv83ezuteefv5972nquq4j4szl/ngit.orangesync.tech/tollgate-infrastructure-kit) services (or at least have the `cloud_lab_runner` Ansible role applied)
- SSH key access to the VPS from your local machine

## One-Time VPS Setup

### 1. Enable the cloud lab runner role

In your `tollgate-infrastructure-kit` `.env`:

```bash
echo "cloud_lab_runner_enabled=true" >> .env
```

### 2. Run the Ansible deployment

```bash
cd tollgate-infrastructure-kit
make deploy
```

This installs QEMU/KVM, downloads OpenWrt and Debian images, creates the virtual lab directory structure, and sets up the Python venv. The role is gated by `cloud_lab_runner_enabled` and does nothing if it's `false` (the default).

### 3. Verify

```bash
ssh root@YOUR_VPS_IP "ls ~/tollgate-virtual-lab/images/"
# Should show: openwrt-base.qcow2  debian-12-nocloud-amd64.qcow2
```

## Usage

### Configure environment

```bash
export TOLLGATE_VPS_HOST=your.vps.ip
export TOLLGATE_VPS_USER=root
export TOLLGATE_VPS_SSH_KEY=~/.ssh/id_ed25519
```

Or add these to your `.env` file.

### Submit a test run

```bash
# Using --provider vps explicitly
./scripts/cloud-lab.py --provider vps submit --pr 42 --publish

# Auto-detected if TOLLGATE_VPS_HOST is set
./scripts/cloud-lab.py submit --pr 42 --publish

# Block until the run finishes
./scripts/cloud-lab.py submit --pr 42 --publish --wait
```

### Check run status

```bash
./scripts/cloud-lab.py --provider vps status-run --run-id 20260526T143000Z-abc1234
```

### Stop a running test

```bash
./scripts/cloud-lab.py --provider vps down
```

### Clean up stale processes

```bash
./scripts/cloud-lab.py --provider vps cleanup-stale
./scripts/cloud-lab.py --provider vps cleanup-all
```

### SSH into the VPS for debugging

```bash
./scripts/cloud-lab.py --provider vps ssh
```

### View logs

```bash
ssh root@$TOLLGATE_VPS_HOST "tail -f /var/log/tollgate-run.log"
```

## GCP Compatibility

The GCP provider still works exactly as before:

```bash
./scripts/cloud-lab.py --provider gcp submit --pr 42 --publish
```

If `TOLLGATE_VPS_HOST` is not set, `--provider gcp` is the default.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TOLLGATE_VPS_HOST` | (empty) | VPS IP or hostname. Auto-selects VPS provider when set |
| `TOLLGATE_VPS_USER` | `root` | SSH user for VPS |
| `TOLLGATE_VPS_SSH_KEY` | `~/.ssh/id_ed25519` | SSH private key for VPS |
| `TOLLGATE_CLOUD_PROVIDER` | auto | Force `gcp` or `vps` (overrides auto-detection) |

## Concurrency

Only one test run can execute on the VPS at a time. A lock file (`/tmp/tollgate-run.lock`) prevents concurrent runs. If a run appears stuck:

```bash
ssh root@$TOLLGATE_VPS_HOST "rm /tmp/tollgate-run.lock"
```

## Troubleshooting

### "No KVM support"

Check if the VPS has hardware virtualization:
```bash
ssh root@$VPS_IP "egrep -c '(vmx|svm)' /proc/cpuinfo"
```
If `0`, your VPS doesn't support nested virtualization. QEMU will fall back to software emulation (very slow).

### "VPS already has an active run"

Either wait for the current run to finish, or force-stop it:
```bash
./scripts/cloud-lab.py --provider vps down
```

### "qemu-system-x86_64 not found"

The Ansible role hasn't been applied. Re-run `make deploy` with `cloud_lab_runner_enabled=true`.
