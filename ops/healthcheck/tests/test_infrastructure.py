import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('infrastructure', Path(__file__).parents[1] / 'infrastructure.py')
infra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(infra)

ADDRESS = '100.64.0.12'
UUID = '11111111-2222-4333-8444-555555555555'
TARGET = '/mnt/nvme'
BASELINE = Path('/fake/nvme.json')
ROOT = {'options': 'rw,relatime'}
MOUNT = {'uuid': UUID, 'source': '/dev/sdb', 'fstype': 'ext4', 'options': 'rw,noatime'}


def answer(*addresses, rcode='NOERROR'):
    return {'rcode': rcode, 'addresses': list(addresses)}


class DNSChecks(unittest.TestCase):
    def check(self, recover=True):
        return infra.check_dns('pihole.home.arpa.', ADDRESS, 53, 5335, recover)

    def test_stopped_daemon_must_fail_an_offline_query_before_restart(self):
        healthy = ({}, [], [])
        with patch.object(infra, 'dns_local', side_effect=[({}, ['unbound'], []), healthy]), \
                patch.object(infra, 'restart_dns', return_value='restarted') as restart, \
                patch.object(infra.time, 'sleep'), \
                patch.object(infra, 'public_dns', return_value={'status': 'healthy'}):
            outcome = self.check()
        restart.assert_called_once_with('unbound')
        self.assertEqual(outcome['status'], 'healthy')

    def test_failed_restart_with_persistent_local_failure_can_escalate(self):
        with patch.object(infra, 'dns_local', return_value=({}, ['pihole'], [])), \
                patch.object(infra, 'restart_dns', return_value='restart-failed'), \
                patch.object(infra.time, 'sleep'):
            self.assertEqual(self.check()['reboot_eligible'], ['pihole'])
            self.assertEqual(self.check()['status'], 'critical')

    def test_invalid_configuration_does_not_escalate(self):
        with patch.object(infra, 'dns_local', return_value=({}, ['unbound'], [])), \
                patch.object(infra, 'restart_dns', return_value='invalid-unbound-config'), \
                patch.object(infra.time, 'sleep'):
            self.assertEqual(self.check()['status'], 'degraded')

    def test_wrong_record_is_a_configuration_issue_not_a_restart(self):
        with patch.object(infra, 'query', side_effect=[answer('100.64.0.3'), answer('127.0.0.1')]), \
                patch.object(infra, 'restart_dns') as restart:
            outcome = self.check()
        restart.assert_not_called()
        self.assertEqual(outcome['reason'], 'local-dns-configuration-mismatch')

    def test_public_dns_can_fail_even_with_local_resolvers_and_egress_working(self):
        replies = [answer(ADDRESS), answer('127.0.0.1')] + [answer(rcode='SERVFAIL')] * 4
        with patch.object(infra, 'query', side_effect=replies), \
                patch.object(infra, 'internet_endpoint', return_value='1.1.1.1'), \
                patch.object(infra, 'restart_dns') as restart:
            outcome = self.check()
        restart.assert_not_called()
        self.assertEqual(outcome['status'], 'degraded')
        self.assertEqual(outcome['reason'], 'recursive-dns-failed-with-tcp-egress')

    def test_read_only_never_restarts_even_if_both_services_are_down(self):
        with patch.object(infra, 'query', return_value=answer(rcode=None)), \
                patch.object(infra, 'restart_dns') as restart:
            self.assertEqual(self.check(recover=False)['status'], 'degraded')
        restart.assert_not_called()

    def test_dig_rcode_is_distinguished_from_timeout_and_a_record(self):
        response = ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\nhost. 30 IN A 100.64.0.12\n'
        with patch.object(infra, 'command', return_value=subprocess.CompletedProcess([], 0, response, '')):
            self.assertEqual(infra.query(53, 'host.'), answer(ADDRESS))
        with patch.object(infra, 'command', side_effect=subprocess.TimeoutExpired('dig', 4)):
            self.assertEqual(infra.query(53, 'host.'), answer(rcode=None))


class MountChecks(unittest.TestCase):
    def probe(self, mount=MOUNT, messages=(), baseline=None, count=2, monitor=False):
        with patch.object(infra, 'exact_mount', side_effect=[ROOT, mount]), \
                patch.object(infra, 'kernel_messages', return_value=messages), \
                patch.object(infra, 'read_baseline', return_value=baseline or {}), \
                patch.object(infra, 'smart_counter', return_value=count) as smart, \
                patch.object(infra, 'store_baseline') as save:
            result = infra.check_storage(TARGET, UUID, BASELINE, monitor)
        return result, smart, save

    def test_unsafe_shutdown_increase_is_observed_on_uuid_verified_source(self):
        outcome, smart, saved = self.probe(baseline={'unsafe_shutdowns': 1})
        self.assertEqual(outcome['status'], 'healthy')
        self.assertIn('unsafe-shutdown-counter-increased', outcome['warnings'])
        smart.assert_called_once_with('/dev/sdb')
        saved.assert_not_called()

    def test_unavailable_smart_is_only_a_warning(self):
        outcome, _, _ = self.probe(count=None)
        self.assertEqual(outcome['status'], 'healthy')
        self.assertIn('smart-unsafe-shutdown-counter-unavailable', outcome['warnings'])

    def test_wrong_uuid_or_read_only_do_not_read_smart_or_write(self):
        for mount in (dict(MOUNT, uuid='other'), dict(MOUNT, options='ro,noatime')):
            outcome, smart, save = self.probe(mount=mount, monitor=True)
            self.assertEqual(outcome['status'], 'hold')
            smart.assert_not_called()
            save.assert_not_called()

    def test_filesystem_errors_take_priority_over_usb_reset(self):
        messages = ['[ 30.0] pi kernel: EXT4-fs error (device sda): bad header',
                    '[ 40.0] pi kernel: usb 1-1.3: reset high-speed USB device number 4']
        outcome, smart, _ = self.probe(messages=messages, mount=None, baseline={'usb_port': '1-1.3'})
        self.assertEqual(outcome['reason'], 'kernel-storage-errors-this-boot')
        smart.assert_not_called()

    def test_missing_nvme_can_escalate_only_with_correlated_recent_reset_and_failed_boot_mount(self):
        line = '[ 950.000000] pi kernel: usb 1-1.3: reset high-speed USB device number 4'
        for journal, port, failed, state in (
                ([line], '1-1.3', True, 'critical'),
                ([line], '1-1.3', False, 'hold'),
                ([line], '1-1.2', True, 'hold'),
                ([line.replace('950.', '150.')], '1-1.3', True, 'hold'),
                (None, '1-1.3', True, 'hold')):
            with self.subTest(journal=journal, port=port, failed=failed):
                with patch.object(infra, 'exact_mount', side_effect=[ROOT, None]), \
                        patch.object(infra, 'kernel_messages', return_value=journal), \
                        patch.object(infra, 'read_baseline', return_value={'usb_port': port}), \
                        patch.object(infra, 'boot_mount_failed', return_value=failed), \
                        patch.object(Path, 'stat', side_effect=FileNotFoundError), \
                        patch.object(Path, 'read_text', return_value='1000.00 0.00'):
                    self.assertEqual(infra.check_storage(TARGET, UUID, BASELINE)['status'], state)


if __name__ == '__main__':
    unittest.main()
