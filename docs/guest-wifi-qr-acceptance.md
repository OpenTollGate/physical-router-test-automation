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
infrastructure error on nonzero exit or timeout instead of turning a failed ADB
transport into a negative Wi-Fi observation. It does not reserve devices, validate
association, or make arbitrary shell commands safe. Example read-only calls after
authorized access: `shell_checked('cmd wifi status')` and
`shell_checked('ip -f inet addr show wlan0')`. Keep raw values private.

Host-only transport contracts (no router fixtures/device access):

```sh
python -m pytest --noconftest -o addopts= tests/unit/test_adb_checked.py -q
```

The next milestone is a required phone scenario using this seam, actual
portal-produced QR bytes, a reserved physical client and router, and separate
recognition/confirmation/association/DHCP/captive assertions. Do not fabricate
that scenario's PASS while those prerequisites are unavailable.
