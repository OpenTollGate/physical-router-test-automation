# QR native discovery — 2026-11-10

Outcome: prerequisite correction verified offline on x280. Native recognition and
all downstream acceptance remain NOT_RUN. No additional emulator boot justified:
the installed scanner is exported and handles WIFI, but automatically saves and
may join. No device/host settings, network, reservation, funds or lab changes.

## Provenance and correction

Base PR340 live head checked 5331b6027871935b24702c4c433f339c24c91378;
author/head owner and gh login felixfelix-bot; existing draft only.
Code milestones fee0989f1a36679c1fc21d0f40717cdb861dc86e and 7c55619.

Installed API34 google_apis x86_64 revision14 system.img SHA256:
156534aa2c707edfb57026f7b573a561e513433d7643d9ce3230ae6d65804c7a
(before/after unchanged). SettingsGoogle.apk SHA256:
e3b3ed1dc4c388304c7283ba03510d03182344e1f2e9cc6299670f52bc05acaf.
Read-only GPT/logical partition extraction, debugfs without -w, SDK aapt/decdump
(no mounts/root/boot) established exported WifiDppEnrolleeActivity and action
android.settings.WIFI_DPP_ENROLLEE_QR_CODE_SCANNER. Actual DEX confirmed
WIFI -> handleZxingWifiFormat -> handler -> addNetwork -> enableNetwork ->
connect if hidden/reachable; no confirmation. No extra is required; ssid is a
label, not a safety constraint. Do not use a component/internal-UI bypass.

Previous REPORT's “missing exported scanner” conclusion is superseded: it tested
the wrong action. Its “global [en_US]” wording was also too strong: retained
commands show the launcher task's CurrentConfiguration, not global am get-config.
Empty persist.sys.locale is not proof of unknown UI locale. The fallback now
uses official am get-config single-English resource qualifiers, never invents a
locale from task-local braces, and leaves ambiguous/multiple locales unsupported.

## Authoritative sources (android-14.0.0_r1)

- [Manifest, exported enrollee](https://android.googlesource.com/platform/packages/apps/Settings/+/refs/tags/android-14.0.0_r1/AndroidManifest.xml)
- [Enrollee action/extras](https://android.googlesource.com/platform/packages/apps/Settings/+/refs/tags/android-14.0.0_r1/src/com/android/settings/wifi/dpp/WifiDppEnrolleeActivity.java)
- [WIFI and DPP parser](https://android.googlesource.com/platform/packages/apps/Settings/+/refs/tags/android-14.0.0_r1/src/com/android/settings/wifi/dpp/WifiQrCode.java)
- [Scanner mutation, lines191–210](https://android.googlesource.com/platform/packages/apps/Settings/+/refs/tags/android-14.0.0_r1/src/com/android/settings/wifi/dpp/WifiDppQrCodeScannerFragment.java)
- [am get-config output](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-14.0.0_r1/services/core/java/com/android/server/am/ActivityManagerShellCommand.java)
- [Locale resource qualifier format](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-14.0.0_r1/core/java/android/content/res/Configuration.java)

## Verification / commands / exits

Quota gate ~/.hermes/profiles/manager/scripts/dispatch-gate.sh exit0; actual label
“ALLOW via z.ai quota (ours)” recorded without interpreting provider inventory.
TDD first correction RED20 failed/16 passed; GREEN36 passed. Review caught invented
am get-config brace fixture; corrected using authoritative source: RED8 failed/
40 passed, GREEN48 passed. Parent reran combined adapter + adb tests:

```
COVERAGE_FILE=/home/c08r4d0r/reports/qr-native-20261110/discovery-adapter/parent.coverage python3 -m coverage run --branch --source=lib.clients.qr_native -m pytest --noconftest -o addopts= tests/unit/test_qr_native.py tests/unit/test_adb_checked.py -q
# exit0: 58 passed
COVERAGE_FILE=/home/c08r4d0r/reports/qr-native-20261110/discovery-adapter/parent.coverage python3 -m coverage report -m
# exit0: 98%, only module-entry SystemExit line109 uncovered
git diff --check
# exit0
```

Initial parent pytest --cov command exit4 (pytest-cov absent); used installed
coverage instead, no install. Full suite is NOT green: Python3.11 cannot collect
existing Python3.12 f-string syntax, and excluding that blocker reveals unrelated
fixture/failure errors. Details in discovery-adapter/PROGRESS.md. Scoped independent
read-only review found no blocking findings after qualifier correction; all device
commands read-only, no launch/PASS path or PID bypass, no removed-symbol references.

Exact offline commands/exits and raw evidence retained locally under
/home/c08r4d0r/reports/qr-native-20261110/{discovery-source,discovery-image,discovery-adapter}/.
Image FINDINGS.md points to manifest5908–5915, DEX handler985–1044,
handleSuccessfulResult8298–8331. Static evidence is not runtime decode proof.

## Remaining prerequisite

Need a supported native scanner/image with verified ordinary WIFI decode and
explicit confirmation BEFORE saving/enabling/joining, public/exported path and
tested adapter. No such safe path verified here; synthetic/unreachable SSID alone
is insufficient. Adapter classifies UNSAFE_AUTO_JOIN, UNVERIFIED or UNRESOLVED and
refuses all launches. Do not boot merely to repeat the corrected intent.
