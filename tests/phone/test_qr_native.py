"""Recognition-only scenario. Use --noconftest: no router fixtures or deployment."""
import json
import os
from pathlib import Path

import pytest

from lib.clients.adb import ADBDevice
from lib.clients.qr_native import recognize


def test_native_wifi_qr():
    manifest = os.environ.get('PRTA_QR_MANIFEST')
    if not manifest:
        pytest.skip('NOT_RUN: PRTA_QR_MANIFEST synthetic portal artifact required')
    config = json.loads(Path(manifest).read_text())
    result = recognize(
        ADBDevice(config.get('serial')), Path(config['image']), config['provenance'],
        authorized=config.get('owned_isolated_emulator') is True,
        emulator_pid=config.get('emulator_pid'),
    )
    output = Path(os.environ.get('PRTA_QR_RESULT', 'results/qr-native/result.json'))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    stage = result['native_recognition']
    if stage['status'] == 'NOT_RUN':
        pytest.skip('NOT_RUN: ' + stage['reason'])
    assert stage['status'] == 'PASS', stage['reason']
