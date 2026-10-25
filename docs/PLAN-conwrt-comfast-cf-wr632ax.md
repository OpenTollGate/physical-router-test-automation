# conwrt PR: COMFAST CF-WR632AX tested-hardware enrollment

Status: **PR opened** (2026-10-25): https://github.com/Amperstrand/conwrt/pull/567

Validation: `make validate-models` OK; 86 targeted model tests + 3 transition
annotation tests pass. GitHub CI: `changes`, `rust`, and `firmware-esp32s3`
pass; the Python `test (3.13)` job fails only in 7 Rust parity tests because the
runner's rustup cannot install `clippy-preview` (`bin/cargo-clippy` conflict);
5,151 Python tests pass. The other Python matrix jobs were cancelled by
fail-fast. This is a runner/toolchain issue, not a model-validation failure.
The PR is open and mergeable; a maintainer rerun/fix is needed for a green
required check.

## Goal

Update `Amperstrand/conwrt` so the tested COMFAST CF-WR632AX is marked as known
hardware and its stock-vs-OpenWrt facts are correct. This is the conwrt
device-model enrollment; it is separate from the live x280 test-fleet inventory,
which already contains `comfast-b3ba`.

## Live evidence (no secrets in the PR)

```text
Live fleet ID     comfast-b3ba
WAN               192.168.50.219 (reservation)
OpenWrt           25.12.5 r33051-f5dae5ece4
Target/profile    mediatek/filogic, comfast_cf-wr632ax
Stock firmware    CF-WR632AX-V2.6.1.2, WebUI at 192.168.0.1
Flash path        stock WebUI -> official OpenWrt 25.12.5 sysupgrade ->
                  firstboot -y -> clean LAN 192.168.1.1
Management        2.5G WAN DHCP, x280-only firewall
1GbE LAN          eth1 did not link on the tested unit
APs               TollGate-COMFAST / -5G (disabled; arm/quiesce verified)
SSH               key-only, 11 keys, password auth off
Canary            lease=PASS reach=PASS dns=PASS UP
```

Do **not** put the fleet IP, MAC, serial, root password, or inventory into the
public conwrt model. `CONTRIBUTING.md` explicitly forbids specimen-specific MACs,
serials, captures, keys, inventory, and `config.toml`.

## conwrt gap on master

`models/comfast-cf-wr632ax.json` exists, but:

- `tested_hardware` is empty;
- `stock_default_ip` is `192.168.1.1`; the tested V2.6.1.2 UI was at
  `192.168.0.1`;
- `mac_oui` has only `40:A5:EF`; the tested unit advertises `8C:3D:16`;
- `openwrt.release` is not pinned to the tested 25.12.5;
- the stock-WebUI bootstrap path and tested-unit notes are missing.

## Planned model changes

Only `models/comfast-cf-wr632ax.json`:

- [ ] `openwrt.release: "25.12.5"`; add `openwrt.board_name` only if
      `ubus call system board` confirms it.
- [ ] `mac_oui`: add `8C:3D:16` (vendor OUI, not a full MAC).
- [ ] `stock_default_ip: "192.168.0.1"`; keep U-Boot recovery at
      `192.168.1.1`.
- [ ] Add a `tested_hardware` entry:
      `stock_webui_openwrt_25_12_5` with `tested: true`, date `2026-10-25`,
      and notes covering the stock UI, upload endpoint, `firstboot -y`, 2.5G
      WAN, eth1 down, and the unexercised U-Boot recovery.
- [ ] Add `signatures.stock_webui` with login and upload paths, default IP.
- [ ] Add notes distinguishing the stock UI IP from the U-Boot recovery IP and
      recording the dead 1GbE LAN port.
- [ ] Do **not** add a new `stock-webui` flash method in this PR. The remote
      master has transition annotations (`resources_required`/`envelope`) and
      an unannotated method fails CI. The existing recovery/tftp/sysupgrade
      methods stay; the stock-WebUI evidence goes under `tested_hardware` and
      `signatures`. A first-class method can be a follow-up once its transition
      enums are confirmed.

## Branch and PR mechanics

- Use a clean worktree from `upstream/master`, not the local
  `feat/image-source-and-make-flash` branch.
- Do not commit the local untracked `config.toml`, keys, fingerprints, or
  scripts.
- Branch: `feat/comfast-cf-wr632ax-tested`.
- Validate: `python3 scripts/validate_models.py` / `make validate-models`, then
  `make ci` or the targeted model tests.
- Push to fork `c03rad0r/conwrt`.
- Open a PR against `Amperstrand/conwrt:master`.
- PR body: tested evidence, validation, no MAC/serial/IP/password.

## Validation checklist

- [ ] JSON schema and id/filename match.
- [ ] `validate_models` passes; no false claim that U-Boot recovery was tested.
- [ ] `git diff --check`; no secrets/specimen MAC/serial/IP.
- [ ] PR opens and CI starts.
