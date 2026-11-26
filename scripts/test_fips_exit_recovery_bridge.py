"""Hardware-free smoke of the actual embedded Go helper; all evidence is synthetic.

FIPS_EXIT_MODULE_REPO=/path/to/module python3 -m unittest discover -s scripts -p test_fips_exit_recovery_bridge.py -v
Requires go on PATH (run with GOTOOLCHAIN=go1.26.10).
"""
import copy
import json
import os
from pathlib import Path
import runpy
import subprocess
import unittest

lane = runpy.run_path(str(Path(__file__).with_name('fips-exit-packet-lane.py')))


class RecoveryBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = os.environ['FIPS_EXIT_MODULE_REPO']
        cls.normal = lane['render'](cls.repo)

    def synthetic_fixture(self):
        # Invented attestations ONLY for testing the pure planner, not observations.
        c = dict(Enabled=True, Selection='guest-forwarding',
                 LocalPublicKey=[1] + [0]*31, ExitPublicKey=[2] + [0]*31,
                 GuestInterface='guest-test', FIPSInterface='mesh-test',
                 TunnelInterface='exit-test', RoutingTable=200, RulePriority=10000, MTU=1240)
        e = dict(Verified=True, OwnershipVerified=True, OwnedConfig=copy.deepcopy(c),
                 Environment=dict(Verified=True, Interfaces={'guest-test':1500, 'mesh-test':1500, 'exit-test':1240},
                                  LocalPublicKey=c['LocalPublicKey'], LocalFIPSAddress=self.normal['outer'],
                                  AddressesConflictFree=True, PolicyOrderSafe=True, OuterRouteVerified=True),
                 Tunnel=dict(Name='exit-test', Mode='ipip6', Device='mesh-test',
                             Local=self.normal['outer'], Remote=self.normal['peer_outer'],
                             Inner=self.normal['inner']+'/32', MTU=1240, Up=True, ExactParameters=True),
                 GuestRule=dict(Priority=10000, Table=200, InputInterface='guest-test', Exact=True),
                 Routes=[dict(Type='unreachable', Destination='default', Protocol='static', Table=200, Metric=42760, Exact=True)])
        return dict(Config=c, RecoveryEvidence=e, GuardReadiness=dict.fromkeys(
            ['Forwarding','DNS','NativeIPv6','NAT','MSS','PortalAuthorization'], True))

    def test_normal_generation(self):
        self.assertTrue(self.normal['fragment'])
        self.assertTrue(self.normal['operations'])

    def test_missing_default_synthetic(self):
        result = lane['render'](self.repo, json.dumps(self.synthetic_fixture()).encode())
        self.assertEqual(len(result['operations']), 1)
        op = result['operations'][0]
        self.assertEqual(op['Program'], 'ip')
        self.assertEqual(op['Args'], ['-4','route','add','default','dev','exit-test','table','200','metric','100','proto','static'])

    def test_refuses_bad_input(self):
        for raw in (b'{', b'null', b'{}', b'{} {}', b'{"unknown":true}'):
            with self.subTest(raw=raw), self.assertRaises(subprocess.CalledProcessError) as caught:
                lane['render'](self.repo, raw)
            self.assertEqual(caught.exception.output, b'')

    def test_cli_plan_only(self):
        result = subprocess.run(['python3', str(Path(__file__).with_name('fips-exit-packet-lane.py')),
                                 '--module-repo', self.repo, '--recovery-json', '-'],
                                input=json.dumps(self.synthetic_fixture()), text=True,
                                capture_output=True, check=True)
        self.assertEqual(len(json.loads(result.stdout)['operations']), 1)

    def test_refuses_missing_safety(self):
        data = self.synthetic_fixture()
        data['RecoveryEvidence']['Routes'] = []
        with self.assertRaises(subprocess.CalledProcessError):
            lane['render'](self.repo, json.dumps(data).encode())


if __name__ == '__main__':
    unittest.main()
