import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('manage', Path(__file__).resolve().parents[1] / 'manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class SettingsTests(unittest.TestCase):
    def test_check_repeat_change_and_preserve_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'pihole.toml'
            original = '[dns]\nhosts = ["100.70.214.76 old.home.arpa"]\nupstreams = ["127.0.0.1#5335"]\n[webserver.api]\npwhash = "test-secret"\n'
            config.write_text(original)
            desired = {'dns.hosts': ['100.70.214.76 grafana.home.arpa']}
            with patch.object(manage.subprocess, 'run') as run:
                self.assertTrue(manage.configure(config, '/fake-ftl', desired, True)['changed'])
                run.assert_not_called()
                self.assertEqual(config.read_text(), original)
                def writer(argv, **kwargs):
                    self.assertEqual(argv[:3], ['/fake-ftl', '--config', 'dns.hosts'])
                    value = json.loads(argv[3])
                    lines = config.read_text().splitlines()
                    config.write_text('\n'.join('hosts = ' + json.dumps(value) if x.startswith('hosts = ') else x for x in lines) + '\n')
                run.side_effect = writer
                self.assertTrue(manage.configure(config, '/fake-ftl', desired, False)['changed'])
                self.assertFalse(manage.configure(config, '/fake-ftl', desired, False)['changed'])
                self.assertEqual(run.call_count, 1)
                desired['dns.hosts'] = ['100.70.214.77 grafana.home.arpa']
                self.assertTrue(manage.configure(config, '/fake-ftl', desired, False)['changed'])
                self.assertEqual(run.call_count, 2)
                self.assertIn('pwhash = "test-secret"', config.read_text())
                self.assertIn('upstreams = ["127.0.0.1#5335"]', config.read_text())

    def test_password_is_not_a_managed_key(self):
        with self.assertRaises(ValueError):
            manage.configure(Path('/unused'), '/fake', {'webserver.api.pwhash': 'oops'}, False)

    def test_cli_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'pihole.toml'
            config.write_text('[dns]\ninterface="eth0"\n')
            with patch.object(manage.subprocess, 'run', side_effect=manage.subprocess.CalledProcessError(1, 'fake')):
                with self.assertRaises(manage.subprocess.CalledProcessError):
                    manage.configure(config, '/fake', {'dns.interface': 'other'}, False)


class AdlistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'gravity.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE "group"(id INTEGER PRIMARY KEY,name TEXT);
                INSERT INTO "group" VALUES(0,'Default');
                CREATE TABLE adlist(id INTEGER PRIMARY KEY,address TEXT NOT NULL,enabled INTEGER,comment TEXT,type INTEGER DEFAULT 0,UNIQUE(address,type));
                CREATE TABLE adlist_by_group(adlist_id INTEGER,group_id INTEGER,PRIMARY KEY(adlist_id,group_id));
                CREATE TABLE gravity(domain TEXT,adlist_id INTEGER);
                INSERT INTO gravity VALUES('ads.example',1);
                CREATE TABLE domainlist(domain TEXT);
                INSERT INTO domainlist VALUES('keep.example');
                INSERT INTO adlist VALUES(1,'https://admin.example/list',1,'User list',0);
                INSERT INTO adlist_by_group VALUES(1,0);
            ''')

    def tearDown(self):
        self.temporary.cleanup()

    def test_insert_repeat_reenable_preserve_and_backup(self):
        urls = ["https://example.org/list'quoted"]
        before = self.path.read_bytes()
        self.assertTrue(manage.adlists(self.path, urls, True)['changed'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(Path(str(self.path) + '.homelab-before-adlists.bak').exists())
        self.assertTrue(manage.adlists(self.path, urls, False)['changed'])
        self.assertFalse(manage.adlists(self.path, urls, False)['changed'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM adlist').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT domain FROM domainlist').fetchone()[0], 'keep.example')
            db.execute('UPDATE adlist SET enabled=0 WHERE address=?', (urls[0],))
        self.assertTrue(manage.adlists(self.path, urls, False)['changed'])
        with sqlite3.connect(str(self.path) + '.homelab-before-adlists.bak') as backup:
            self.assertEqual(backup.execute('SELECT enabled FROM adlist WHERE address=?', (urls[0],)).fetchone()[0], 0)

    def test_empty_gravity_requests_refresh_without_rewriting_adlists(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM gravity')
        result = manage.adlists(self.path, ['https://admin.example/list'], True)
        self.assertFalse(result['changed'])
        self.assertTrue(result['gravity_required'])

    def test_invalid_url_and_missing_database_are_rejected(self):
        with self.assertRaises(ValueError):
            manage.adlists(self.path, ['file:///etc/passwd'], False)
        missing = self.path.with_name('missing.db')
        with self.assertRaises(sqlite3.OperationalError):
            manage.adlists(missing, ['https://example.org/list'], False)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
