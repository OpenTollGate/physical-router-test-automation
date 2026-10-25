# conwrt PR: COMFAST CF-WR632AX tested-hardware enrollment

Status: **PR green, awaiting maintainer merge** (2026-10-26):
https://github.com/Amperstrand/conwrt/pull/567

Head: `e950d95` on `feat/comfast-cf-wr632ax-tested`.
Validation: `make validate-models` OK; 89 targeted model/transition tests pass.
GitHub CI: `changes`, `rust`, `firmware-esp32s3`, and `test` (3.10, 3.11, 3.12,
3.13) all pass; merge state is CLEAN. Only the upstream maintainer merge
remains.

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
Management        physical 2.5G WAN (netdev wan) DHCP, x280-only firewall
1GbE LAN          stock UI showed eth1 down; OpenWrt link came up at 1Gbps,
                  but management used the 2.5G WAN
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

- [x] `openwrt.release: "25.12.5"`; `openwrt.board_name` confirmed by
      `ubus call system board` as `comfast,cf-wr632ax`.
- [x] `mac_oui`: add `8C:3D:16` (vendor OUI, not a full MAC).
- [x] `stock_default_ip: "192.168.0.1"`; U-Boot recovery stays
      `192.168.1.1`.
- [x] Add a `tested_hardware` entry:
      `stock_webui_openwrt_25_12_5` with `tested: true`, date `2026-10-25`,
      and notes covering the stock UI, upload endpoint, `firstboot -y`, 2.5G
      WAN, the LAN link, and the unexercised U-Boot recovery.
- [x] Add `signatures.stock_webui` with login and upload paths, default IP.
- [x] Add notes distinguishing the stock UI IP from the U-Boot recovery IP and
      recording the LAN/management facts.
- [x] Do **not** add a new `stock-webui` flash method in this PR. The remote
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

- [x] JSON schema and id/filename match.
- [x] `validate_models` passes; no false claim that U-Boot recovery was tested.
- [x] `git diff --check`; no secrets/specimen MAC/serial/IP.
- [x] PR opens and CI starts.
- [ ] PR merged by a conwrt maintainer (all checks green).
