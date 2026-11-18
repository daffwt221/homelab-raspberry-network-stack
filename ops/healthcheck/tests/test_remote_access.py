import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('access', Path(__file__).parents[1] / 'remote_access.py')
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)


def snapshot(backend='Running', peers=None):
    return {'BackendState': backend, 'Self': {'TailscaleIPs': ['100.64.0.1']},
            'Peer': peers or {}}


def peer(address, online=True):
    return {'Online': online, 'TailscaleIPs': [address]}


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        patch.object(access, 'route_ready', return_value=True).start()
        self.addCleanup(patch.stopall)

    def test_online_peers_discovered_without_hostnames_or_dns(self):
        data = snapshot(peers={'a': peer('100.64.0.2'), 'b': peer('100.64.0.3', False),
                               'self': peer('100.64.0.1'), 'duplicate': peer('100.64.0.2')})
        self.assertEqual(access.online_addresses(data), ['100.64.0.2'])

    def test_ipv4_preferred_but_ipv6_supported(self):
        data = snapshot(peers={'a': {'Online': True, 'TailscaleIPs': ['fd7a:115c:a1e0::2', '100.64.0.2']},
                               'b': peer('fd7a:115c:a1e0::3')})
        self.assertEqual(access.online_addresses(data), ['100.64.0.2', 'fd7a:115c:a1e0::3'])

    def test_missing_peers_is_inconclusive_without_pinging(self):
        with patch.object(access, 'interface_ready', return_value=True), patch.object(access, 'ping') as ping:
            result = access.tailnet_path(snapshot())
        self.assertEqual(result['reason'], 'no-online-peers')
        self.assertEqual(result['status'], 'unknown')
        ping.assert_not_called()

    def test_one_online_peer_responding_proves_connectivity(self):
        data = snapshot(peers={'a': peer('100.64.0.2'), 'b': peer('100.64.0.3')})
        with patch.object(access, 'interface_ready', return_value=True), \
                patch.object(access, 'ping', side_effect=[False, True]) as ping:
            result = access.tailnet_path(data)
        self.assertEqual(result['status'], 'healthy')
        self.assertEqual(result['peer'], '100.64.0.3')
        self.assertEqual(result['failed_peers'], ['100.64.0.2'])
        self.assertEqual(ping.call_count, 2)

    def test_all_online_peers_failing_is_critical(self):
        data = snapshot(peers={'a': peer('100.64.0.2'), 'b': peer('100.64.0.3')})
        with patch.object(access, 'interface_ready', return_value=True), patch.object(access, 'ping', return_value=False):
            result = access.tailnet_path(data)
        self.assertEqual(result['status'], 'critical')
        self.assertEqual(result['reason'], 'all-online-peers-unreachable')

    def test_incomplete_probe_budget_is_not_claimed_as_all_peers_failing(self):
        data = snapshot(peers={'a': peer('100.64.0.2'), 'b': peer('100.64.0.3')})
        with patch.object(access, 'interface_ready', return_value=True), \
                patch.object(access.time, 'monotonic', side_effect=[0, 20]), patch.object(access, 'ping') as ping:
            result = access.tailnet_path(data)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'peer-probe-budget-exhausted')
        ping.assert_not_called()

    def test_tun_interface_down_is_critical_even_if_daemon_is_running(self):
        with patch.object(access, 'interface_ready', return_value=False):
            result = access.tailnet_path(snapshot(peers={'a': peer('100.64.0.2')}))
        self.assertEqual(result['status'], 'critical')
        self.assertEqual(result['reason'], 'tailscale-interface-down-or-unaddressed')

    def test_interface_requires_up_flag_and_a_local_tailscale_address(self):
        link = [{'flags': ['UP'], 'addr_info': [{'local': '100.64.0.1'}]}]
        with patch.object(access, 'command', return_value=Mock(returncode=0, stdout=json.dumps(link))):
            self.assertTrue(access.interface_ready(snapshot()))
        link[0]['flags'] = []
        with patch.object(access, 'command', return_value=Mock(returncode=0, stdout=json.dumps(link))):
            self.assertFalse(access.interface_ready(snapshot()))
        link[0]['flags'] = ['UP']; link[0]['addr_info'][0]['local'] = '192.0.2.1'
        with patch.object(access, 'command', return_value=Mock(returncode=0, stdout=json.dumps(link))):
            self.assertFalse(access.interface_ready(snapshot()))

    def test_ipv6_address_does_not_mask_missing_primary_ipv4_on_interface(self):
        data = snapshot(); data['Self']['TailscaleIPs'].append('fd7a:115c:a1e0::1')
        links = [{'flags': ['UP'], 'addr_info': [{'local': 'fd7a:115c:a1e0::1'}]}]
        with patch.object(access, 'command', return_value=Mock(returncode=0, stdout=json.dumps(links))):
            self.assertFalse(access.interface_ready(data))

    def test_tsmp_probe_accepts_relay_and_uses_literals(self):
        with patch.object(access, 'command', return_value=Mock(returncode=0)) as command:
            self.assertTrue(access.ping('100.64.0.2'))
        self.assertIn('--tsmp', command.call_args.args[0])
        self.assertIn('--until-direct=false', command.call_args.args[0])
        self.assertEqual(command.call_args.args[0][-1], '100.64.0.2')

    def test_ping_timeout_is_a_failed_probe(self):
        with patch.object(access, 'command', side_effect=subprocess.TimeoutExpired('tailscale', 4)):
            self.assertFalse(access.ping('100.64.0.2'))


class TailnetRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(access.time, 'sleep').start()
        patch.object(access, 'command', return_value=Mock(returncode=0)).start()
        patch.object(access, 'forwarding_status', return_value={'ipv4': False, 'ipv6': False}).start()
        patch.object(access, 'network_diagnostics', return_value={'available': True}).start()
        self.addCleanup(patch.stopall)

    def test_healthy_tailnet_never_restarts_service(self):
        with patch.object(access, 'status', return_value=snapshot()), \
                patch.object(access, 'tailnet_path', return_value={'status': 'healthy'}), \
                patch.object(access, 'restart') as restart:
            result = access.check_tailscale(True)
        restart.assert_not_called()
        self.assertEqual(result['status'], 'healthy')

    def test_transient_failure_is_retried_with_fresh_discovery_before_restart(self):
        with patch.object(access, 'status', side_effect=[snapshot(), snapshot()]) as status, \
                patch.object(access, 'tailnet_path', side_effect=[{'status': 'critical'}, {'status': 'healthy'}]), \
                patch.object(access, 'restart') as restart:
            result = access.check_tailscale(True)
        restart.assert_not_called()
        self.assertEqual(status.call_count, 2)
        self.assertEqual(result['status'], 'healthy')

    def test_confirmed_failure_gets_one_service_restart_and_verification(self):
        with patch.object(access, 'status', return_value=snapshot()), \
                patch.object(access, 'tailnet_path', side_effect=[{'status': 'critical'}, {'status': 'critical'}, {'status': 'healthy'}]), \
                patch.object(access, 'restart', return_value=True) as restart:
            result = access.check_tailscale(True)
        restart.assert_called_once_with('tailscaled.service')
        self.assertEqual(result['status'], 'healthy')
        self.assertTrue(result['restart_attempted'])

    def test_local_api_unavailable_after_restart_remains_critical(self):
        with patch.object(access, 'status', side_effect=access.LocalAPIUnavailable('unavailable')), \
                patch.object(access, 'restart', return_value=False) as restart:
            result = access.check_tailscale(True)
        self.assertEqual(result['status'], 'critical')
        restart.assert_called_once_with('tailscaled.service')

    def test_read_only_probe_never_restarts_a_service(self):
        with patch.object(access, 'status', side_effect=access.LocalAPIUnavailable('unavailable')), \
                patch.object(access, 'restart') as restart:
            result = access.check_tailscale(False)
        self.assertEqual(result['status'], 'critical')
        restart.assert_not_called()

    def test_login_required_is_manual_intervention_without_restart(self):
        with patch.object(access, 'status', return_value=snapshot('NeedsLogin')), patch.object(access, 'restart') as restart:
            result = access.check_tailscale(True)
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('NeedsLogin', result['reason'])
        restart.assert_not_called()

    def test_health_warning_is_reported_even_when_a_peer_responds(self):
        data = snapshot(); data['Health'] = ['Subnet routing requires IP forwarding']
        with patch.object(access, 'status', return_value=data), \
                patch.object(access, 'tailnet_path', return_value={'status': 'healthy'}), \
                patch.object(access, 'restart') as restart:
            result = access.check_tailscale(True)
        self.assertEqual(result['status'], 'degraded')
        self.assertEqual(result['health_warnings'], data['Health'])
        self.assertEqual(result['forwarding']['ipv4'], False)
        restart.assert_not_called()

    def test_inactive_service_with_responding_daemon_is_degraded_not_restarted(self):
        with patch.object(access, 'status', return_value=snapshot()), \
                patch.object(access, 'command', return_value=Mock(returncode=3)), \
                patch.object(access, 'tailnet_path', return_value={'status': 'healthy'}), \
                patch.object(access, 'restart') as restart:
            result = access.check_tailscale(True)
        self.assertEqual(result['status'], 'degraded')
        self.assertFalse(result['systemd_active'])
        restart.assert_not_called()

    def test_offline_control_state_does_not_hide_working_peer_path(self):
        data = snapshot(); data['Self']['Online'] = False
        with patch.object(access, 'status', return_value=data), \
                patch.object(access, 'tailnet_path', return_value={'status': 'healthy'}), \
                patch.object(access, 'restart') as restart:
            result = access.check_tailscale(True)
        self.assertEqual(result['status'], 'degraded')
        self.assertFalse(result['self_online'])
        restart.assert_not_called()

    def test_userspace_mode_requires_configuration_not_reboot(self):
        data = snapshot(); data['TUN'] = False
        with patch.object(access, 'status', return_value=data), patch.object(access, 'restart') as restart:
            result = access.check_tailscale(True)
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('manual', result['reason'])
        restart.assert_not_called()

    def test_invalid_health_payload_is_not_silently_healthy(self):
        data = snapshot(); data['Health'] = 'unexpected-string'
        with patch.object(access, 'status', return_value=data), \
                patch.object(access, 'tailnet_path', return_value={'status': 'healthy'}):
            with self.assertRaises(ValueError):
                access.check_tailscale()


class RouteAndDiagnosticTests(unittest.TestCase):
    def test_effective_route_must_use_the_expected_tun(self):
        with patch.object(access, 'command', return_value=Mock(returncode=0, stdout='[{"dev":"eth0"}]')):
            self.assertFalse(access.route_ready('100.64.0.2'))
        with patch.object(access, 'command', return_value=Mock(returncode=0, stdout='[{"dev":"tailscale0"}]')) as command:
            self.assertTrue(access.route_ready('100.64.0.2'))
        self.assertIn('-4', command.call_args.args[0])

    def test_wrong_route_cannot_be_masked_by_an_internal_ping(self):
        data = snapshot(peers={'a': peer('100.64.0.2')})
        with patch.object(access, 'interface_ready', return_value=True), \
                patch.object(access, 'route_ready', return_value=False), patch.object(access, 'ping') as ping:
            result = access.tailnet_path(data)
        self.assertEqual(result['status'], 'critical')
        self.assertEqual(result['reason'], 'kernel-routes-unavailable')
        ping.assert_not_called()

    def test_udp_blocked_but_relay_available_is_not_automatically_a_failure(self):
        data = {'UDP': False, 'IPv4': True, 'RegionLatency': {'1': 123}}
        with patch.object(access, 'command', return_value=Mock(returncode=0, stdout=json.dumps(data))):
            result = access.network_diagnostics()
        self.assertTrue(result['available'])
        self.assertFalse(result['udp'])
        self.assertEqual(result['reachable_relay_regions'], 1)

    def test_netcheck_failure_does_not_erase_the_original_failure_evidence(self):
        with patch.object(access, 'command', side_effect=subprocess.TimeoutExpired('netcheck', 10)):
            self.assertFalse(access.network_diagnostics()['available'])


class SSHTests(unittest.TestCase):
    def test_healthy_ssh_is_not_restarted(self):
        with patch.object(access, 'ssh_ready', return_value=True), patch.object(access, 'restart') as restart:
            self.assertEqual(access.check_ssh(recover=True)['status'], 'healthy')
        restart.assert_not_called()

    def test_invalid_ssh_config_never_triggers_restart(self):
        with patch.object(access, 'ssh_ready', return_value=False), \
                patch.object(access, 'command', return_value=Mock(returncode=1)), patch.object(access, 'restart') as restart:
            result = access.check_ssh(recover=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertIn('manual-intervention', result['reason'])
        restart.assert_not_called()

    def test_failed_service_is_restarted_once_then_rechecked(self):
        with patch.object(access, 'ssh_ready', side_effect=[False, True]), \
                patch.object(access, 'command', return_value=Mock(returncode=0)), \
                patch.object(access, 'restart', return_value=True) as restart:
            result = access.check_ssh(recover=True)
        self.assertEqual(result['status'], 'healthy')
        restart.assert_called_once_with('ssh.service')

    def test_failed_recovery_remains_critical_without_repeated_restarts(self):
        with patch.object(access, 'ssh_ready', return_value=False), patch.object(access.time, 'sleep'), \
                patch.object(access, 'command', return_value=Mock(returncode=0)), \
                patch.object(access, 'restart', return_value=False) as restart:
            result = access.check_ssh(recover=True)
        self.assertEqual(result['status'], 'critical')
        restart.assert_called_once_with('ssh.service')

    def test_banner_checks_protocol_and_handles_fragmented_tcp_data(self):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.recv.side_effect = [b'SSH-', b'2.0-OpenSSH_test\r\n']
        with patch.object(access.socket, 'create_connection', return_value=connection):
            self.assertTrue(access.ssh_banner('127.0.0.1', 22))
        connection.recv.side_effect = [b'HTTP/1.1 200 OK\r\n']
        with patch.object(access.socket, 'create_connection', return_value=connection):
            self.assertFalse(access.ssh_banner('127.0.0.1', 22))

    def test_both_components_are_checked_even_when_one_fails(self):
        with patch.object(access, 'check_ssh', return_value={'status': 'critical'}), \
                patch.object(access, 'check_tailscale', return_value={'status': 'healthy'}) as tailscale:
            result = access.inspect()
        tailscale.assert_called_once()
        self.assertEqual(result['status'], 'critical')


if __name__ == '__main__':
    unittest.main()
