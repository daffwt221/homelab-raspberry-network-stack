#!/usr/bin/python3
"""Bounded container snapshots and retention of managed healthcheck reports."""
import argparse
import datetime
import fcntl
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import uuid


def managed_files(directory):
    return [p for p in directory.iterdir()
            if (p.name.endswith('-container.log') or p.name.endswith('-reboot-report.log'))
            and stat.S_ISREG(p.lstat().st_mode)]


def prune(directory, max_files, max_total_bytes):
    if max_files <= 0 or max_total_bytes <= 0:
        raise ValueError('Retention limits must be positive')
    files = sorted(managed_files(directory), key=lambda p: (p.stat().st_mtime_ns, p.name))
    size = sum(p.stat().st_size for p in files)
    remaining = len(files)
    for path in files:
        if remaining <= max_files and size <= max_total_bytes:
            break
        size -= path.stat().st_size
        path.unlink()
        remaining -= 1


def capture(directory, container, tail, max_bytes):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,254}', container):
        raise ValueError('Invalid Docker container name')
    if tail <= 0 or max_bytes < 1024:
        raise ValueError('Invalid container snapshot bounds')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    header = f'timestamp_utc={stamp}\ncontainer={container}\nlast_log_lines={tail}\n\n'.encode()
    budget = max_bytes - len(header) - 100
    process = subprocess.Popen(['timeout', '-k', '2', '8', 'docker', 'logs',
                                '--timestamps', '--tail', str(tail), container],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    try:
        data = process.stdout.read(budget)
        truncated = len(data) == budget
        if truncated:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.stdout.close()
        status = process.wait(timeout=12)
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
    footer = f'\n\ncapture_exit_status={status}\ntruncated={str(truncated).lower()}\n'.encode()
    fd, temporary = tempfile.mkstemp(prefix='.container-', dir=directory)
    destination = directory / f'{stamp}-{container}-{uuid.uuid4().hex[:8]}-container.log'
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(header + data + footer)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--max-files', type=int, default=40)
    parser.add_argument('--max-total-bytes', type=int, default=10 * 1024 * 1024)
    parser.add_argument('--container')
    parser.add_argument('--tail', type=int, default=200)
    parser.add_argument('--max-bytes', type=int, default=256 * 1024)
    args = parser.parse_args()
    if args.directory.is_symlink():
        raise ValueError('Report directory must not be a symlink')
    args.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.directory.stat().st_uid != os.geteuid():
        raise ValueError('Report directory belongs to another user')
    args.directory.chmod(0o700)
    fd = os.open(args.directory / '.diagnostics.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.container:
            print(capture(args.directory, args.container, args.tail, args.max_bytes))
        prune(args.directory, args.max_files, args.max_total_bytes)


if __name__ == '__main__':
    main()
