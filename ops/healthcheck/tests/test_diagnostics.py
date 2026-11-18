import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('diagnostics', Path(__file__).parents[1] / 'diagnostics.py')
diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostics)


class FakeProcess:
    pid = 999999

    def __init__(self, data, status=0):
        self.stdout = io.BytesIO(data)
        self.status = status

    def wait(self, timeout=None):
        return self.status

    def poll(self):
        return self.status


class DiagnosticTests(unittest.TestCase):
    def test_snapshot_requests_last_200_lines_and_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(diagnostics.subprocess, 'Popen', return_value=FakeProcess(b'crash evidence')) as popen:
                path = diagnostics.capture(Path(temp), 'caddy', 200, 262144)
            self.assertIn(b'crash evidence', path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(popen.call_args.args[0][-4:], ['--timestamps', '--tail', '200', 'caddy'])
            self.assertIn(b'truncated=false', path.read_bytes())

    def test_large_log_lines_have_a_total_byte_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(diagnostics.subprocess, 'Popen', return_value=FakeProcess(b'x' * 1000000)), \
                    patch.object(diagnostics.os, 'killpg') as kill:
                path = diagnostics.capture(Path(temp), 'caddy', 200, 1024)
            self.assertLessEqual(path.stat().st_size, 1024)
            self.assertIn(b'truncated=true', path.read_bytes())
            kill.assert_called_once()

    def test_command_failure_is_saved_as_diagnostic_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(diagnostics.subprocess, 'Popen', return_value=FakeProcess(b'docker unavailable', 1)):
                path = diagnostics.capture(Path(temp), 'caddy', 200, 1024)
            self.assertIn(b'docker unavailable', path.read_bytes())
            self.assertIn(b'capture_exit_status=1', path.read_bytes())

    def test_invalid_container_name_cannot_escape_report_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                diagnostics.capture(Path(temp), '../escape', 200, 1024)
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_retention_caps_file_count_and_keeps_newest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(5):
                path = root / f'{index}-caddy-container.log'
                path.write_bytes(b'log')
                os.utime(path, (index + 1, index + 1))
            diagnostics.prune(root, 2, 100)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['3-caddy-container.log', '4-caddy-container.log'])

    def test_total_bytes_count_both_report_types_but_not_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / 'old-reboot-report.log'; old.write_bytes(b'x' * 80)
            os.utime(old, (1, 1))
            new = root / 'new-caddy-container.log'; new.write_bytes(b'y' * 60)
            unrelated = root / 'notes.txt'; unrelated.write_text('retain')
            link = root / 'link-container.log'; link.symlink_to(unrelated)
            diagnostics.prune(root, 40, 100)
            self.assertFalse(old.exists())
            self.assertTrue(new.exists())
            self.assertEqual(unrelated.read_text(), 'retain')
            self.assertTrue(link.is_symlink())


if __name__ == '__main__':
    unittest.main()
