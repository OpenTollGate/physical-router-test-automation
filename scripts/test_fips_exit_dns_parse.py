"""Offline contract tests; real dnsmasq evidence comes from --parse-only."""
import contextlib
import io
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch

helper = runpy.run_path(str(Path(__file__).with_name('fips-exit-bootstrap-dns.py')))


class ParseOnlyTests(unittest.TestCase):
    def exercise(self, codes):
        calls = []
        def run(argv, **kwargs):
            self.assertEqual(argv[:2], ['/dnsmasq', '--test'])
            self.assertEqual(len(argv), 3)
            path = Path(argv[2].removeprefix('--conf-file='))
            calls.append(path.read_bytes())
            self.assertEqual(kwargs['timeout'], 10)
            return subprocess.CompletedProcess(argv, codes[len(calls)-1], '', 'parser diagnostic')
        with patch('subprocess.run', side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            helper['parse_only']('no-resolv\n', '/dnsmasq')
        return calls

    def test_unchanged_bytes_and_invalid_control(self):
        calls = self.exercise([0, 1])
        self.assertEqual(calls[0], b'no-resolv\n')
        self.assertEqual(calls[1], calls[0] + b'prta-invalid-directive-control\n')

    def test_rejected_generated_config_fails_after_both_controls(self):
        with self.assertRaisesRegex(RuntimeError, 'module defect'):
            self.exercise([1, 1])

    def test_accepted_invalid_control_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'invalid directive'):
            self.exercise([0, 0])


if __name__ == '__main__':
    unittest.main()
