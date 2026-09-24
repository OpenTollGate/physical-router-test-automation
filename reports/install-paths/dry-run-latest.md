# Dual-install-path e2e — flash-free dry run

- release: `v0.6.0-alpha4-pre16` (artifact version stem `0.6.0_alpha4_pre16`)
- bench host: `192.168.1.1`
- generated: 2026-09-24T22:52:24+00:00
- checks: 20 (18 pass, 1 fail, 1 skip)

| check | status | detail |
| --- | --- | --- |
| `release-manifest` | pass | 14 entries in v0.6.0-alpha4-pre16/SHA256SUMS |
| `artifact-selection` | pass | selected tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk (fmt=apk, arch=aarch64_cortex-a53) |
| `artifact-sha256-vs-manifest` | pass | tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk: 104e9ce00b8f01c0… (8361504 bytes) manifest=104e9ce00b8f01c0… |
| `expected-binary-sha256-from-artifact` | pass | /usr/bin/tollgate-wrt inside apk: dce8b1f1c89a0d04… |
| `artifact-ships-the-566-policy` | fail | the artifact payload does not contain 31-admin-board-not-guest-reachable.nft — the #566 admin-board guard cannot be present after install, so the POLICY assertions are unreachable with this release (the fix must actually ship in the package) |
| `sibling-ipk-binary-is-a-different-build` | pass | apk payload dce8b1f1c89a0d04… vs ipk payload 5ddda42bf55c3e00… — the identity gate must use the artifact of the format it installs |
| `package-format-split` | pass | apk magic b'ADBd' vs ipk magic b'\x1f\x8b\x08\x00' |
| `installer-script-fetched` | pass | 30462 bytes from https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh (bash -n exit 0), --tag supported: True |
| `installer-command-shape` | pass | bash <(curl -fsSL https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh) --tag v0.6.0-alpha4-pre16 192.168.1.1 '<router-secret>' felix@coinos.io |
| `fresh-flash-image-verified` | pass | openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin sha256 ok (OpenWrt 25.12.x / mediatek-filogic / gl-mt3000) |
| `happy-path-suite-collected` | pass | 6 tests listed for the reused happy-path suite (protocol/captive-portal.spec.mjs, project desktop-portal, runner repo-local (node_modules/.bin/playwright)) |
| `bench-lock-state` | pass | free (/home/c03rad0r/.hermes/state/bench-mt3000.lock) |
| `bench-port-22` | pass | open from this host (br-lan side) |
| `bench-port-2050` | pass | open from this host (br-lan side) |
| `bench-port-2051` | pass | open from this host (br-lan side) |
| `bench-port-2121` | pass | open from this host (br-lan side) |
| `bench-port-8080` | pass | open from this host (br-lan side) |
| `bench-port-8090` | pass | open from this host (br-lan side) |
| `bench-identity` | pass | OpenWrt 25.12.5 r33051-f5dae5ece4\|aarch64_cortex-a53\|mediatek/filogic \| installed: tollgate-wrt-0.6.0_alpha4_pre16-r1 |
| `bench-policy-snapshot` | skip | informational — current install does NOT match the expected policy: nodogsplash users_to_router is missing [23, 53, 67, 80, 443, 2050, 2051, 2121, 8080]; guard file /etc/nftables.d/31-admin-board-not-guest-reachable.nft is missing |

## collected facts

```json
{
  "artifact": {
    "filename": "tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk",
    "sha256": "104e9ce00b8f01c09840c9aa6d8976c6a6712a4cd05376ddf5f5a237eb2b4e72",
    "url": "https://github.com/FreedomTechFeed/packages/releases/download/v0.6.0-alpha4-pre16/tollgate-wrt_0.6.0_alpha4_pre16_aarch64_cortex-a53.apk"
  },
  "artifact_bytes": 8361504,
  "bench_installed": "tollgate-wrt-0.6.0_alpha4_pre16-r1",
  "bench_lock": {
    "free": true,
    "holder": "manager pid=2727422 purpose=policy-convergence-ab t_3ac1bb9d (rerun) since=2026-09-25T00:46:34+02:00",
    "path": "/home/c03rad0r/.hermes/state/bench-mt3000.lock"
  },
  "bench_policy_snapshot": {
    "guard_present": false,
    "users_to_router_ports": [
      22
    ],
    "violations": [
      "nodogsplash users_to_router is missing [23, 53, 67, 80, 443, 2050, 2051, 2121, 8080]",
      "guard file /etc/nftables.d/31-admin-board-not-guest-reachable.nft is missing"
    ]
  },
  "bench_release": "OpenWrt 25.12.5 r33051-f5dae5ece4|aarch64_cortex-a53|mediatek/filogic",
  "expected_binary_sha256": "dce8b1f1c89a0d04d705aa4ed15071aaf66a658dda791dee0996f99c76fd56bb",
  "fresh_flash_image": {
    "path": "/home/c03rad0r/worktrees/mt3000-flash/openwrt-25.12.5-mediatek-filogic-glinet_gl-mt3000-squashfs-sysupgrade.bin",
    "sha256": "1ffa6526ea099878e0fc520dc0473e95202c1a470b3eabcd2cd40e9c8eaab8c6"
  },
  "happy_path_suite": {
    "expected_titles": [
      "API returns valid advertisement with pricing",
      "portal shows cashu token input",
      "portal shows lightning amount input",
      "portal shows mint selection pricing buttons"
    ],
    "grep": "captive portal \u2014 happy path",
    "listed": 6,
    "project": "desktop-portal",
    "runner": "repo-local (node_modules/.bin/playwright)",
    "spec": "protocol/captive-portal.spec.mjs"
  },
  "host": "192.168.1.1",
  "installer": {
    "sha256": "531c0754e9e133496bc209ffd80a2e4c8bc262437e93b05eea41eaca1ae14ed7",
    "supports_tag": true,
    "url": "https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh"
  },
  "ipk_binary_sha256": "5ddda42bf55c3e007ce01016d9aa076661307e604c8df4e3b4d6e11c86c21956",
  "manifest_entries": 14,
  "payload_policy_readiness": {
    "problems": [
      "the artifact payload does not contain 31-admin-board-not-guest-reachable.nft \u2014 the #566 admin-board guard cannot be present after install, so the POLICY assertions are unreachable with this release (the fix must actually ship in the package)"
    ],
    "setup_removes_admin_ports": {
      "8090": true,
      "8443": true
    },
    "ships_guard_nft": false
  },
  "tag": "v0.6.0-alpha4-pre16",
  "version": "0.6.0_alpha4_pre16"
}
```
