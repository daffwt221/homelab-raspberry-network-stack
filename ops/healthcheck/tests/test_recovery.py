import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('recovery', Path(__file__).parents[1] / 'recovery.py')
recovery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = recovery
SPEC.loader.exec_module(recovery)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = recovery.Policy()

    def failures(self, state, boot, uptime, kind='containers'):
        for _ in range(3):
            state, decision = recovery.transition(state, 'failure', boot, uptime, self.policy, kind)
        return state, decision

    def test_first_boot_threshold_and_cooldown(self):
        state, decision = self.failures(None, 'a', 599)
        self.assertEqual(decision, ('wait', 3, 600, 'boot-cooldown'))
        state, decision = recovery.transition(state, 'failure', 'a', 600, self.policy, 'containers')
        self.assertEqual(decision, ('reboot', 4, 600, 'reserved'))
        self.assertEqual(state['reboot_attempts'], 1)
        self.assertEqual(state['container_reboot_attempts'], 1)

    def test_persistent_backoff_across_boots_and_global_ceiling(self):
        state = None
        for boot, uptime, delay in [('a', 600, 600), ('b', 1200, 1200), ('c', 1800, 1800), ('d', 1800, 1800)]:
            state, decision = self.failures(state, boot, uptime, 'critical')
            self.assertEqual(decision[0], 'reboot')
            self.assertEqual(decision[2], delay)
        self.assertEqual(state['reboot_attempts'], 4)

    def test_second_boot_cannot_reboot_after_only_ten_minutes(self):
        state, _ = self.failures(None, 'a', 600)
        state, decision = self.failures(state, 'b', 600)
        self.assertEqual(decision, ('wait', 3, 1200, 'boot-cooldown'))

    def test_no_duplicate_request_in_same_boot(self):
        state, _ = self.failures(None, 'a', 600)
        state, decision = recovery.transition(state, 'failure', 'a', 5000, self.policy, 'critical')
        self.assertEqual(decision[3], 'already-requested-this-boot')
        self.assertEqual(state['reboot_attempts'], 1)

    def test_container_budget_shared_across_containers_not_critical_checks(self):
        state = None
        for boot, uptime in [('a', 600), ('b', 1200), ('c', 1800)]:
            state, decision = self.failures(state, boot, uptime)
            self.assertEqual(decision[0], 'reboot')
        state, decision = self.failures(state, 'd', 1800)
        self.assertEqual(decision[3], 'container-reboot-limit')
        self.assertEqual(state['container_reboot_attempts'], 3)
        state, decision = recovery.transition(state, 'failure', 'd', 1801, self.policy, 'critical')
        self.assertEqual(decision[0], 'reboot')
        self.assertEqual(decision[2], 1800)
        self.assertEqual(state['container_reboot_attempts'], 3)
        self.assertEqual(state['reboot_attempts'], 4)

    def test_changing_failure_kind_does_not_bypass_global_cooldown(self):
        state, _ = self.failures(None, 'a', 600)
        state, decision = self.failures(state, 'b', 601, 'critical')
        self.assertEqual(decision[3], 'boot-cooldown')
        self.assertEqual(decision[2], 1200)

    def test_single_healthy_check_only_clears_consecutive_failures(self):
        state, _ = self.failures(None, 'a', 600)
        state, decision = recovery.transition(state, 'healthy', 'b', 10, self.policy)
        self.assertEqual(state['failures'], 0)
        self.assertEqual(state['reboot_attempts'], 1)
        self.assertEqual(decision[3], 'observing-stability')

    def test_only_continuous_stability_resets_episode(self):
        state, _ = self.failures(None, 'a', 600)
        for uptime in range(0, 1801, 60):
            state, decision = recovery.transition(state, 'healthy', 'b', uptime, self.policy)
        self.assertEqual(decision[3], 'stable-reset')
        self.assertEqual(state['reboot_attempts'], 0)
        self.assertEqual(state['container_reboot_attempts'], 0)
        self.assertIsNone(state['requested_boot_id'])

    def test_a_failure_restarts_stability_interval(self):
        state, _ = self.failures(None, 'a', 600)
        for uptime in range(0, 1201, 60):
            state, _ = recovery.transition(state, 'healthy', 'b', uptime, self.policy)
        state, _ = recovery.transition(state, 'failure', 'b', 1201, self.policy)
        state, _ = recovery.transition(state, 'healthy', 'b', 1260, self.policy)
        self.assertEqual(state['healthy_since'], 1260)
        self.assertEqual(state['reboot_attempts'], 1)

    def test_long_gap_does_not_count_as_healthy_monitoring(self):
        state, _ = self.failures(None, 'a', 600)
        state, _ = recovery.transition(state, 'healthy', 'b', 10, self.policy)
        state, _ = recovery.transition(state, 'healthy', 'b', 2000, self.policy)
        self.assertEqual(state['healthy_since'], 2000)
        self.assertEqual(state['reboot_attempts'], 1)

    def test_manual_reboot_does_not_erase_attempts_or_count_as_stability(self):
        state, _ = self.failures(None, 'a', 600)
        state, _ = recovery.transition(state, 'healthy', 'b', 100, self.policy)
        state, _ = recovery.transition(state, 'healthy', 'c', 1, self.policy)
        self.assertEqual(state['healthy_since'], 1)
        self.assertEqual(state['reboot_attempts'], 1)

    def test_corrupt_or_incomplete_state_is_not_reset_implicitly(self):
        for state in ({}, {'version': 1}, {'version': 2}):
            with self.assertRaises(ValueError):
                recovery.transition(state, 'failure', 'a', 600, self.policy)

    def test_same_boot_clock_regression_suppresses_reboot(self):
        state, _ = self.failures(None, 'a', 600)
        with self.assertRaises(ValueError):
            recovery.transition(state, 'failure', 'a', 10, self.policy)

    def test_invalid_policy_rejected(self):
        with self.assertRaises(ValueError):
            recovery.Policy(base_cooldown=1800, max_cooldown=600)
        with self.assertRaises(ValueError):
            recovery.Policy(container_reboot_limit=0)

    def test_access_reboot_budget_persists_and_is_shared_by_ssh_and_tailnet(self):
        state, decision = self.failures(None, 'a', 600, 'access')
        self.assertEqual(decision[0], 'reboot')
        self.assertEqual(state['access_reboot_attempts'], 1)
        state, decision = self.failures(state, 'b', 1200, 'access')
        self.assertEqual(decision[3], 'access-reboot-limit')
        self.assertEqual(state['reboot_attempts'], 1)

    def test_old_state_migration_preserves_global_and_container_budgets(self):
        state, _ = self.failures(None, 'a', 600)
        old = dict(state, version=1)
        for key in ('access_reboot_attempts', 'dns_reboot_attempts', 'storage_reboot_attempts',
                    'last_failure_kind', 'kind_failures'):
            del old[key]
        migrated = recovery.upgrade_state(old)
        self.assertEqual(migrated['version'], 3)
        self.assertEqual(migrated['reboot_attempts'], 1)
        self.assertEqual(migrated['container_reboot_attempts'], 1)
        self.assertEqual(migrated['requested_boot_id'], 'a')
        self.assertEqual(migrated['access_reboot_attempts'], 0)

    def test_inconclusive_probe_does_not_rearm_budgets_or_request_reboot(self):
        state, _ = self.failures(None, 'a', 600, 'access')
        state, decision = recovery.transition(state, 'degraded', 'b', 5000, self.policy)
        self.assertEqual(decision[0], 'wait')
        self.assertEqual(state['access_reboot_attempts'], 1)
        self.assertEqual(state['reboot_attempts'], 1)
        self.assertIsNone(state['healthy_since'])

    def test_stability_resets_access_budget_too(self):
        state, _ = self.failures(None, 'a', 600, 'access')
        for uptime in range(0, 1801, 60):
            state, _ = recovery.transition(state, 'healthy', 'b', uptime, self.policy)
        self.assertEqual(state['access_reboot_attempts'], 0)

    def test_v2_migration_preserves_access_budget_and_reservation(self):
        state, _ = self.failures(None, 'a', 600, 'access')
        old = dict(state, version=2)
        for key in ('dns_reboot_attempts', 'storage_reboot_attempts', 'last_failure_kind', 'kind_failures'):
            del old[key]
        migrated = recovery.upgrade_state(old)
        self.assertEqual(migrated['access_reboot_attempts'], 1)
        self.assertEqual(migrated['requested_boot_id'], 'a')
        self.assertEqual(migrated['storage_reboot_attempts'], 0)

    def test_dns_and_storage_have_one_attempt_each_under_the_same_cooldown(self):
        state, decision = self.failures(None, 'a', 600, 'dns')
        self.assertEqual(decision[0], 'reboot')
        state, decision = self.failures(state, 'b', 1200, 'dns')
        self.assertEqual(decision[3], 'dns-reboot-limit')
        state, decision = self.failures(state, 'b', 1201, 'storage')
        self.assertEqual(decision[:3], ('reboot', 6, 1200))
        state, decision = self.failures(state, 'c', 1800, 'storage')
        self.assertEqual(decision[3], 'storage-reboot-limit')

    def test_dns_and_storage_do_not_inherit_other_faults_streaks(self):
        for kind in ('dns', 'storage'):
            state, _ = self.failures(None, 'a', 100, 'containers')
            state, decision = recovery.transition(state, 'failure', 'a', 900, self.policy, kind)
            self.assertEqual(decision[3], 'below-failure-threshold')
            self.assertEqual(state['kind_failures'], 1)


class StorageTests(unittest.TestCase):
    def test_atomic_state_has_private_mode_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'recovery.json'
            state, _ = recovery.transition(None, 'failure', 'a', 600, recovery.Policy())
            recovery.atomic_write(path, state)
            self.assertEqual(recovery.read_state(path), state)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_failed_replace_keeps_original_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'recovery.json'
            state = recovery.initial('a', 600)
            recovery.atomic_write(path, state)
            changed, _ = recovery.transition(state, 'failure', 'a', 600, recovery.Policy())
            with patch.object(recovery.os, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    recovery.atomic_write(path, changed)
            self.assertEqual(recovery.read_state(path), state)
            self.assertFalse(list(Path(temp).glob('.recovery-*')))

    def test_invalid_json_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'recovery.json'
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                recovery.read_state(path)
            path.unlink()
            other = Path(temp) / 'other'
            other.write_text('{}')
            path.symlink_to(other)
            with self.assertRaises(ValueError):
                recovery.read_state(path)
            self.assertEqual(other.read_text(), '{}')

    def test_real_lock_prevents_duplicate_parallel_reservations(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, str(Path(recovery.__file__)), '--state-dir', temp,
                       '--failures', '1', '--base-cooldown', '1', '--max-cooldown', '2', 'failure']
            def request(_):
                return subprocess.run(command, text=True, capture_output=True, check=True).stdout.split()[0]
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                decisions = list(pool.map(request, range(4)))
            self.assertEqual(decisions.count('reboot'), 1)
            state = recovery.read_state(Path(temp) / 'recovery.json')
            self.assertEqual(state['reboot_attempts'], 1)
            self.assertEqual(state['failures'], 4)


if __name__ == '__main__':
    unittest.main()
