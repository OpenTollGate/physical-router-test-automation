"""Offline parser tests; no kernel operations or recovery attestations."""
import json
from pathlib import Path
import runpy
import unittest

HELPER = Path(__file__).with_name('fips-exit-routes.py')
LOG = Path(__file__).resolve().parents[1] / 'docs/evidence/fips-exit-ipip6-down-up.log'


class RecoveryRoutes(unittest.TestCase):
    def parse(self, raw, **context):
        self.assertTrue(HELPER.is_file(), 'strict route parser is missing')
        return runpy.run_path(str(HELPER))['parse_routes'](raw, **context)

    def test_observed_empty_flags_safety(self):
        observed = json.loads(LOG.read_text().splitlines()[23])['table_200_routes']
        self.assertEqual(self.parse(json.dumps(observed), query_table=200), [dict(
            Type='unreachable', Destination='default', Device='', Protocol='static',
            Table=200, Metric=42760, Exact=True)])

    def test_complete_inventory_with_tunnel(self):
        safety = json.loads(LOG.read_text().splitlines()[23])['table_200_routes'][0]
        tunnel = dict(dst='default', dev='exit-test', protocol='static',
                      scope='link', metric=100, flags=[])
        for routes in ([safety, tunnel], [tunnel, safety]):
            with self.subTest(routes=routes):
                result = self.parse(json.dumps(routes), query_table=200)
                self.assertEqual(result[routes.index(tunnel)], dict(
                    Type='unicast', Destination='default', Device='exit-test',
                    Protocol='static', Table=200, Metric=100, Exact=True))
                self.assertEqual(len(result), 2)

    def safety(self):
        return json.loads(LOG.read_text().splitlines()[23])['table_200_routes'][0]

    def test_trusted_query_context_required(self):
        raw = json.dumps([self.safety()])
        with self.assertRaises(TypeError):
            self.parse(raw)
        for table in (None, '200', True, 200.0, 0, 201):
            with self.subTest(table=table), self.assertRaises(ValueError):
                self.parse(raw, query_table=table)

    def test_refuses_duplicate_json_attributes(self):
        raw = json.dumps([self.safety()]).replace('"metric": 42760', '"metric": 1, "metric": 42760')
        with self.assertRaises(ValueError):
            self.parse(raw, query_table=200)

    def test_refuses_noninteger_metric(self):
        route = self.safety()
        route['metric'] = 42760.0
        with self.assertRaises(ValueError):
            self.parse(json.dumps([route]), query_table=200)

    def test_refuses_malformed_inventory(self):
        for raw in ('null', '1', 'true', '{}', '"routes"', '[null]', '[[]]', '{', '[] []', '[NaN]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.parse(raw, query_table=200)

    def test_refuses_missing_safety_duplicates_and_foreign_routes(self):
        safety = self.safety()
        tunnel = dict(dst='default', dev='exit-test', protocol='static',
                      scope='link', metric=100, flags=[])
        for routes in ([], [tunnel], [safety, safety], [safety, tunnel, tunnel],
                       [safety, dict(dst='10.0.0.0/8')]):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                self.parse(json.dumps(routes), query_table=200)

    def test_refuses_every_unknown_or_changed_attribute(self):
        for original in (self.safety(), dict(dst='default', dev='exit-test',
                         protocol='static', scope='link', metric=100, flags=[])):
            changes = dict(dst='0.0.0.0/0', dev='wan', protocol='boot', metric=101,
                           flags=['linkdown'], gateway='192.0.2.1', nexthops=[],
                           table=201, scope='host', type='blackhole', prefsrc='192.0.2.2',
                           unknown=True)
            for key, value in changes.items():
                route = dict(original, **{key: value})
                with self.subTest(key=key, original=original), self.assertRaises(ValueError):
                    inventory = [route] if original == self.safety() else [self.safety(), route]
                    self.parse(json.dumps(inventory), query_table=200)
            for key in original:
                route = dict(original)
                del route[key]
                with self.subTest(missing=key), self.assertRaises(ValueError):
                    inventory = [route] if original == self.safety() else [self.safety(), route]
                    self.parse(json.dumps(inventory), query_table=200)


if __name__ == '__main__':
    unittest.main()
