# tollgate-clientd — Debian laptop lane

`tollgate-clientd` keeps a laptop alive behind a TollGate: it discovers the
gateway on `:2121`, registers with the captive portal, shows remaining
bytes/seconds, and auto-tops-up with ecash from a local wallet before the
allotment runs out.

- **Source of truth**: `scripts/tollgate-clientd.py` here is vendored from
  [tollgate-module-basic-go](https://github.com/OpenTollGate/tollgate-module-basic-go)
  (`scripts/tollgate-clientd.py`, commit `9e89ab5`). Re-vendor on updates and
  bump the provenance line in the docstring.
- **Wallets**: `cdk-cli` (modern `--v3 --amount` and legacy stdin CLIs) or
  nutshell (`cashu --host <mint> --yes send <amount>`), auto-detected.
- **Protocol**: `GET :2121/` advertisement → `POST :2121/?mac=` raw token →
  `GET :2121/usage` `"used/allotment"` (`-1/-1` = no session).

## Quick start (Debian lane)

```bash
pip install cdk-cli   # or: cargo install cdk-cli; or pip install cashu (nutshell)

# one-shot status (JSON for scripts)
scripts/tollgate-clientd.py --status --json

# daemon: buy 1 step at a time, renew when 30s remain
scripts/tollgate-clientd.py --steps 1 --renew-below 30s
```

Against the module repo's cloud lab (real backend, no hardware):

```bash
cd tollgate-module-basic-go/tests/cloud-lab
docker compose up -d mint upstream
docker compose restart upstream                      # clear in-memory sessions
docker compose run --rm client -sv test_clientd_autotopup.py
```

## Status bar (waybar)

`--waybar` emits module JSON; add to waybar config:

```json
"custom/tollgate": {
    "exec": "~/src/physical-router-test-automation/scripts/tollgate-clientd.py --waybar",
    "interval": 5,
    "return-type": "json",
    "tooltip": true
}
```

CSS classes: `good` (green), `warning` (within 2× the renewal threshold),
`critical` (no session). Preview render:
`evidence/2026-09-19-tollgate-clientd/waybar-preview.png`.

## The laptop lane (real router, real NoDogSplash)

`tests/laptop/test_laptop_clientd.py` runs clientd from the controller
host against a real TollGate router — the one class the docker cloud-lab
cannot test: the router itself resolves our IP to a MAC (ARP), port-80
traffic registers the MAC with NoDogSplash, and a real payment leaves the
MAC `Authenticated` in `ndsctl`. Verified on the local QEMU OpenWrt venue
(router at `10.99.99.1`, NDS 5.0.2) — the same lane runs unchanged
against a physical router by pointing the env vars at it.

Provisioning (local QEMU venue):

1. **Mint** the router's `accepted_mints` URL must answer from the router.
   The venue routers expect `http://10.99.99.2:8085`; run a FakeWallet
   cdk-mintd there (`config init --new-mint` with the canonical mnemonic,
   see `~/tollgate-virtual-lab/mint-8085/`) and open the host firewall:
   `sudo ufw allow from 10.99.99.0/24 to any port 8085 proto tcp`.
2. **Wallet** a funded cdk-cli wallet the host can call. Without a rust
   toolchain, a shim runs cdk-cli from the cloud-lab-client image:

   ```bash
   # ~/bin/cdk-cli — forwards to docker, mounting any -w wallet dir
   # (auto-detects the modern --v3 --amount CLI)
   exec docker run --rm --network host $(mount args…) \
       --entrypoint cdk-cli cloud-lab-client "$@"
   ```

   Fund it: `cdk-cli -w /tmp/laptop-wallet mint http://10.99.99.2:8085 100`.
3. **Run** (the Makefile target takes the hardware lock):

   ```bash
   make lock PHASE="laptop lane"
   PATH=~/bin:$PATH LAPTOP_GATEWAY=10.99.99.1 make test-laptop-clientd
   ```

Recording a demo with the router's live log (`cmd:ssh` log source —
works for physical routers too):

```bash
python3 scripts/record-demo.py \
    --clientd-cmd "python3 scripts/tollgate-clientd.py --gateway 10.99.99.1 \
        --wallet-dir /tmp/laptop-wallet --steps 1 --renew-below 25MB --interval 2" \
    --log-source "cmd:sshpass -p tollgate ssh root@10.99.99.1 logread -f" \
    --duration 60 --out evidence/$(date +%F)-laptop-lane --export-video
```

Session reset between runs: `ssh root@<router> 'ndsctl deauth <mac>;
/etc/init.d/tollgate-wrt restart'` (sessions are in-memory per MAC).

## Regression lane

`tests/unit/test_tollgate_clientd.py` runs the script's built-in
`--selftest` (in-process mock TollGate — no hardware, mint, or wallet) and
pins the CLI flags the lanes and waybar config depend on.

## Evidence

- `evidence/2026-09-19-tollgate-clientd/cloud-lab-demo.txt` — full demo
  against the real backend: discovery, status, waybar JSON, daemon paying
  1 sat for a minute, auto-renewal at the 45s threshold (`0:45 left →
  paid → 1:59 left`), persistence after daemon exit.
- `evidence/2026-09-19-tollgate-clientd/waybar-preview.png` — status-bar
  rendering of the three module states.
- `evidence/2026-09-20-laptop-lane-hardware/` — the laptop lane recorded
  against the real OpenWrt/NDS venue: 2 payments (initial + threshold
  renewal) with the router's own `logread -f` synchronized alongside —
  including the `nodogsplash: Authenticating <ip> <mac>` line proving the
  port-80 MAC registration. `events.json` + `player.html` committed; the
  `demo.webm` export is gitignored, regenerate with `--export-video`.
