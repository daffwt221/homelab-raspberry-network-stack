import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('secondary', Path(__file__).parents[1] / 'secondary.py')
secondary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secondary)


class SecondaryTests(unittest.TestCase):
    def test_missing_mount_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaises(ValueError):
                secondary.validate(root, root, root / 'copy')

    def test_same_filesystem_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / 'source'
            mount = root / 'disk'
            source.mkdir(); mount.mkdir()
            with patch.object(secondary.os.path, 'ismount', return_value=True):
                with self.assertRaisesRegex(ValueError, 'different filesystem'):
                    secondary.validate(source, mount, mount / 'copy')

    def test_destination_outside_mount_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with patch.object(secondary.os.path, 'ismount', return_value=True):
                with self.assertRaisesRegex(ValueError, 'strictly inside'):
                    secondary.validate(root, root, root.parent / 'copy')

    def test_copy_keeps_deleted_and_replaced_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / 'source'; source.mkdir()
            destination = root / 'copy'
            config = root / 'config.json'
            config.write_text(json.dumps({'source': str(source), 'mount': str(root), 'destination': str(destination)}))
            with patch.object(secondary, 'validate', return_value=(source, destination)), \
                    patch.object(secondary.subprocess, 'run') as run, \
                    patch.object(sys, 'argv', ['secondary.py', str(config)]):
                secondary.main()
            command = run.call_args.args[0]
            self.assertNotIn('--delete', command)
            self.assertIn('--checksum', command)
            self.assertIn('--backup', command)
            self.assertTrue(any(arg.startswith('--backup-dir=') for arg in command))
            self.assertTrue((destination / 'last-success.json').exists())

    def test_failed_copy_does_not_record_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            destination = root / 'copy'
            config = root / 'config.json'
            config.write_text(json.dumps({'source': str(root), 'mount': str(root), 'destination': str(destination)}))
            with patch.object(secondary, 'validate', return_value=(root, destination)), \
                    patch.object(secondary.subprocess, 'run', side_effect=RuntimeError('copy failed')), \
                    patch.object(sys, 'argv', ['secondary.py', str(config)]):
                with self.assertRaises(RuntimeError):
                    secondary.main()
            self.assertFalse((destination / 'last-success.json').exists())


if __name__ == '__main__':
    unittest.main()
