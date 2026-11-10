"""Host-only contracts; no emulator or ADB is exercised."""
import hashlib
import json
from unittest.mock import Mock

import pytest
from lib.clients import qr_native

ACTION = 'android.settings.WIFI_DPP_ENROLLEE_QR_CODE_SCANNER'
ENROLLEE = 'com.android.settings/.wifi.dpp.WifiDppEnrolleeActivity'


def fixture(tmp_path):
    image = tmp_path / 'qr.png'
    image.write_bytes(b'fixture-not-a-real-qr')
    return image, {'qr_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                   'portal_commit': 'b' * 40, 'synthetic': True}


def probe(tmp_path, responses, **kwargs):
    image, provenance = fixture(tmp_path)
    device = Mock(serial='emulator-5556')
    device.shell_checked.side_effect = responses
    result = qr_native.recognize(device, image, provenance, authorized=True, **kwargs)
    commands = [call.args[0] for call in device.shell_checked.call_args_list]
    # Exact read-only allowlist forbids start, input, UI dumps and network changes.
    assert set(commands) <= {
        'getprop ro.kernel.qemu', 'getprop ro.build.version.sdk',
        'getprop persist.sys.locale', 'am get-config',
        f'cmd package resolve-activity --brief -a {ACTION}',
    }
    assert all(result[k]['status'] == 'NOT_RUN'
               for k in ('os_consent', 'association', 'dhcp', 'captive'))
    return result, commands


@pytest.mark.parametrize('component,classification', [
    (ENROLLEE, 'UNSAFE_AUTO_JOIN'),
    ('com.android.settings/com.android.settings.wifi.dpp.WifiDppEnrolleeActivity', 'UNSAFE_AUTO_JOIN'),
    ('com.android.settings/.Scanner', 'UNVERIFIED'),
    ('vendor.settings/.Scanner', 'UNVERIFIED'),
    (ENROLLEE + 'Extra', 'UNVERIFIED'),
    ('No activity found', 'UNRESOLVED'),
    ('', 'UNRESOLVED'),
])
def test_components_fail_closed(tmp_path, component, classification):
    # Former fictitious .Scanner happy-path PASS is not evidence of safe decoding.
    result, commands = probe(tmp_path, ['1', '34', 'en-US', component], emulator_pid=123)
    assert result['native_recognition']['status'] == 'NOT_RUN'
    assert result['scanner_capability'] == {
        'classification': classification, 'action': ACTION, 'component': component}
    assert commands[-1] == f'cmd package resolve-activity --brief -a {ACTION}'
    if classification == 'UNRESOLVED':
        assert 'other scanners' in result['native_recognition']['reason']


@pytest.mark.parametrize('locale', ['en-US', 'en_US', 'en'])
def test_english_locale_normalized(tmp_path, locale):
    result, commands = probe(tmp_path, ['1', '34', locale, ENROLLEE])
    assert result['scanner_capability']['classification'] == 'UNSAFE_AUTO_JOIN'
    assert 'am get-config' not in commands


# Android 14 runGetConfig prints resource qualifiers, not Configuration.toString.
# These are source-derived fixtures, not captured emulator output.
@pytest.mark.parametrize('config,supported', [
    ('config: mcc310-mnc260-en-rUS-ldltr-sw360dp-w360dp-h640dp-normal-notlong-notround-port-notnight-xhdpi-finger-keysexposed-qwerty-navexposed-dpad-720x1280-v34\nabi: x86_64', True),
    ('config: en-ldltr-sw360dp-v34\nabi: x86_64\n', True),
    ('config: b+en+US-ldltr-sw360dp-v34\nabi: x86_64', True),
    ('config: b+en+Latn+US-ldltr-sw360dp-v34\nabi: x86_64', True),
    ('config: mcc310-en-rGB-feminine-ldltr-sw360dp-v34\nabi: x86_64', True),
    # AOSP preserves list order with commas; reject all multi-locale fallback.
    ('config: de-rDE,en-rUS-ldltr-sw360dp-v34\nabi: x86_64', False),
    ('config: en-rUS,de-rDE-ldltr-sw360dp-v34\nabi: x86_64', False),
    ('config: b+de+Latn+DE,b+en+US-ldltr-v34\nabi: x86_64', False),
    ('config: ldltr-sw360dp-v34\nabi: x86_64', False),
    ('config: unknown-en-rUS-ldltr-v34\nabi: x86_64', False),
    ('config: de-rDE-ldltr-v34\nabi: x86_64', False),
    ('config: en-rUS-ldltr\nconfig: de-rDE-ldltr\nabi: x86_64', False),
    ('config: en-rUS-ldltr', False),
    ('config: en-rUS-ldltr\n{1.0 [en_US] ldltr}', False),
    ('CurrentConfiguration={1.0 [en_US] ldltr}', False),
    ('{1.0 [en_US] ldltr}', False),
    ('{1.0 [] ldltr}', False),
    ('', False),
    ('{1.0 [unknown] ldltr}', False),
])
def test_empty_locale_uses_current_global_primary_only(tmp_path, config, supported):
    result, commands = probe(tmp_path, ['1', '34', '', config, ENROLLEE])
    assert 'am get-config' in commands
    assert result['native_recognition']['status'] == 'NOT_RUN'
    if supported:
        assert result['scanner_capability']['classification'] == 'UNSAFE_AUTO_JOIN'
    else:
        assert 'English' in result['native_recognition']['reason']
        assert not commands[-1].startswith('cmd package')


@pytest.mark.parametrize('locale', ['de-DE', 'unknown', 'english', 'enough'])
def test_unsupported_locale_does_not_guess(tmp_path, locale):
    result, commands = probe(tmp_path, ['1', '34', locale])
    assert result['native_recognition']['status'] == 'NOT_RUN'
    assert 'English' in result['native_recognition']['reason']
    assert 'am get-config' not in commands


@pytest.mark.parametrize('responses,status', [
    (['0'], 'NOT_RUN'), (['1', '35'], 'NOT_RUN'),
    ([RuntimeError('transport failed')], 'INFRA_ERROR'),
    (['1', '34', '', RuntimeError('config failed')], 'INFRA_ERROR'),
    (['1', '34', 'en-US', RuntimeError('resolve failed')], 'INFRA_ERROR'),
])
def test_read_only_probe_errors(tmp_path, responses, status):
    result, _ = probe(tmp_path, responses)
    assert result['native_recognition']['status'] == status


@pytest.mark.parametrize('authorized,serial', [(False, 'emulator-5556'), (True, 'physical')])
def test_authorization_and_device_guards(tmp_path, authorized, serial):
    device = Mock(serial=serial)
    result = qr_native.recognize(device, tmp_path / 'missing', {}, authorized=authorized)
    assert result['native_recognition']['status'] == 'NOT_RUN'
    device.shell_checked.assert_not_called()


@pytest.mark.parametrize('bad', [None, {}, {'synthetic': True, 'portal_commit': 123},
    {'synthetic': True, 'portal_commit': 'not-a-commit'},
    {'synthetic': True, 'portal_commit': 'b' * 40, 'qr_sha256': '0' * 64}])
def test_invalid_provenance(tmp_path, bad):
    image, _ = fixture(tmp_path)
    device = Mock(serial='emulator-5556')
    assert qr_native.recognize(device, image, bad, authorized=True)['native_recognition']['status'] == 'FAIL'
    device.shell_checked.assert_not_called()


def test_missing_image_is_infra_error(tmp_path):
    image, provenance = fixture(tmp_path)
    image.unlink()
    device = Mock(serial='emulator-5556')
    assert qr_native.recognize(device, image, provenance, authorized=True)['native_recognition']['status'] == 'INFRA_ERROR'
    device.shell_checked.assert_not_called()


@pytest.mark.parametrize('valid', [True, False])
def test_cli_compatibility_never_launches(tmp_path, monkeypatch, valid):
    image, provenance = fixture(tmp_path)
    source = tmp_path / 'provenance.json'
    source.write_text(json.dumps(provenance) if valid else '{broken')
    output = tmp_path / 'out/result.json'
    device = Mock(serial='emulator-5556')
    device.shell_checked.side_effect = ['1', '34', 'en-US', ENROLLEE]
    monkeypatch.setattr(qr_native, 'ADBDevice', lambda serial: device)
    monkeypatch.setattr('sys.argv', ['qr_native', '--image', str(image),
        '--provenance', str(source), '--serial', 'emulator-5556',
        '--emulator-pid', '123', '--owned-isolated-emulator', '--output', str(output)])
    assert qr_native.main() == (4 if valid else 1)
    assert json.loads(output.read_text())['native_recognition']['status'] == ('NOT_RUN' if valid else 'FAIL')
    assert all('am start' not in call.args[0] for call in device.shell_checked.call_args_list)
