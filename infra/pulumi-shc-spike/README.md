# pulumi-shc-spike

An **isolated, throwaway experiment** that provisions a single disposable SHC VPS
via the local [`shc-pulumi`](../../..) dynamic provider. This is a spike to
evaluate whether Pulumi is a cleaner way to model the SHC VM lifecycle than the
imperative code in `lib/cloud_lab/shc*.py`. It is **not** wired into
`cloud-lab.py` and is safe to delete without affecting any other flow.

> **Decision status**: see [`docs/pulumi-shc-spike.md`](../../docs/pulumi-shc-spike.md)
> for the full evaluation. TL;DR — Pulumi is a clean fit for the *VM lifecycle*
> step; the worker-bootstrap step still needs the existing SSH path or Pulumi
> Automation API.

## What this spike does

- Provisions **one** SHC VPS using `SHCVMResource` from `shc-pulumi`.
- Defaults to the **cheapest** tier (`dev-1c-4gb`, currently $0.00/day).
- Reads the API key from `SHC_API_KEY` (no Pulumi config secrets, no passphrase
  dance).
- Auto-discovers an SSH public key (`~/.ssh/id_ed25519.pub`, then `id_rsa.pub`).
- Uses a **local file backend** (`.state/`) so nothing leaks to the Pulumi
  service or `~/.pulumi`.
- `pulumi destroy` cancels the VM immediately (`auto_cancel=True` default).

## What this spike does NOT do

- **No custom cloud-init user-data.** Empirically verified on both Dev VPS and
  NVMe (2026-07-02): SHC uses cloud-init (NoCloud seed CD-ROM) for its own
  provisioning, but the order API does **not** merge any user-supplied
  `user_data` into the seed. On NVMe/SSD/HDD cloud-init runs (so the order-time
  `ssh_key` is installed automatically); on Dev VPS cloud-init is **disabled by
  a marker file** (so `ssh_key` reaches the seed but is never consumed — the
  provider's `apply_ssh_key_live` workaround is needed). The "write a
  timestamped marker file on boot" goal from the spike brief therefore cannot
  be expressed through any SHC API on any tier. Full evidence in
  [`../shc-toolkit/docs/cloud-init.md`](../../../shc-toolkit/docs/cloud-init.md)
  and [`docs/pulumi-shc-spike.md`](../../docs/pulumi-shc-spike.md).
- **No tags / labels / metadata on the VM.** SHC exposes none.
- **No worker bootstrap.** It does not clone the test repo, run pytest, or
  publish results. It only proves VM create/read/destroy.
- **No lease/kill-switch.** `auto_cancel=True` schedules a *non-immediate*
  SHC-billing cancellation on create; `pulumi destroy` does the *immediate*
  cancel. There is no VM-internal `at`/polling lease like the imperative path
  has — the orchestrator must survive to call destroy.

## Prerequisites

1. **Pulumi CLI** ≥ 3.0 (`brew install pulumi`).
2. **Python 3.11+** (the `pulumi install` step creates an isolated `.venv`).
3. **Sibling checkouts** at the same level as this repo:
   - `../shc-pulumi` — the dynamic provider (referenced as editable install).
   - `../shc-toolkit` — the SHC API client (pulled in transitively).
4. **`SHC_API_KEY`** exported in your shell (`shc_live_...`). Generate one at
   <https://blesta.sovereignhybridcompute.com/user-api/docs/>.
5. **(Optional)** an SSH public key at `~/.ssh/id_ed25519.pub` or
   `~/.ssh/id_rsa.pub`, or set `PULUMI_SPIKE_SSH_PUBKEY_PATH` to a `.pub` file.
   If none is found, the VM is created with no injected key.

## How credentials are discovered

| Source | Used for | Required |
|---|---|---|
| `SHC_API_KEY` env var | SHC API auth (Bearer token) | **Yes** |
| `~/.ssh/id_ed25519.pub` (or `id_rsa.pub`) | SSH key injected at order time | No |
| `PULUMI_SPIKE_SSH_PUBKEY_PATH` env var | Override path to a `.pub` file | No |

The API key is **never** written to a Pulumi stack config file. It is read from
the environment inside `__main__.py` and passed to the provider, which masks it
as a Pulumi secret (`api_key : [secret]` in diffs).

## Files

```
infra/pulumi-shc-spike/
├── Pulumi.yaml          # project + stack config schema
├── __main__.py          # the Pulumi program (one SHCVMResource)
├── requirements.txt     # local editable installs of shc-{toolkit,pulumi}
├── _common.sh           # shared wrapper setup (local backend, stack select)
├── run-preview.sh       # dry-run (calls SHC API for size/credit, no VM)
├── run-up.sh            # create/update the VM
├── run-destroy.sh       # cancel the VM
├── .gitignore           # ignores .venv/, .state/, Pulumi.*.yaml, logs
└── README.md            # this file
```

## Pointing Pulumi at the local `../shc-pulumi` provider

`shc-pulumi` is a **Pulumi dynamic provider**, not a binary plugin. There is no
`pulumi plugin install` step. The provider runs as Python code inside the
Pulumi Python runtime, so pointing Pulumi at the local checkout is just an
editable pip install:

`requirements.txt` contains:

```
-e ../../../shc-toolkit
-e ../../../shc-pulumi
pulumi>=3.0
```

`pulumi install` (run by the wrapper scripts on first invocation, or manually)
creates `.venv/` and installs all three. After that, `import shc_pulumi` in
`__main__.py` resolves to `../shc-pulumi/src/shc_pulumi/`.

## Usage

All commands run from this directory.

```bash
# 0. Export your API key (do this once per shell)
export SHC_API_KEY="shc_live_..."

# 1. Dry-run — resolves the plan, hits SHC for size/credit check, creates NOTHING
./run-preview.sh

# 2. Create the VM (defaults: dev-1c-4gb, ~2.5 min to provision)
./run-up.sh

# 3. Inspect outputs
pulumi stack output
#   service_id  : 814
#   ip          : "66.92.204.238"
#   os_user     : "debian"
#   ssh_command : "ssh debian@66.92.204.238"

# 4. Cancel the VM
./run-destroy.sh
```

### Overriding defaults

```bash
# Use a bigger tier (matches the imperative path's "2C/8GB Dev VPS Standard")
pulumi config set size dev-2c-8gb

# Custom hostname
pulumi config set hostname my-spike-vm

# Create stopped (no hourly cost while you inspect)
pulumi config set power_state stopped
```

### Changing the stack name

The default stack is `spike`. Override per-invocation:

```bash
SPIKE_STACK=experiment-1 ./run-up.sh
SPIKE_STACK=experiment-1 ./run-destroy.sh
```

Each stack gets its own VM (hostname is derived from the stack name).

## Cleanup (remove ALL spike local state)

```bash
./run-destroy.sh                 # cancel any live VM first
pulumi stack rm spike --yes      # remove the stack from the local backend
rm -rf .venv .state last-*.txt *.log   # remove local artifacts
```

## Known limitations

- **`pulumi preview` is not free.** Dynamic providers call the real SHC API
  during preview (to resolve `size` → `package_id`/`pricing_id` and to run the
  credit pre-check). It does not create a VM, but it does consume an API call.
- **`dev-1c-4gb` may report a charge mismatch.** The provider's cost audit logs
  `CHARGE MISMATCH: charged $0.0000, expected $0.2400` — at the time of the
  spike this tier was billing $0.00/day. Harmless.
- **Provisioning takes ~2.5 minutes.** The provider polls `provisioning_state`
  every 5 s for up to 600 s. `pulumi up` will appear to hang during this window.
- **Destroy requires the orchestrator to be alive.** Unlike the imperative
  path's VM-internal `at`-based lease, if the machine running `pulumi` dies
  before `pulumi destroy`, the VM stays alive until the non-immediate
  `auto_cancel` resolves at the SHC billing layer (could be hours). For
  fire-and-forget runs, keep the imperative path's lease or add a cron-based
  reaper.
- **No cloud-init.** SHC has no user-data field. Any post-create setup must be
  done via SSH (as the imperative path does) or via a Pulumi `Command` resource.
