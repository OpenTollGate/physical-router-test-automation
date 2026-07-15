# Publisher identities

Single source of truth for the Nostr identities that sign events and uploads
produced by this repo and its CI. Use this when debugging "events under the
wrong npub" or "Blossom 402 on upload".

## Canonical identities

| Hex pubkey | npub | Role | Key location | Status |
|---|---|---|---|---|
| `9a515b0f08d554b582e54202c7ca0e6ee56d81559957cbf9b40047d391b95fd5` | `npub1nfg4krcg642ttqh9ggpv0jswdmjkmq24n9tuh7d5qpra8ydetl2sq8qtef` | fips test-run publisher (`#t=fips-test`, `fips-ble`, etc.) + bcr-agent's signing identity | `~/.config/bcr-deploy/secrets` as `BOT_NSEC_HEX` (bcr-agent). Historical events on this npub were published by bcr-agent, not the SHC VM. | Active for bcr-agent. Whitelisted in Blossomflare. |
| `28602aa4b9a599e5fb6a1ee974dbb5447c6baab7f677803d140cdbae6fdc9010` | `npub19psz4f9e5kv7t7m2rm5hfka4g37xh24h7emcq0g5pnd6um7ujqgqtm2mu0` | tollgate test-run publisher (`#t=tollgate`, `test-run`) | GitHub Actions secret `BOT_NSEC_HEX` in this repo. | Active. Whitelisted in Blossomflare 2026-07-13. |
| `76c714199ad17278276d4cd51ddec7d0df0715a91b2f2f03f16c03925b3a0911` | `npub1wmr3gxv669e8sfmdfn23mhk86r0sw9dfrvhj7ql3dspeyke6pygs3ucedl` | tollgate-wrt CI release signing bot (`#n=tollgate-wrt`, kinds 1063 + 30078) | `~/.config/prta/nsec` on dev Mac. Rotated from `5075e61f…`. | Active. Whitelisted in Blossomflare. |
| `5075e61f0b048148b60105c1dd72bbeae1957336ae5824087e52efa374f8416a` | `npub12p67v8ctqjq53dspqhqa6u4matse2uek4evzgzr72th6xa8cg94qxks7ks` | Previous tollgate-wrt CI bot (pre-rotation) | Lost. | Superseded. See `lib/deploy.py:27` for the rotation note. |

## Code references

| File | Reference |
|---|---|
| `lib/deploy.py:27` | `NOSTR_PUBLISHER_PUBKEY = "5075e61f…"` (pre-rotation) |
| `lib/deploy.py:28` | `NOSTR_PUBLISHER_PUBKEY_NEW = "76c714199a…"` (current) |
| `lib/deploy.py:22-26` | Inline comment: resolver no longer filters by `-a` author; uses `#n=tollgate-wrt` tag. |
| `lib/cloud_lab/shc_submit.py:100-104` | `_nsec_hex()` reads `NSEC_FILE` env var or falls back to `~/.config/prta/nsec`. |
| `lib/cloud_lab/shc_submit.py:687` | `BOT_NSEC_HEX` injected into the VM bootstrap from `_nsec_hex()`. |
| `lib/cloud_lab/shc_submit.py:688-689` | `EXPECTED_NPUB` and `STRICT_NPUB_CHECK` injected (optional). |
| `lib/cloud_lab/shc_submit.py:274-285` | Bootstrap step 4: writes `/root/nsec`, derives + logs publisher npub. |
| `.github/workflows/cloud-lab-shc.yml:78-84` | CI writes `~/.config/prta/nsec` from `BOT_NSEC_HEX` GitHub secret before running. |
| `.github/workflows/cloud-lab-runner.yml:135-156` | Runner writes `/root/nsec` from `BOT_NSEC_HEX`. |

## Key sourcing chain

```
GitHub secret BOT_NSEC_HEX
    ↓ (workflow writes to ~/.config/prta/nsec on the runner)
shc_submit.py _nsec_hex()
    ↓ (passes as BOT_NSEC_HEX env var in bootstrap_env)
VM bootstrap step 4
    ↓ (echo -n "$BOT_NSEC_HEX" | sudo tee /root/nsec)
/root/nsec on the SHC VM
    ↓ (nak event --sec "$(cat /root/nsec)" or NOSTR_SECRET_KEY env var)
Nostr events signed by 28602aa4…
```

The GitHub Actions secret is the root. Whatever scalar is stored there is the
canonical tollgate test-run publisher identity.

## Blossomflare whitelist

Blossom (`blossom.psbt.me`) gates uploads by checking `pubkey_whitelist` in
the `blossomflare-meta` D1 database. The gating order is in
`blossomflare/src/routes/preflight.ts`:
`pubkey_whitelist` → `subscriptions` → free tier (<1MB) → 402.

The three active identities above are all whitelisted with:
`max_total_bytes = 5368709120` (5 GiB) or higher,
`max_file_bytes = 2147483648` (2 GiB),
`default_expiry_days = 36500`.

To add a new identity:

```bash
cd /Users/macbook/src/blossomflare
wrangler d1 execute blossomflare-meta --remote --command \
  "INSERT INTO pubkey_whitelist (pubkey, max_total_bytes, max_file_bytes, default_expiry_days) \
   VALUES ('<hex-pubkey>', 5368709120, 2147483648, 36500);"
```

## Bootstrap env vars

`shc_submit.py` reads these from the launcher's environment and exports them
into the VM bootstrap:

| Env var | Default | Effect |
|---|---|---|
| `EXPECTED_NPUB` | (empty) | If set, the bootstrap step 4 warns when the derived publisher npub differs. |
| `STRICT_NPUB_CHECK` | `0` | When `1` and `EXPECTED_NPUB` is set, step 4 hard-fails on npub mismatch. Useful for production CI; leave `0` for local experimentation. |

Example CI usage:

```yaml
env:
  EXPECTED_NPUB: 28602aa4b9a599e5fb6a1ee974dbb5447c6baab7f677803d140cdbae6fdc9010
  STRICT_NPUB_CHECK: 1
```

## Verifying the current identity

Check the GitHub secret's npub without reading its value (the secret is
write-only; instead, trigger a workflow run and inspect the bootstrap log
for the `Publisher npub:` line that step 4 now emits):

```
[4/15] Writing nsec... (14:32:01)...
  Publisher npub: 28602aa4b9a599e5fb6a1ee974dbb5447c6baab7f677803d140cdbae6fdc9010
[4/15] done (0s)
```

**Verified 2026-07-15**: EXPECTED_NPUB=28602aa4... is correct. The GitHub
secret `BOT_NSEC_HEX` produces this npub. End-to-end smoke test passed with
STRICT_NPUB_CHECK=1 — the assertion accepted the key and the bootstrap
completed. Events from 28602aa4... confirmed on relay.cashu.email and nos.lol.

Or check recent Nostr events from a key you control locally:

```bash
nak key public "$(cat ~/.config/prta/nsec)"
```

Or query recent events signed by a known npub:

```bash
nak req -a 28602aa4b9a599e5fb6a1ee974dbb5447c6baab7f677803d140cdbae6fdc9010 -l 5 \
  wss://relay.cashu.email wss://nos.lol wss://relay1.orangesync.tech
```

## Rotating the tollgate test-run identity

1. Generate a new key: `nak key generate > /tmp/new_nsec` (64-char hex).
2. Derive the pubkey: `nak key public "$(cat /tmp/new_nsec)"`.
3. Add the new pubkey to Blossomflare `pubkey_whitelist` (see above).
4. Update the `BOT_NSEC_HEX` GitHub secret with the new hex value.
5. Set `EXPECTED_NPUB` (and optionally `STRICT_NPUB_CHECK=1`) in the workflow
   to make future drift loud.
6. Leave the old pubkey in `pubkey_whitelist` so historical artifacts keep
   their upload permissions.
7. Update this doc.

## Why the identities are split

Before 2026-07, both fips and tollgate test runs were published under
`9a515b0f…` for dashboard simplicity. The `BOT_NSEC_HEX` GitHub secret in
this repo was later set to a different scalar, so new tollgate runs sign as
`28602aa4…`. The historical `9a515b0f…` key is bcr-agent's signing identity
(stored at `~/.config/bcr-deploy/secrets`) — bcr-agent is what originally
published the fips-test events, not the SHC VM.

The dashboard filters by `#t` tag rather than author, so historical and
current runs coexist regardless of which identity signed them. See
`fips-testing-plan.md` "Shared infrastructure" for the tag mapping.
