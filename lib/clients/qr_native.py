"""Native Settings QR recognition only; never join a network or infer RF from NAT."""
import argparse
import hashlib
import json
import os
import re
import socket
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from lib.clients.adb import ADBDevice

ACTION = 'android.settings.WIFI_DPP_QR_CODE_SCANNER'
SSID = 'PRTA-QR-SYNTHETIC'


def wifi_confirmation(xml):
    """English API34 Settings adapter; unsupported UI is not a positive decode."""
    try:
        nodes = ET.fromstring(xml[xml.index('<hierarchy'):]).iter('node')
        nodes = [n for n in nodes if n.get('package') == 'com.android.settings']
        return (any(n.get('text') == SSID for n in nodes)
                and any(n.get('text') == 'Connect' and n.get('enabled') == 'true'
                        for n in nodes))
    except (ValueError, ET.ParseError):
        return False


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
        if not device.shell_checked('getprop persist.sys.locale').startswith('en-'):
            return finish('NOT_RUN', 'Prerequisite: English Settings UI adapter')
        component = device.shell_checked(
            f'cmd package resolve-activity --brief -a {ACTION}')
        if not re.fullmatch(r'com\.android\.settings/[\w.$]+', component):
            return finish('NOT_RUN', f'Prerequisite: exported Settings {ACTION} activity')
        # Bind the supported imagefile input to this actual emulator process, not
        # a caller assertion. Never boot, stop, install into or reconfigure an AVD.
        if emulator_pid is None:
            return finish('NOT_RUN', 'Prerequisite: owned emulator PID with -camera-back imagefile:QR')
        proc = Path('/proc') / str(int(emulator_pid))
        if proc.stat().st_uid != os.getuid():
            return finish('NOT_RUN', 'Emulator process is owned by another user')
        args = proc.joinpath('cmdline').read_bytes().decode().split('\0')
        port = device.serial.removeprefix('emulator-')
        pairs = list(zip(args, args[1:]))
        if (('qemu-system' not in args[0] and 'emulator' not in args[0])
                or ('-port', port) not in pairs
                or ('-camera-back', 'imagefile:' + str(Path(image).resolve())) not in pairs):
            return finish('NOT_RUN', 'Prerequisite: PID/serial/exact imagefile camera binding')
        baseline = device.shell_checked('uiautomator dump /sdcard/prta-qr.xml >/dev/null && cat /sdcard/prta-qr.xml')
        if wifi_confirmation(baseline):
            return finish('NOT_RUN', 'Pre-existing synthetic confirmation: reset owned guest before recognition')
        start = device.shell_checked(f'am start -W -a {ACTION}')
        if 'Error:' in start or 'Status: ok' not in start:
            return finish('INFRA_ERROR', 'Settings scanner activity did not start')
        for _ in range(10):
            xml = device.shell_checked('uiautomator dump /sdcard/prta-qr.xml >/dev/null && cat /sdcard/prta-qr.xml')
            if wifi_confirmation(xml):
                result['confirmation_prompt'] = 'Observed Settings synthetic SSID + enabled Connect; not tapped'
                return finish('PASS', 'Native Settings recognized camera-injected synthetic QR; consent not exercised')
            time.sleep(1)
        return finish('FAIL', 'Scanner launched but expected Settings Wi-Fi confirmation absent after 10 observations')
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
