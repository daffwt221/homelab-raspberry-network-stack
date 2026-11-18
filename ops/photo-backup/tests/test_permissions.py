import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('permissions', Path(__file__).parents[1] / 'permissions.py')
permissions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(permissions)


@unittest.skipUnless(os.name == 'posix', 'POSIX metadata')
class PermissionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'inbox'
        self.root.mkdir()
        self.photo = self.root / 'photo.jpg'
        self.photo.write_bytes(b'original photo bytes')

    def test_migration_preserves_bytes_and_is_idempotent(self):
        changes = permissions.plan(self.root, os.getuid(), os.getgid())
        permissions.apply(changes, self.base / 'audit' / 'latest.json')
        self.assertEqual(self.photo.read_bytes(), b'original photo bytes')
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o2750)
        self.assertEqual(stat.S_IMODE(self.photo.stat().st_mode), 0o640)
        self.assertEqual(permissions.plan(self.root, os.getuid(), os.getgid()), [])
        manifests = list((self.base / 'audit').glob('permissions-*.json'))
        self.assertEqual(len(manifests), 1)
        self.assertTrue(json.loads(manifests[0].read_text()))
        self.assertEqual(stat.S_IMODE(manifests[0].stat().st_mode), 0o600)

    @unittest.skipUnless(os.geteuid() == 0, 'requires root for recovered numeric IDs')
    def test_recovered_group_collision_migrates_without_changing_uid(self):
        os.chown(self.root, 994, 989)
        os.chown(self.photo, 994, 989)
        changes = permissions.plan(self.root, 994, 10001)
        permissions.apply(changes, self.base / 'audit' / 'latest.json')
        self.assertEqual(self.photo.stat().st_uid, 994)
        self.assertEqual(self.photo.stat().st_gid, 10001)
        self.assertEqual(self.photo.read_bytes(), b'original photo bytes')

    def test_symlink_refused_before_changes(self):
        (self.root / 'link').symlink_to(self.base)
        before = self.photo.stat().st_mode
        with self.assertRaises(ValueError):
            permissions.plan(self.root, os.getuid(), os.getgid())
        self.assertEqual(self.photo.stat().st_mode, before)

    def test_unexpected_owner_refused(self):
        with self.assertRaises(ValueError):
            permissions.plan(self.root, os.getuid() + 1, os.getgid())

    def test_symlink_parent_refused(self):
        link = self.base / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            permissions.plan(link, os.getuid(), os.getgid())

    def test_archive_outside_inbox_untouched(self):
        archive = self.base / 'Takeout.jpg'
        archive.write_bytes(b'archive')
        archive.chmod(0o600)
        permissions.apply(permissions.plan(self.root, os.getuid(), os.getgid()), self.base / 'audit' / 'latest.json')
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertEqual(archive.read_bytes(), b'archive')

    def test_metadata_race_refused(self):
        changes = permissions.plan(self.root, os.getuid(), os.getgid())
        self.root.chmod(0o700)
        with self.assertRaises(ValueError):
            permissions.apply(changes, self.base / 'audit' / 'latest.json')


if __name__ == '__main__':
    unittest.main()
