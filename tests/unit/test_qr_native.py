"""Host-only contracts: doubles never represent native acceptance."""
import hashlib
from unittest.mock import Mock

from lib.clients.qr_native import recognize, wifi_confirmation


def test_confirmation_requires_settings_ssid_and_connect():
    xml = '<hierarchy><node package="com.android.settings" text="PRTA-QR-SYNTHETIC"/><node package="com.android.settings" text="Connect" enabled="true"/></hierarchy>'
    assert wifi_confirmation(xml)
    assert not wifi_confirmation(xml.replace('com.android.settings', 'com.android.chrome'))
    assert not wifi_confirmation(xml.replace('Connect', 'Cancel'))
    assert not wifi_confirmation(xml.replace('PRTA-QR-SYNTHETIC', 'Other'))
    assert not wifi_confirmation('malformed')


def test_no_authorization_never_calls_device(tmp_path):
    device = Mock()
    result = recognize(device, tmp_path / 'missing', {}, authorized=False)
    assert result['native_recognition']['status'] == 'NOT_RUN'
    device.shell_checked.assert_not_called()


def fixture(tmp_path):
    image = tmp_path / 'qr.png'
    image.write_bytes(b'fixture-not-a-real-qr')
    return image, {'qr_sha256': hashlib.sha256(image.read_bytes()).hexdigest(),
                   'portal_commit': 'b' * 40, 'synthetic': True}


def test_hash_mismatch_fails_before_device(tmp_path):
    image, provenance = fixture(tmp_path)
    provenance['qr_sha256'] = '0' * 64
    device = Mock(serial='emulator-5556')
    result = recognize(device, image, provenance, authorized=True)
    assert result['native_recognition']['status'] == 'FAIL'
    device.shell_checked.assert_not_called()


def test_missing_scanner_is_explicit_not_run(tmp_path):
    image, provenance = fixture(tmp_path)
    device = Mock(serial='emulator-5556')
    device.shell_checked.side_effect = ['1', '34', 'en-US', 'No activity found']
    result = recognize(device, image, provenance, authorized=True)
    assert result['native_recognition']['status'] == 'NOT_RUN'
    assert 'WIFI_DPP_QR_CODE_SCANNER' in result['native_recognition']['reason']
    assert all(result[k]['status'] == 'NOT_RUN' for k in ('os_consent', 'association', 'dhcp', 'captive'))


def test_transport_failure_not_product_failure(tmp_path):
    image, provenance = fixture(tmp_path)
    device = Mock(serial='emulator-5556')
    device.shell_checked.side_effect = RuntimeError('ADB observation failed: exit 1')
    result = recognize(device, image, provenance, authorized=True)
    assert result['native_recognition']['status'] == 'INFRA_ERROR'


def test_bound_camera_recognition_never_consents(tmp_path, monkeypatch):
    image, provenance = fixture(tmp_path)
    device = Mock(serial='emulator-5556')
    xml = '<hierarchy><node package="com.android.settings" text="PRTA-QR-SYNTHETIC"/><node package="com.android.settings" text="Connect" enabled="true"/></hierarchy>'
    device.shell_checked.side_effect = ['1', '34', 'en-US', 'com.android.settings/.Scanner', '<hierarchy/>', 'Status: ok', xml]
    from pathlib import Path
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: (
        ('\0'.join(['/sdk/qemu-system-x86_64', '-port', '5556', '-camera-back', 'imagefile:' + str(image), ''])).encode()
        if str(p) == '/proc/123/cmdline' else original(p)))
    original_stat = Path.stat
    import os
    monkeypatch.setattr(Path, 'stat', lambda p, **kw: Mock(st_uid=os.getuid()) if str(p) == '/proc/123' else original_stat(p, **kw))
    result = recognize(device, image, provenance, authorized=True, emulator_pid=123)
    assert result['native_recognition']['status'] == 'PASS'
    assert result['os_consent']['status'] == 'NOT_RUN'
    assert not any('input tap' in str(c) or 'connect-network' in str(c) for c in device.shell_checked.call_args_list)


def test_invalid_provenance_is_structured(tmp_path):
    image, _ = fixture(tmp_path)
    for provenance in (None, {'synthetic': True, 'portal_commit': 123}):
        result = recognize(Mock(serial='emulator-5556'), image, provenance, authorized=True)
        assert result['native_recognition']['status'] == 'FAIL'


def test_unsupported_sdk_is_not_run(tmp_path):
    image, provenance = fixture(tmp_path)
    device = Mock(serial='emulator-5556')
    device.shell_checked.side_effect = ['1', '35']
    assert recognize(device, image, provenance, authorized=True)['native_recognition']['status'] == 'NOT_RUN'


def test_physical_device_refused(tmp_path):
    image, provenance = fixture(tmp_path)
    device = Mock(serial='physical')
    assert recognize(device, image, provenance, authorized=True)['native_recognition']['status'] == 'NOT_RUN'
    device.shell_checked.assert_not_called()
