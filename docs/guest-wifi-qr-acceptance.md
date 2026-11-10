# Restaurant guest Wi-Fi QR acceptance

This is a prerequisite/observation milestone, **not a completed phone test**.
Portal implementation belongs to portal PR #102; integration documentation to
module PR #1204. No package pin changes are required by this milestone.

## Venue and authorization gate

Before touching a device, obtain the current registry and resolve its logical
router and client places. Module AGENTS names private `Amperstrand/conwrt-bench`
`registry/` (ADR-0005) as authority and retires `conwrt-lab`. PRTA's legacy local
inventory adapter is not permission to recreate that registry or guess addresses.
If authority cannot be read or the coordinator cannot be queried, report BLOCKED.
An HTTP 404 for a private repo does not distinguish absence from lack of access.

Read-only inventory commands (no acquire, power or deploy):

```sh
LG_COORDINATOR=ai-legion:20408 labgrid-client places
LG_COORDINATOR=ai-legion:20408 labgrid-client who
```

Resolve the registry/place mapping and authorization first, then use existing
PRTA labgrid venue and hardware-lock integration. Never steal a stale lock.
A missing local lock is not proof of availability. Device reservations cover
both router and client; other workers' AVDs and ad-hoc labs are off-limits.

## Evidence contract

Keep evidence local under gitignored `results/<run-id>/`. Before a run record:

- exact portal, module and PRTA source commits (re-read live PR heads);
- artifact paths and SHA256 **of actual bytes**, including the portal distribution,
  module package and QR image; origin/build linkage to source commits;
- deployed artifact identities, OS/image version, architecture, package manager;
- router/client logical identities, approved reservation, timestamp, vantage;
- commands with individual exit status and separate stdout/stderr, sanitized.

A source SHA is not an artifact hash. A QR fixture or unit mock is not deployed
acceptance evidence. No release PASS without deployed identity verification.
Do not publish real SSIDs, passwords, MACs, device serials or raw screenshots.

## Separate observations, never collapse the ladder

| Stage | Required observation | Insufficient substitute |
| --- | --- | --- |
| QR decode | Decode actual rendered/exported QR bytes to expected `WIFI:` payload | DOM text, URL QR, fixture payload |
| OS recognition | Supported phone scanner recognizes those bytes as Wi-Fi | Library decode alone |
| OS confirmation | Capture the OS join confirmation and user consent | Browser navigation or adb connect-network |
| Association | Physical station connected to intended AP/BSSID, corroborated at AP | NAT, emulator synthetic Wi-Fi, SSID substring |
| DHCP | Lease for that associated station and guest interface address/route | Host lease or unrelated default route |
| Captive observation | OS captive detection and portal response from guest vantage | Host browser or manually opened portal |
| Paid internet | Separately authorized payment and measured gate transition | QR join success; this milestone never spends |

Classify transport/connectivity, authentication, image availability, camera
capability and RF association prerequisites separately. Missing prerequisite =
BLOCKED/NOT_RUN, not product FAIL or PASS. Do not infer DHCP from association or
association from a successful HTTP request. Android emulation can exercise OS
recognition only with an explicitly supported camera-injection path; its default
NAT does not associate a physical radio with the restaurant AP.

## Existing runner seams and next execution

Use `lib/clients/adb.py:ADBDevice`, existing phone fixtures and
`tests/phone/test_captive_portal_auto.py` for the downstream captive stage.
That test has stateful fixtures: **do not run it without the venue gate**.
`test_camera_captive.py` tests camera restriction *inside* captive WebView,
not native Wi-Fi QR scanning. It is not a substitute recognition test.

`ADBDevice.shell_checked()` is the additive observation seam for the future QR
scenario. It preserves empty successful output, but raises a sanitized
infrastructure error on process launch failure, nonzero exit or timeout instead of turning a failed ADB
transport into a negative Wi-Fi observation. It does not reserve devices, validate
association, or make arbitrary shell commands safe. Example read-only calls after
authorized access: `shell_checked('cmd wifi status')` and
`shell_checked('ip -f inet addr show wlan0')`. Keep raw values private.

Host-only transport contracts (no router fixtures/device access):

```sh
python -m pytest --noconftest -o addopts= tests/unit/test_adb_checked.py -q
```

## Read-only native capability discovery/refusal

`tests/phone/test_qr_native.py` uses `lib/clients/qr_native.py` and the checked
ADB seam to discover capability and refuse launch, not drive the scanner.
It cannot produce native recognition acceptance. It does not use the stateful
router fixtures. Run ONLY this scoped path with `--noconftest`:

```sh
PRTA_QR_MANIFEST=/absolute/manifest.json python -m pytest --noconftest -o addopts= tests/phone/test_qr_native.py -q -rs
# Automation requiring a nonzero NOT_RUN exit:
python -m lib.clients.qr_native --image /absolute/portal.png --provenance /absolute/provenance.json --serial emulator-5556 --output results/qr-native/result.json
```

Manifest fields: `image` (absolute PNG), `serial`, `emulator_pid`,
`owned_isolated_emulator` (default false), `provenance` object. Provenance requires
`synthetic: true`, `portal_commit` (40 hex), `qr_sha256` (actual PNG bytes).
Record renderer/build/source linkage alongside it, not merely a claimed SHA.
Synthetic fixture identity is deliberately fixed to `PRTA-QR-SYNTHETIC`; never
supply private Wi-Fi credentials. Exact payload used in development:
`WIFI:T:WPA;S:PRTA-QR-SYNTHETIC;P:synthetic-only-123;H:false;;`.

**Do not boot an emulator for this path yet.** The API34 Google APIs revision14
image has an exported Settings enrollee scanner, but it is not recognition-only.
The corrected action is `android.settings.WIFI_DPP_ENROLLEE_QR_CODE_SCANNER`;
`com.android.settings/.wifi.dpp.WifiDppEnrolleeActivity` accepts it with no required
extras. Optional `ssid` is a display label, not a connection restriction.

Both [Android14 source](https://android.googlesource.com/platform/packages/apps/Settings/+/refs/tags/android-14.0.0_r1/src/com/android/settings/wifi/dpp/WifiDppQrCodeScannerFragment.java)
and installed APK DEX show standard `WIFI:` decode calling `addNetwork`,
`enableNetwork`, then `connect` for hidden/reachable networks without confirmation.
Not tapping Connect is therefore insufficient. An unreachable synthetic SSID does
not prevent the network-save side effect. The old spelling's failed resolution
was not evidence that the standard Wi-Fi scanner was missing.

The canonical adapter now performs read-only capability discovery, never launches
an activity, and classifies the enrollee component `UNSAFE_AUTO_JOIN`. Other
components are `UNVERIFIED`; unresolved action is `UNRESOLVED`, not proof that all
scanners are unavailable. All return NOT_RUN. English locale can come from
`persist.sys.locale` or the supported single-English `am get-config` qualifier
format; ambiguous/missing/multiple locale fallback remains unknown. It never sets
locale. `--emulator-pid` remains accepted for compatibility but cannot enable launch.

Next prerequisite: a native scanner/image with verified ordinary `WIFI:` decode
and an explicit confirmation boundary **before any network save/enable/join**,
plus exported/public UI entry and tested adapter. No such safe path is verified
in this installed image. No third-party install or internal-activity bypass is
permitted. See [offline evidence report](qr-native-discovery/REPORT.md).

Result JSON separates native recognition, OS consent, association, DHCP, and
captive statuses. All native stages remain NOT_RUN; NAT proves no RF or lease.
CLI exits: 0 recognition PASS only, 1 FAIL, 2 INFRA_ERROR, 4 NOT_RUN. Pytest skip
is **not acceptance** even when pytest exits zero. Host tests use doubles only:

```sh
python -m pytest --noconftest -o addopts= tests/unit/test_qr_native.py tests/unit/test_adb_checked.py -q
```

Native execution and the reserved physical downstream ladder remain unverified.
Do not run the generic phone profile for this isolated recognition-only scope.
