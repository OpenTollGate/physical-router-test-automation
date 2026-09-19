# tollgate-clientd — Debian laptop lane

`tollgate-clientd` keeps a laptop alive behind a TollGate: it discovers the
gateway on `:2121`, registers with the captive portal, shows remaining
bytes/seconds, and auto-tops-up with ecash from a local wallet before the
allotment runs out.

- **Source of truth**: `scripts/tollgate-clientd.py` here is vendored from
  [tollgate-module-basic-go](https://github.com/OpenTollGate/tollgate-module-basic-go)
  (`scripts/tollgate-clientd.py`, commit `19b76ec`). Re-vendor on updates and
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
