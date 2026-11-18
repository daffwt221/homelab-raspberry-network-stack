#!/usr/bin/python3
"""Probe host DNS and storage. Only the DNS probe has optional service recovery."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
import uuid

DNS_UNITS = {'pihole': 'pihole-FTL.service', 'unbound': 'unbound.service'}
STORAGE_ERRORS = re.compile(
    r'\b(?:EXT4-fs error|EXT4-fs (?:warning.*aborted journal|.*remounting filesystem read-only)'
    r'|Buffer I/O error|(?:blk_update_request|blk_print_req_error): I/O error'
    r'|I/O error, dev |Aborting journal)', re.I
)
USB_RESET = re.compile(r'^\[\s*(\d+(?:\.\d+)?)\]\s+(?:.*?\bkernel:\s+)?usb ([\d.-]+): reset (?:[\w-]+ )?USB device', re.I)


def command(*argv, timeout=8):
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout)


def result(state, reason, **details):
    return dict(status=state, reason=reason, **details)


def query(port, name):
    try:
        process = command('dig', '+time=2', '+tries=1', '+noall', '+comments', '+answer',
                          '@127.0.0.1', '-p', str(port), name, 'A', timeout=4)
    except subprocess.TimeoutExpired:
        return {'rcode': None, 'addresses': []}
    header = re.search(r'^;; ->>HEADER<<- .*?\bstatus: ([A-Z]+),', process.stdout, re.M)
    addresses = []
    for line in process.stdout.splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[2:4] == ['IN', 'A'] and not line.startswith(';'):
            addresses.append(str(ipaddress.IPv4Address(fields[-1])))
    return {'rcode': header[1] if process.returncode == 0 and header else None, 'addresses': addresses}


def public_dns(public_names, port, unbound_port):
    # These names diagnose the upstream path. Filtering, caches and an Internet
    # outage can affect the results even when both local services are working.
    pihole = {name: query(port, name) for name in public_names}
    missing = [name for name, answer in pihole.items() if not has_public_answer(answer)]
    if len(missing) != len(public_names):
        return result('healthy', 'local-and-public-dns-responding', public=pihole)
    upstream = {name: query(unbound_port, name) for name in missing}
    if any(map(has_public_answer, upstream.values())):
        return result('degraded', 'public-dns-fails-only-through-pihole', public=pihole, unbound_public=upstream)
    reachable = internet_endpoint()
    reason = 'recursive-dns-failed-with-tcp-egress' if reachable else 'recursive-dns-and-tcp-egress-unverified'
    return result('degraded', reason, public=pihole, unbound_public=upstream, tcp_egress=reachable)


def internet_endpoint():
    # Only connectivity to a literal IP. No DNS query, TLS or application data.
    for address in ('1.1.1.1', '9.9.9.9'):
        try:
            with socket.create_connection((address, 443), timeout=2):
                return address
        except OSError:
            continue
    return None


def has_public_answer(answer):
    return answer['rcode'] == 'NOERROR' and any(
        ipaddress.IPv4Address(address).is_global for address in answer['addresses']
    )


def dns_local(name, expected, port, unbound_port):
    observations = {'pihole': query(port, name), 'unbound': query(unbound_port, 'localhost.')}
    expected_addresses = {'pihole': expected, 'unbound': '127.0.0.1'}
    unavailable, mismatched = [], []
    for daemon, answer in observations.items():
        if answer['rcode'] in (None, 'SERVFAIL'):
            unavailable.append(daemon)
        elif answer['rcode'] != 'NOERROR' or set(answer['addresses']) != {expected_addresses[daemon]}:
            mismatched.append(daemon)
    return observations, unavailable, mismatched


def restart_dns(daemon):
    unit = DNS_UNITS[daemon]
    try:
        mode = command('systemctl', 'show', '-p', 'LoadState', '--value', unit)
        if mode.returncode or mode.stdout.strip() != 'loaded':
            return 'unit-not-loaded'
        if daemon == 'unbound' and command('unbound-checkconf').returncode:
            return 'invalid-unbound-config'
        return 'restarted' if command('systemctl', 'restart', unit, timeout=20).returncode == 0 else 'restart-failed'
    except (OSError, subprocess.TimeoutExpired):
        return 'restart-unavailable'


def check_dns(name, expected, port, unbound_port, recover=False):
    local, unavailable, mismatched = dns_local(name, expected, port, unbound_port)
    attempts = {}
    if recover and unavailable:
        # Restore the upstream first when both daemons have a local fault.
        attempts = {daemon: restart_dns(daemon) for daemon in reversed(unavailable)}
        time.sleep(2)
        local, unavailable, mismatched = dns_local(name, expected, port, unbound_port)
    if unavailable:
        eligible = [daemon for daemon in unavailable
                    if attempts.get(daemon) in ('restarted', 'restart-failed')]
        status = 'critical' if eligible else 'degraded'
        return result(status, 'local-resolver-unavailable', local=local, restart=attempts,
                      unavailable=unavailable, mismatched=mismatched, reboot_eligible=eligible)
    if mismatched:
        return result('degraded', 'local-dns-configuration-mismatch', local=local, restart=attempts,
                      mismatched=mismatched)
    response = public_dns(('example.com.', 'iana.org.'), port, unbound_port)
    return dict(response, local=local, restart=attempts)


def exact_mount(target):
    probe = command('findmnt', '--json', '--mountpoint', target, '--output', 'SOURCE,UUID,FSTYPE,OPTIONS')
    if probe.returncode == 1 and not probe.stderr.strip():
        return None
    probe.check_returncode()
    mounts = json.loads(probe.stdout)['filesystems']
    if len(mounts) != 1:
        raise ValueError(f'Ambiguous mount at {target}')
    return mounts[0]


def block_usb_port(device):
    try:
        path = (Path('/sys/class/block') / Path(device).name).resolve(strict=True)
    except OSError:
        return None
    for parent in path.parents:
        if re.fullmatch(r'\d+-\d+(?:\.\d+)*', parent.name):
            return parent.name
    return None


def kernel_messages():
    probe = command('journalctl', '-k', '-b', '--no-pager', '-o', 'short-monotonic', timeout=6)
    if probe.returncode or probe.stderr.strip() or not probe.stdout.strip() or '-- No entries --' in probe.stdout:
        return None
    return probe.stdout.splitlines()


def recent_usb_reset(messages, port, uptime):
    if not port:
        return False
    for line in messages:
        match = USB_RESET.match(line)
        if match and match[2] == port and 0 <= uptime - float(match[1]) <= 300:
            return True
    return False


def boot_mount_failed(target, expected_uuid):
    entry = command('findmnt', '--fstab', '-n', '-M', target, '-o', 'SOURCE,FSTYPE')
    if entry.returncode or entry.stdout.split() != [f'UUID={expected_uuid}', 'ext4']:
        return False
    unit = command('systemd-escape', '--path', '--suffix=mount', target)
    if unit.returncode:
        return False
    state = command('systemctl', 'show', '-p', 'ActiveState', '--value', unit.stdout.strip())
    return state.returncode == 0 and state.stdout.strip() == 'failed'


def smart_counter(source):
    try:
        probe = command('smartctl', '-j', '-a', '-T', 'permissive', source, timeout=8)
        data = json.loads(probe.stdout)
        count = data.get('nvme_smart_health_information_log', {}).get('unsafe_shutdowns')
        if type(count) is int and count >= 0:
            return count
        # Some bridges expose the counter only in the text rendering.
        probe = command('smartctl', '-a', '-T', 'permissive', source, timeout=8)
        line = re.search(r'^Unsafe Shutdowns:\s*([\d,]+)\s*$', probe.stdout, re.M)
        return int(line[1].replace(',', '')) if line else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def read_baseline(path, expected_uuid):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) and data.get('uuid') == expected_uuid else {}
    except (OSError, ValueError):
        return {}


def store_baseline(path, data):
    # The coordinator holds its global flock and Ansible creates the private state directory.
    with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def check_storage(target, expected_uuid, baseline, monitor=False):
    root = exact_mount('/')
    if not root or 'rw' not in root['options'].split(','):
        return result('hold', 'root-filesystem-not-read-write')
    messages = kernel_messages()
    if messages is not None:
        errors = [line for line in messages if STORAGE_ERRORS.search(line)]
        if errors:
            return result('hold', 'kernel-storage-errors-this-boot', evidence=errors[-5:])
    mounted = exact_mount(target)
    previous = read_baseline(baseline, expected_uuid)
    if mounted is None:
        device = Path('/dev/disk/by-uuid') / expected_uuid
        try:
            detected = stat.S_ISBLK(device.stat().st_mode)
        except FileNotFoundError:
            detected = False
        reason = 'nvme-detected-but-unmounted' if detected else 'nvme-not-detected'
        port = previous.get('usb_port')
        uptime = float(Path('/proc/uptime').read_text().split()[0])
        # A known USB port, a fresh reset, a failed automatic mount and a clean
        # kernel journal are all required. This is evidence for one retry at boot.
        if (messages is not None and recent_usb_reset(messages, port, uptime)
                and boot_mount_failed(target, expected_uuid)):
            return result('critical', 'nvme-mount-failed-after-usb-reset', device_detected=detected, usb_port=port)
        return result('hold', reason, usb_port=port)
    if mounted.get('uuid') != expected_uuid or mounted.get('fstype') != 'ext4':
        return result('hold', 'unexpected-filesystem-at-nvme-mount', mount=mounted)
    if 'rw' not in mounted['options'].split(','):
        return result('hold', 'nvme-mounted-read-only', mount=mounted)
    source = mounted['source']
    count = smart_counter(source)
    warnings = []
    if messages is None:
        warnings.append('kernel-storage-log-unavailable')
    if count is None:
        warnings.append('smart-unsafe-shutdown-counter-unavailable')
    old_count = previous.get('unsafe_shutdowns')
    if count is not None and type(old_count) is int and count > old_count:
        warnings.append('unsafe-shutdown-counter-increased')
    if monitor:
        port = block_usb_port(source)
        observed = dict(uuid=expected_uuid, usb_port=port, unsafe_shutdowns=count if count is not None else old_count)
        if observed != previous:
            try:
                store_baseline(baseline, observed)
            except OSError:
                warnings.append('nvme-baseline-write-unavailable')
    return result('healthy', 'expected-nvme-mounted-read-write', mount=mounted, warnings=warnings,
                  unsafe_shutdowns=count, previous_unsafe_shutdowns=old_count)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='probe', required=True)
    dns = subparsers.add_parser('dns')
    dns.add_argument('--name', required=True)
    dns.add_argument('--address', required=True, type=ipaddress.IPv4Address)
    dns.add_argument('--port', type=int, default=53)
    dns.add_argument('--unbound-port', type=int, default=5335)
    dns.add_argument('--recover', action='store_true')
    storage = subparsers.add_parser('storage')
    storage.add_argument('--uuid', required=True, type=uuid.UUID)
    storage.add_argument('--mount', default='/mnt/nvme')
    storage.add_argument('--baseline', type=Path,
                         default=Path('/var/lib/homelab-healthcheck/nvme-observation.json'))
    storage.add_argument('--monitor', action='store_true')
    args = parser.parse_args()
    try:
        if args.probe == 'dns':
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*', args.name) or not all(
                    1 <= port <= 65535 for port in (args.port, args.unbound_port)):
                raise ValueError('Invalid local DNS probe settings')
            observation = check_dns(args.name.rstrip('.') + '.', str(args.address), args.port,
                                    args.unbound_port, args.recover)
        else:
            observation = check_storage(args.mount, str(args.uuid), args.baseline, args.monitor)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        observation = result('hold' if args.probe == 'storage' else 'degraded',
                             'probe-unavailable', error=str(error))
    print(json.dumps(observation, sort_keys=True))
    return {'healthy': 0, 'critical': 10, 'degraded': 20, 'hold': 20}[observation['status']]


if __name__ == '__main__':
    raise SystemExit(main())
