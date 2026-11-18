#!/usr/bin/env python3
"""Copy uploads to a separate mounted filesystem, keeping overwritten versions."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys


def validate(source, mount, destination):
    source, mount, destination = map(Path, (source, mount, destination))
    for path in (source, mount, destination):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError("Backup paths must be absolute and contain no symlinks")
    if not source.is_dir() or not os.path.ismount(mount):
        raise ValueError("Source is missing or backup filesystem is not mounted")
    if mount == destination or mount not in destination.parents:
        raise ValueError("Destination must be strictly inside the backup mount")
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Source and destination must not overlap")
    if source.stat().st_dev == mount.stat().st_dev:
        raise ValueError("A second copy requires a different filesystem")
    # A nested mount at the destination must not bypass the device check.
    ancestor = destination
    while not ancestor.exists():
        ancestor = ancestor.parent
    if ancestor.stat().st_dev != mount.stat().st_dev:
        raise ValueError("Destination is not on the configured backup filesystem")
    return source, destination


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    source, destination = validate(config['source'], config['mount'], config['destination'])
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    versions = destination / 'versions' / stamp
    # No --delete: files removed on the phone are retained here. Replacements
    # are preserved in versions/. Checksums detect same-size, same-time changes.
    subprocess.run(['rsync', '-rt', '--checksum', '--no-links', '--backup',
                    '--backup-dir=' + str(versions), '--chmod=D700,F600',
                    '--', str(source) + '/', str(destination / 'current') + '/'], check=True)
    (destination / 'last-success.json').write_text(json.dumps({'completed_utc': stamp}))


if __name__ == '__main__':
    main()
