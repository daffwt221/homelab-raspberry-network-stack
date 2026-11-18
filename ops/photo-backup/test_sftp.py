#!/usr/bin/env python3
"""Isolated Docker integration test. Requires root; never uses production paths.

All keys, uploads, containers and host ports are temporary test fixtures.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Run as root so fixture ownership can match UID/GID 10001')
    name = 'photo-sftp-test-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='photo-sftp-test-') as temp:
        base = Path(temp)
        uploads = base / 'uploads'; uploads.mkdir(mode=0o750)
        os.chown(uploads, 10001, 10001)
        hostkeys = base / 'hostkeys'; hostkeys.mkdir(mode=0o700)
        key = base / 'phone'
        bad_key = base / 'unknown'
        for path in (key, bad_key):
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path)])
        authorized = base / 'authorized_keys'
        authorized.write_text(key.with_suffix('.pub').read_text())
        authorized.chmod(0o644)
        original = base / 'original'; original.write_bytes(os.urandom(8193))
        known = base / 'known_hosts'

        def start():
            run(['docker', 'run', '-d', '--name', name, '--read-only', '--init',
                 '--cap-drop=ALL', '--cap-add=NET_BIND_SERVICE', '--cap-add=SETGID',
                 '--cap-add=SETUID', '--cap-add=SYS_CHROOT', '--security-opt=no-new-privileges:true',
                 '--memory=128m', '--memory-reservation=96m', '--pids-limit=32',
                 '--tmpfs=/run:rw,noexec,nosuid,size=8m', '--tmpfs=/tmp:rw,noexec,nosuid,size=4m',
                 '-p', '127.0.0.1::22',
                 '--mount', f'type=bind,src={uploads},dst=/srv/sftp/upload',
                 '--mount', f'type=bind,src={hostkeys},dst=/etc/ssh/hostkeys',
                 '--mount', f'type=bind,src={authorized},dst=/etc/ssh/photo-backup_authorized_keys,readonly',
                 args.image])
            port = run(['docker', 'port', name, '22/tcp']).stdout.strip().rsplit(':', 1)[1]
            pub = hostkeys / 'ssh_host_ed25519_key.pub'
            for _ in range(60):
                if pub.exists():
                    known.write_text('[127.0.0.1]:' + port + ' ' + pub.read_text())
                    options = ['-oBatchMode=yes', '-oIdentitiesOnly=yes', '-oStrictHostKeyChecking=yes',
                               '-oUserKnownHostsFile=' + str(known), '-oConnectTimeout=2']
                    check = subprocess.run(['sftp', *options, '-i', str(key), '-P', port,
                                            '-b', '-', 'photo-backup@127.0.0.1'],
                                           input='pwd\n', text=True, capture_output=True)
                    if check.returncode == 0:
                        return port, options
                time.sleep(1)
            raise RuntimeError('SFTP did not become usable: ' + run(['docker', 'logs', name]).stderr)

        def sftp(port, options, batch, identity=key):
            return subprocess.run(['sftp', *options, '-i', str(identity), '-P', port,
                                   '-b', '-', 'photo-backup@127.0.0.1'],
                                  input=batch, text=True, capture_output=True)

        def assert_ok(result):
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)

        try:
            port, options = start()
            assert_ok(sftp(port, options, f'put "{original}" /upload/probe.bin\n'))
            first_host_key = (hostkeys / 'ssh_host_ed25519_key.pub').read_bytes()
            # A rejected key must not authenticate.
            assert sftp(port, options, 'pwd\n', bad_key).returncode != 0
            # Chroot must prevent reads of the host/container passwd file.
            assert sftp(port, options, f'get /../../etc/passwd "{base / "escape"}"\n').returncode != 0
            # Even an authenticated exec is forced into internal-sftp.
            shell = subprocess.run(['ssh', *options, '-i', str(key), '-p', port,
                                    'photo-backup@127.0.0.1', 'id'],
                                   input='', text=True, capture_output=True, timeout=10)
            assert 'uid=' not in shell.stdout
            # Check effective server policy, not just a failed batch password prompt.
            policy = run(['docker', 'exec', name, '/usr/sbin/sshd', '-T',
                          '-C', 'user=photo-backup,host=localhost,addr=127.0.0.1']).stdout
            assert 'passwordauthentication no' in policy
            assert 'authenticationmethods publickey' in policy
            run(['docker', 'rm', '-f', name])
            port, options = start()
            assert (hostkeys / 'ssh_host_ed25519_key.pub').read_bytes() == first_host_key
            recovered = base / 'download'
            assert_ok(sftp(port, options, f'get /upload/probe.bin "{recovered}"\n'))
            assert hashlib.sha256(original.read_bytes()).digest() == hashlib.sha256(recovered.read_bytes()).digest()
            assert (uploads / 'probe.bin').stat().st_uid == 10001
            print('PASS: upload, SHA256, recreate persistence, persistent host key, key rejection, chroot, shell and password policy')
        except BaseException:
            subprocess.run(['docker', 'logs', name], check=False)
            raise
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, check=False)


if __name__ == '__main__':
    main()
