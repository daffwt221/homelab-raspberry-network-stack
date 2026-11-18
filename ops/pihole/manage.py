#!/usr/bin/env python3
"""Converge owned Pi-hole settings or required adlists; never replace credentials/DB."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import tomllib
from urllib.parse import urlsplit


def configure(path, binary, desired, check):
    allowed = {'dns.hosts', 'dns.upstreams', 'dns.interface', 'dns.listeningMode',
               'webserver.port', 'webserver.headers', 'webserver.interface.theme'}
    if set(desired) - allowed:
        raise ValueError('Unsupported managed configuration key')
    with path.open('rb') as stream:
        current = tomllib.load(stream)
    changed = []
    for key, value in desired.items():
        observed = current
        for segment in key.split('.'):
            observed = observed.get(segment, {}) if isinstance(observed, dict) else None
        same = sorted(observed) == sorted(value) if key == 'dns.hosts' and isinstance(observed, list) else observed == value
        if same:
            continue
        changed.append(key)
        if not check:
            encoded = json.dumps(value) if isinstance(value, (list, dict, bool)) else str(value)
            subprocess.run([binary, '--config', key, encoded], check=True, capture_output=True, text=True)
    return {'changed': bool(changed), 'keys': changed}


def backup_database(connection, path):
    """SQLite's backup API includes committed WAL content, unlike copying a DB file."""
    fd, temporary = tempfile.mkstemp(prefix='.homelab-gravity-', dir=path.parent)
    os.close(fd)
    try:
        with sqlite3.connect(temporary) as destination:
            connection.backup(destination)
        os.chmod(temporary, 0o600)
        os.replace(temporary, str(path) + '.homelab-before-adlists.bak')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def adlists(path, desired, check):
    if not isinstance(desired, list) or len(set(desired)) != len(desired):
        raise ValueError('Adlists must be a list of unique URLs')
    for address in desired:
        url = urlsplit(address)
        if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.password:
            raise ValueError('Adlist URL must use HTTP(S) without embedded credentials')
    # mode=rw refuses to create an empty database if installation did not finish.
    mode = 'ro' if check else 'rw'
    with sqlite3.connect(path.resolve().as_uri() + '?mode=' + mode, uri=True, timeout=30) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(adlist)')}
        if not {'id', 'address', 'enabled', 'comment'} <= columns:
            raise ValueError('Unsupported Pi-hole adlist schema')
        if db.execute('SELECT 1 FROM "group" WHERE id=0').fetchone() is None:
            raise ValueError('The default Pi-hole group is missing')
        has_type = 'type' in columns
        scope = ' AND type=0' if has_type else ''
        changes = []
        for address in desired:
            row = db.execute('SELECT id,enabled FROM adlist WHERE address=?' + scope, (address,)).fetchone()
            if row is None or row[1] != 1:
                changes.append(address)
            elif db.execute('SELECT 1 FROM adlist_by_group WHERE adlist_id=? AND group_id=0', (row[0],)).fetchone() is None:
                changes.append(address)
        # Rebuild an empty gravity table even when the desired URL was already registered.
        empty_gravity = bool(desired) and db.execute('SELECT 1 FROM gravity LIMIT 1').fetchone() is None
        if changes and not check:
            backup_database(db, path)
            with db:
                for address in changes:
                    row = db.execute('SELECT id FROM adlist WHERE address=?' + scope, (address,)).fetchone()
                    if row is None:
                        fields = 'address,enabled,comment' + (',type' if has_type else '')
                        values = '?,?,?' + (',?' if has_type else '')
                        args = (address, 1, 'Managed by homelab Ansible') + ((0,) if has_type else ())
                        row_id = db.execute('INSERT INTO adlist (' + fields + ') VALUES (' + values + ')', args).lastrowid
                    else:
                        row_id = row[0]
                        db.execute('UPDATE adlist SET enabled=1 WHERE id=?', (row_id,))
                    db.execute('INSERT OR IGNORE INTO adlist_by_group(adlist_id,group_id) VALUES (?,0)', (row_id,))
        return {'changed': bool(changes), 'addresses': changes, 'gravity_required': bool(changes) or empty_gravity}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('settings', 'adlists'))
    parser.add_argument('--path', type=Path, required=True)
    parser.add_argument('--binary', default='/usr/bin/pihole-FTL')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    import sys
    desired = json.load(sys.stdin)
    result = configure(args.path, args.binary, desired, args.check) if args.operation == 'settings' else adlists(args.path, desired, args.check)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
