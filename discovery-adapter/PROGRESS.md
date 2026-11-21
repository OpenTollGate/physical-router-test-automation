# Native QR discovery: locale fallback correction

## Android 14 source verification

Sources inspected directly at pinned `android-14.0.0_r1`:

- [ActivityManagerShellCommand.java — runGetConfig](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-14.0.0_r1/services/core/java/com/android/server/am/ActivityManagerShellCommand.java): obtains `mInterface.getConfiguration()` and prints `config: ` + `Configuration.resourceQualifierString(config, metrics)`, then `abi: ` + comma-joined supported ABIs. It does **not** print Configuration.toString braces.
- [Configuration.java — resourceQualifierString and localesToResourceQualifier](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-14.0.0_r1/core/java/android/content/res/Configuration.java): optional MCC/MNC precede the locale qualifier. `localesToResourceQualifier` traverses LocaleList by index, preserves order, and comma-separates locales. Traditional forms are `xx` / `xx-rYY`; other locales use `b+language[+script][+country][+variant]`. Empty languages are skipped; extensions are ignored. Thus a later English locale is not proof of an English primary locale.

The fallback now accepts only the two-line command-output envelope with a single supported English qualifier at the correct position, followed by optional grammatical gender and LTR layout. It supports simple `en`, `en-rUS`, and bounded BCP47 forms such as `b+en+US` and `b+en+Latn+US`. It deliberately rejects all multi-locale outputs, including English-first lists, rather than inferring the primary locale from potentially lossy serialization. Unsupported forms, missing locale, duplicate config lines, arbitrary braces and task-local CurrentConfiguration remain unknown. This is a conservative subset parser, not a complete resource-qualifier validator.

Fixtures are source-derived host-test inputs, **not emulator captures**. Retained task-local CurrentConfiguration output is not global-locale evidence. No emulator boot, scanner launch, UI input, or network mutation was performed.

## Strict RED → GREEN

Tests replaced the former invented braces happy path before production changes.

- RED: `python3 -m pytest tests/unit/test_qr_native.py -q` → **8 failed, 40 passed**. Five real-format English cases failed because no scanner-capability probe was reached; three arbitrary-brace cases incorrectly reached the probe. The failures establish both the missing real-format support and the unsafe evidence interpretation.
- GREEN: `python3 -m coverage run --branch --source=lib.clients.qr_native -m pytest tests/unit/test_qr_native.py -q` → **48 passed**.
- `python3 -m coverage report -m` → `lib/clients/qr_native.py`: **65 statements, 1 missed; 20 branches, 1 partial; 98%**. Missing line 109 is the `__main__` entrypoint; CLI main behavior is tested directly.
- All probe fixtures enforce the existing exact read-only command allowlist and downstream NOT_RUN statuses.

## Broader regression attempt

`python3 -m pytest tests/unit -q` cannot complete collection under Python 3.11: `lib/cloud_lab/worker/conwrt_deploy.py:57` has an f-string expression containing a backslash.

Retry excluding only that collection blocker:
`python3 -m pytest tests/unit -q --ignore=tests/unit/test_conwrt_deploy.py`
→ **3 failed, 1289 passed, 8 skipped, 3 xfailed, 9 errors**. Remaining failures are password-convention fixture lookup, read-only router expected SSH-call count, and Python 3.11 compilation of `scripts/meshlink-lab.py`; nine setup errors concern absent `lib.hardware_lock._held_lock`. These are outside the edited locale adapter/tests; no full-suite-green claim is made and no unrelated repairs were attempted.
