#!/usr/bin/python3
"""Probe SSH and discovered online tailnet peers. Reboots are NEVER executed here.

Default is read-only. --recover permits one service restart per probe invocation.
Exit 0: verified; 10: confirmed failure; 20: inconclusive/manual intervention.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time


class LocalAPIUnavailable(RuntimeError):
    pass


def command(argv, timeout=10):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def status():
    try:
        result = command(['tailscale', 'status', '--json'])
    except subprocess.TimeoutExpired as error:
        raise LocalAPIUnavailable('tailscaled API timeout') from error
    if result.returncode:
        raise LocalAPIUnavailable('tailscaled API unavailable')
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get('BackendState'), str):
        raise ValueError('Invalid Tailscale status JSON')
    return data


def online_addresses(data):
    peers = data.get('Peer') or {}
    if not isinstance(peers, dict):
        raise ValueError('Invalid peer map')
    own = set((data.get('Self') or {}).get('TailscaleIPs') or [])
    addresses = []
    for peer in peers.values():
        if not isinstance(peer, dict) or peer.get('Online') is not True:
            continue
        candidates = [ipaddress.ip_address(value) for value in peer.get('TailscaleIPs', []) if value not in own]
        candidates.sort(key=lambda address: address.version)
        if candidates:
            address = str(candidates[0])
            if address not in addresses:
                addresses.append(address)
    return addresses


def ping(address, timeout=4):
    try:
        result = command(['tailscale', 'ping', '--tsmp', '--c=1', '--timeout=3s',
                          '--until-direct=false', address], timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def interface_ready(data, interface='tailscale0'):
    result = command(['ip', '-j', 'addr', 'show', 'dev', interface])
    if result.returncode:
        return False
    links = json.loads(result.stdout)
    if not isinstance(links, list):
        raise ValueError('Invalid interface status JSON')
    expected = sorted((ipaddress.ip_address(value) for value in
                       (data.get('Self') or {}).get('TailscaleIPs', [])), key=lambda address: address.version)
    if not expected:
        return False
    primary = str(expected[0])
    return any(isinstance(link, dict) and 'UP' in link.get('flags', []) and
               primary in {address.get('local') for address in link.get('addr_info', [])}
               for link in links)



def route_ready(address, interface='tailscale0'):
    family = '-4' if ipaddress.ip_address(address).version == 4 else '-6'
    result = command(['ip', '-j', family, 'route', 'get', address])
    if result.returncode:
        return False
    routes = json.loads(result.stdout)
    if not isinstance(routes, list):
        raise ValueError('Invalid route status JSON')
    return any(isinstance(route, dict) and route.get('dev') == interface and
               route.get('type', 'unicast') == 'unicast' for route in routes)


def forwarding_status():
    result = {}
    for family, name in [('ipv4', '/proc/sys/net/ipv4/ip_forward'),
                         ('ipv6', '/proc/sys/net/ipv6/conf/all/forwarding')]:
        try:
            value = Path(name).read_text().strip()
            result[family] = value == '1' if value in ('0', '1') else None
        except OSError:
            result[family] = None
    return result


def network_diagnostics():
    try:
        result = command(['tailscale', 'netcheck', '--format=json'], timeout=10)
        if result.returncode:
            return {'available': False, 'reason': 'netcheck-failed'}
        data = json.loads(result.stdout)
        latencies = data.get('RegionLatency') or data.get('DERPLatency') or {}
        return {'available': True, 'udp': data.get('UDP'), 'ipv4': data.get('IPv4'),
                'ipv6': data.get('IPv6'), 'captive_portal': data.get('CaptivePortal'),
                'reachable_relay_regions': len(latencies)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'available': False, 'reason': 'netcheck-unavailable'}


def tailnet_path(data, budget=16, interface='tailscale0'):
    backend = data['BackendState']
    if backend != 'Running':
        return {'status': 'unknown', 'reason': 'backend-' + backend, 'peers': []}
    if not interface_ready(data, interface):
        return {'status': 'critical', 'reason': 'tailscale-interface-down-or-unaddressed'}
    peers = online_addresses(data)
    if not peers:
        return {'status': 'unknown', 'reason': 'no-online-peers', 'peers': []}
    deadline = time.monotonic() + budget
    failed = []
    route_failures = []
    for address in peers:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            return {'status': 'unknown', 'reason': 'peer-probe-budget-exhausted',
                    'peers': failed, 'route_failures': route_failures}
        if not route_ready(address, interface):
            route_failures.append(address)
            continue
        if ping(address, min(4, remaining)):
            return {'status': 'degraded' if route_failures else 'healthy',
                    'reason': 'online-peer-responded', 'peer': address,
                    'failed_peers': failed, 'route_failures': route_failures}
        failed.append(address)
    return {'status': 'critical',
            'reason': 'kernel-routes-unavailable' if route_failures else 'all-online-peers-unreachable',
            'peers': failed, 'route_failures': route_failures}


def probe_tailscale(interface):
    data = status()
    unit_active = command(['systemctl', 'is-active', '--quiet', 'tailscaled.service']).returncode == 0
    if data.get('TUN') is False:
        result = {'status': 'unknown', 'reason': 'userspace-mode-requires-manual-configuration'}
    else:
        result = tailnet_path(data, interface=interface)
    warnings = data.get('Health') or []
    if not isinstance(warnings, list) or any(not isinstance(warning, str) for warning in warnings):
        raise ValueError('Invalid Tailscale Health messages')
    result.update(backend_state=data['BackendState'], systemd_active=unit_active,
                  self_online=(data.get('Self') or {}).get('Online'),
                  health_warnings=warnings, forwarding=forwarding_status())
    # Peer reachability does not imply healthy control/configuration. Health
    # messages are human-readable: preserve them, never regex-match for reboot.
    if result['status'] == 'healthy' and (warnings or not unit_active or result['self_online'] is False):
        result.update(status='degraded', reason='reachable-with-health-or-control-warnings')
    return result


def restart(unit):
    try:
        return command(['systemctl', 'restart', unit], timeout=20).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def check_tailscale(recover=False, interface='tailscale0'):
    try:
        first = probe_tailscale(interface)
    except LocalAPIUnavailable as error:
        first = {'status': 'critical', 'reason': str(error)}
    if first['status'] != 'critical':
        return first
    # Repeat discovery as well as pings before treating a transient as a failure.
    time.sleep(1)
    try:
        repeated = probe_tailscale(interface)
    except LocalAPIUnavailable as error:
        repeated = {'status': 'critical', 'reason': str(error)}
    if repeated['status'] != 'critical':
        repeated['initial_failure'] = first
        return repeated
    diagnostics = network_diagnostics()
    repeated['network_diagnostics'] = diagnostics
    if not recover:
        return repeated
    restarted = restart('tailscaled.service')
    time.sleep(2)
    try:
        final = probe_tailscale(interface)
    except LocalAPIUnavailable as error:
        final = {'status': 'critical', 'reason': str(error)}
    final.update(restart_attempted=True, restart_succeeded=restarted, initial_failure=first,
                 network_diagnostics_before_recovery=diagnostics)
    return final


def ssh_banner(host, port):
    try:
        with socket.create_connection((host, port), timeout=3) as connection:
            connection.settimeout(3)
            banner = b''
            while b'\n' not in banner and len(banner) < 255:
                chunk = connection.recv(255 - len(banner))
                if not chunk:
                    break
                banner += chunk
            return banner.startswith((b'SSH-2.0-', b'SSH-1.99-'))
    except OSError:
        return False


def ssh_ready(host, port):
    return command(['systemctl', 'is-active', '--quiet', 'ssh.service']).returncode == 0 and ssh_banner(host, port)


def check_ssh(host='127.0.0.1', port=22, recover=False):
    if ssh_ready(host, port):
        return {'status': 'healthy', 'reason': 'ssh-service-and-banner-ok'}
    if command(['/usr/sbin/sshd', '-t']).returncode:
        return {'status': 'unknown', 'reason': 'invalid-sshd-config-or-hostkeys-manual-intervention'}
    if not recover:
        return {'status': 'critical', 'reason': 'ssh-service-or-banner-unavailable'}
    restarted = restart('ssh.service')
    for attempt in range(3):
        if ssh_ready(host, port):
            return {'status': 'healthy', 'reason': 'ssh-recovered', 'restart_attempted': True}
        if attempt < 2:
            time.sleep(1)
    return {'status': 'critical', 'reason': 'ssh-still-unavailable',
            'restart_attempted': True, 'restart_succeeded': restarted}


def inspect(recover=False, ssh_host='127.0.0.1', ssh_port=22, interface='tailscale0'):
    # Neither component failure may hide the other component's diagnostics.
    results = {}
    for name, probe in [('ssh', lambda: check_ssh(ssh_host, ssh_port, recover)),
                        ('tailscale', lambda: check_tailscale(recover, interface))]:
        try:
            results[name] = probe()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            results[name] = {'status': 'unknown', 'reason': type(error).__name__ + ': ' + str(error)}
    values = {entry['status'] for entry in results.values()}
    overall = ('critical' if 'critical' in values else 'unknown' if 'unknown' in values
               else 'degraded' if 'degraded' in values else 'healthy')
    return {'status': overall, 'checks': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recover', action='store_true')
    parser.add_argument('--ssh-host', default='127.0.0.1')
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--interface', default='tailscale0')
    args = parser.parse_args()
    ipaddress.ip_address(args.ssh_host)  # A literal avoids depending on Pi-hole.
    if not 1 <= args.ssh_port <= 65535:
        parser.error('Invalid SSH port')
    result = inspect(args.recover, args.ssh_host, args.ssh_port, args.interface)
    print(json.dumps(result, sort_keys=True))
    return {'healthy': 0, 'critical': 10, 'unknown': 20, 'degraded': 20}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
