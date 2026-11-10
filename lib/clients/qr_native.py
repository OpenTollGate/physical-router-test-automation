"""Native Settings QR recognition only; never join a network or infer RF from NAT."""
import argparse
import hashlib
import json
import re
import socket
from pathlib import Path

from lib.clients.adb import ADBDevice

ACTION = 'android.settings.WIFI_DPP_ENROLLEE_QR_CODE_SCANNER'


def recognize(device, image, provenance, *, authorized=False, emulator_pid=None):
    result: dict = {k: {'status': 'NOT_RUN', 'reason': reason} for k, reason in {
        'native_recognition': 'Explicit isolated-emulator ownership approval required',
        'os_consent': 'Recognition only: no Connect tap or consent supplied',
        'association': 'Emulator NAT is not physical RF association',
        'dhcp': 'No associated physical guest lease measured',
        'captive': 'No native captive detection measured',
    }.items()}
    result['venue'] = {'host': socket.gethostname(), 'network': 'emulator NAT, not RF'}
    result['provenance'] = provenance

    def finish(status, reason):
        result['native_recognition'] = {'status': status, 'reason': reason}
        return result

    if not authorized:
        return result
    if not re.fullmatch(r'emulator-\d+', device.serial or ''):
        return finish('NOT_RUN', 'Only an explicitly selected isolated emulator is supported')
    try:
        if (not isinstance(provenance, dict)
                or provenance.get('synthetic') is not True
                or not isinstance(provenance.get('portal_commit'), str)
                or not re.fullmatch('[0-9a-f]{40}', provenance.get('portal_commit', ''))
                or hashlib.sha256(Path(image).read_bytes()).hexdigest()
                != provenance.get('qr_sha256')):
            return finish('FAIL', 'Synthetic portal source/hash provenance invalid')
        if device.shell_checked('getprop ro.kernel.qemu') != '1':
            return finish('NOT_RUN', 'Selected device is not an emulator')
        if device.shell_checked('getprop ro.build.version.sdk') != '34':
            return finish('NOT_RUN', 'Prerequisite: supported API34 Settings UI adapter')
        locale = device.shell_checked('getprop persist.sys.locale').strip()
        if not locale:
            config = device.shell_checked('am get-config')
            # Android 14 runGetConfig emits resourceQualifierString + ABI.
            # Locales follow optional MCC/MNC, in LocaleList order, comma joined.
            # Accept only a single supported English locale; unknown/multi-locale
            # output stays unknown (never search later qualifiers or task braces).
            match = re.fullmatch(
                r'config: (?:mcc[0-9]+-(?:mnc[0-9]+-)?)?'
                r'(?:en(?:-r[A-Z]{2})?|b\+en(?:\+[A-Z][a-z]{3})?'
                r'(?:\+(?:[A-Z]{2}|[0-9]{3}))?)'
                r'-(?:(?:neuter|feminine|masculine)-)?ldltr'
                r'(?:-[A-Za-z0-9]+)*\nabi: [A-Za-z0-9_,.-]+\n?', config)
            locale = 'en' if match else ''
        if not re.fullmatch(r'en(?:-[A-Za-z0-9]+)*', locale.replace('_', '-')):
            return finish('NOT_RUN', 'Prerequisite: English Settings UI adapter; locale unknown or unsupported')
        component = device.shell_checked(
            f'cmd package resolve-activity --brief -a {ACTION}').strip()
        if not re.fullmatch(r'[\w.]+/[\w.$]+', component):
            classification = 'UNRESOLVED'
            reason = f'{ACTION} unresolved; this does not establish that other scanners are unavailable'
        elif component in {
            'com.android.settings/.wifi.dpp.WifiDppEnrolleeActivity',
            'com.android.settings/com.android.settings.wifi.dpp.WifiDppEnrolleeActivity',
        }:
            classification = 'UNSAFE_AUTO_JOIN'
            # Android14 source and installed API34 image: standard WIFI payloads
            # add/enable/connect without confirmation. No decode-only contract.
            reason = 'Settings enrollee scanner can auto-join Wi-Fi without confirmation; launch refused'
        else:
            classification = 'UNVERIFIED'
            reason = 'Resolved scanner has no verified decode-only safety contract; launch refused'
        result['scanner_capability'] = {
            'classification': classification, 'action': ACTION, 'component': component,
        }
        # emulator_pid remains accepted for CLI/API compatibility, never a bypass.
        return finish('NOT_RUN', reason)
    except (RuntimeError, OSError, ValueError):
        return finish('INFRA_ERROR', 'ADB transport or local emulator/provenance inspection failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--emulator-pid', type=int)
    parser.add_argument('--owned-isolated-emulator', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        provenance = json.loads(args.provenance.read_text())
    except (OSError, ValueError):
        provenance = None
    result = recognize(ADBDevice(args.serial), args.image,
                       provenance,
                       authorized=args.owned_isolated_emulator,
                       emulator_pid=args.emulator_pid)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    return {'PASS': 0, 'FAIL': 1, 'NOT_RUN': 4, 'INFRA_ERROR': 2}[result['native_recognition']['status']]


if __name__ == '__main__':
    raise SystemExit(main())
