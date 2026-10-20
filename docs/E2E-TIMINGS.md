# E2E quick-test timings — COVR-X1860 alpha rig (tollgate-AFA5)

Measured cycle timings for the one-command quick-test entry point
`scripts/bench/alpha-quicktest.sh`, the wired ALPHA seat rig
(D-Link COVR-X1860 A1, OpenWrt 25.12.5, feed artifact tollgate-wrt
v0.6.0-rc6, fakewallet mint on ai-legion-small).

## Entry point

```bash
# from the repo root, on ai-legion:
scripts/bench/alpha-quicktest.sh --release v0.6.0-rc6
```

One command runs the whole cycle under a rig lock:

1. **ACQUIRE** — verify host prep on ai-legion-small, ARP-pin the DUT
   primary against the switch-SVI collision, verify board identity,
   ensure the DUT's dedicated mgmt secondary (192.168.105.61).
2. **INSTALL ("flash")** — fetch + sha256-verify the feed release asset
   (`scripts/download-feed-release.sh`), transfer via small (`scp -O`),
   full reinstall on the DUT: `apk del tollgate-wrt` → route re-assert →
   `apk update` → `apk add --allow-untrusted` (both network bounces are
   tolerated with wait-for-SSH loops; deps verified: jq + ndsctl).
3. **SETUP** — default route + DNS (works around
   [tollgate-rs#29](https://github.com/OpenTollGate/tollgate-rs/issues/29)),
   wallet.db reset, accepted_mints → fakewallet mint, restart, 15 s settle.
4. **TEST (standard e2e suite)** — kind:10021 health gate on :2121;
   cdk-cli mint 11 sat + send 4 sat (fakewallet); independent netns
   client (fresh MAC/IP, gw = DUT); portal prime :2050; token POST →
   **kind:1022**; `ndsctl` Authenticated; `/usage` allotment; internet
   through the DUT. All assertions hard-fail the cycle otherwise.
5. **TEARDOWN** — netns rig removed, addressing restored, ARP pin
   re-applied.

Exit codes: `0` ok, `3` lock busy, `10/20/30/40/50` phase failures.
Per-phase timestamps land in `results/alpha-quicktest/run-*/phases.tsv`
plus a machine-readable `result.json` (results/ is gitignored — physical
lab data stays local per the AGENTS.md privacy rules).

## Timings

### Quick-test cycle (package flow, full reinstall) — 2026-10-16

| Phase (owner vocabulary) | Script phase      | Run 1 (20:15) | Run 2 (20:18) | Run 3 (20:20) | Notes |
|---|---|---:|---:|---:|---|
| — (acquire DUT)         | acquire           | 5.8 s | 6.0 s | 7.0 s | board identity + mgmt IP |
| flash (rc image)        | install           | 94.6 s | 95.6 s | 96.2 s | artifact fetch+verify (cached ~0 s), push, `apk del`, `apk update`, `apk add` incl. ~24 MiB dep re-fetch over the bench NAT, both network bounces |
| — (setup)               | setup             | 18.2 s | 19.0 s | 18.8 s | incl. fixed 15 s daemon-settle |
| test-run                | test              | 10.2 s | 9.4 s | 9.8 s | health gate + mint + pay + verify |
| — (teardown)            | teardown          | 0.8 s | 0.7 s | 0.8 s | |
| **TOTAL**               |                   | **129.7 s** | **130.8 s** | **132.7 s** | ~2.2 min/cycle |

### Compile (image build) — reference datapoint

This rig's quick-test cycle has **no compile step by design**: it
validates the published feed artifact exactly as shipped (sha256 verified
against the release `SHA256SUMS`). For comparison, a full OpenWrt
ImageBuilder build for this board (`dlink_covr-x1860-a1`, 25.12.5,
`PACKAGES="nodogsplash jq"`):

| Step | Time | Notes |
|---|---:|---|
| ImageBuilder download + first `make image` (cold) | **45.5 s** | on ai-legion, includes tarball + apk dep downloads; warm rebuilds are faster |
| Factory/recovery/sysupgrade images produced | 3 × ~14.2 MiB | `/root/ib/openwrt-imagebuilder-*/bin/targets/ramips/mt7621/` |

A sysupgrade-flash cycle is **not wired for this rig**: the DUT has no
serial/console access on this seat and its bench network layout
(br-private addressing) is operator-managed, so a mis-flashed image
cannot be recovered remotely. Wiring it later needs (a) console access
(a labgrid exporter / serial lane on ai-legion-small) and (b) the bench
network layout baked into the image (cf. `scripts/bench/build-image.sh`,
which does exactly this for the MT3000 lanes).

### Steady-state smoke (no reinstall)

`--no-reinstall` skips the purge and re-installs over the running
install. Measured 2026-10-16 (run-20261016-202800, PASS):

| Phase | Time |
|---|---:|
| acquire | 7.5 s |
| install (verify + no-op add) | 25.6 s |
| setup | 19.5 s |
| test | 10.4 s |
| teardown | 1.2 s |
| **TOTAL** | **64.2 s** |

So the A/B for the flash step: full flash-equivalent cycle ≈ 130 s,
steady-state re-test ≈ 64 s — the purge+dep-refetch+bounces cost ≈ 65 s
per cycle.

## Reproduce

```bash
cd /home/ubuntu/physical-router-test-automation   # on ai-legion
scripts/bench/alpha-quicktest.sh --release v0.6.0-rc6            # full cycle
scripts/bench/alpha-quicktest.sh --release v0.6.0-rc6 --no-reinstall  # steady-state
cat results/alpha-quicktest/run-*/result.json                    # timings
```

ImageBuilder datapoint:

```bash
cd /root/ib
curl -fsSLO https://downloads.openwrt.org/releases/25.12.5/targets/ramips/mt7621/openwrt-imagebuilder-25.12.5-ramips-mt7621.Linux-x86_64.tar.zst
tar --zstd -xf openwrt-imagebuilder-*.tar.zst && cd openwrt-imagebuilder-*/
time make image PROFILE=dlink_covr-x1860-a1 PACKAGES="nodogsplash jq"
```

## One-time host prep (ai-legion-small)

Stable bench-NIC addressing so NetworkManager never strands the rig
(the default DHCP profile has no server on VLAN 1005 and flushes manual
addresses on renewal — this stranded the rig twice before being fixed):

```bash
sudo nmcli con mod bench-trunk ipv4.method manual \
     ipv4.addresses "192.168.105.10/24,192.168.105.2/24"
sudo nmcli con up bench-trunk
# NAT + forwarding for the rig upstream (already in place on the bench):
sudo iptables -t nat -A POSTROUTING -s 192.168.105.0/24 -j MASQUERADE
sudo sysctl -w net.ipv4.ip_forward=1
```

## Open dependencies

- **Bench owner (blocking other lanes, not this script):** the OpenWrt
  bench switch (GS1900-8HP #1) still announces a VLAN 1005 SVI at the
  DUT's primary address (192.168.105.1) — an IP collision. This script
  works around it with an ARP pin on small plus the DUT's dedicated
  mgmt secondary (192.168.105.61); anything else on that VLAN is still
  exposed to the flap. Removing the SVI retires both workarounds.
- [tollgate-rs#29](https://github.com/OpenTollGate/tollgate-rs/issues/29)
  — installer wipes the static default route (SETUP re-asserts it).
- [tollgate-rs#30](https://github.com/OpenTollGate/tollgate-rs/issues/30)
  — post-install hook noise on minimal images (cosmetic).
- [tollgate-rs#31](https://github.com/OpenTollGate/tollgate-rs/issues/31)
  — management-side traffic is not toll-gated (by design? to confirm).

## Known measurement caveats

- The paying client is a network namespace on ai-legion-small (fresh
  MAC/IP per cycle). Its return path is asymmetric (replies deliver via
  the bridge, not through the DUT), so `/usage` download bytes stay 0 on
  this bench — allotment accounting is asserted, byte counting is not
  exercised honestly here. A dedicated client host would fix this.
- Bench NAT uplink (via ai-legion-small) is shared; install-phase
  dependency fetch times (≈25 s of the install phase) vary with its load.
- Fakewallet money is free; each cycle mints 11 fresh sats.
