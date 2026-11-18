import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).parents[1] / 'homelab-healthcheck'


def extract(name):
    source = SCRIPT.read_text()
    start = source.index(name + '() {')
    end = source.index('\n}\n', start) + len('\n}\n')
    return source[start:end]


class CoordinatorTests(unittest.TestCase):
    def run_checks(self, critical_failure='', container_failure=True, access_state='healthy',
                   storage_status=0, dns_status=0):
        prelude = r"""
set -eu
CONTAINER_FAILURE_REASON=""
ACCESS_FAILURE_REASON=""
ACCESS_WARNING=""
DNS_CHECK_HOST=example.test
log() { :; }
check_remote_access() {
    echo CHECK:remote-access
    case "$TEST_ACCESS_STATE" in
        critical) ACCESS_FAILURE_REASON="tailnet/ssh failed" ;;
        unknown) ACCESS_WARNING="no online peers" ;;
    esac
}
degraded() { echo INCONCLUSIVE; }
fail() { printf 'FAIL:%s:%s\n' "${2:-critical}" "$1"; exit 42; }
run_check() {
    printf 'CHECK:%s:%s\n' "$1" "$2"
    if [ "$2" = "$TEST_CRITICAL_FAILURE" ]; then
        if [ "$1" = containers ]; then
            CONTAINER_FAILURE_REASON="${CONTAINER_FAILURE_REASON}$3; "
        else
            fail "$3"
        fi
    fi
}
check_unhealthy_containers() {
    echo SCAN:containers
    if [ "$TEST_CONTAINER_FAILURE" = yes ]; then
        CONTAINER_FAILURE_REASON="caddy did not recover"
        return 1
    fi
}
check_storage() {
    echo CHECK:storage
    STORAGE_STATUS=$TEST_STORAGE_STATUS
    STORAGE_RESULT="storage observation"
}
check_dns() {
    echo "CHECK:dns:$*"
    DNS_STATUS=$TEST_DNS_STATUS
    DNS_RESULT="DNS observation"
}
ok() { echo HEALTHY; }
"""
        env = dict(os.environ, TEST_CRITICAL_FAILURE=critical_failure, TEST_ACCESS_STATE=access_state,
                   TEST_CONTAINER_FAILURE='yes' if container_failure else 'no',
                   TEST_STORAGE_STATUS=str(storage_status), TEST_DNS_STATUS=str(dns_status))
        return subprocess.run(['/bin/sh'], input=prelude + extract('run_checks') + '\nrun_checks\n',
                              text=True, capture_output=True, env=env, timeout=5)

    def test_container_failure_does_not_starve_dns_or_storage(self):
        result = self.run_checks()
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn('CHECK:storage', result.stdout)
        self.assertIn('CHECK:dns:--recover', result.stdout)
        self.assertTrue(result.stdout.splitlines()[-1].startswith('FAIL:containers:'))
        self.assertEqual(result.stdout.count('FAIL:'), 1)
        self.assertNotIn('HEALTHY', result.stdout)

    def test_local_dns_uses_its_own_budget_before_docker(self):
        result = self.run_checks(dns_status=10)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn('FAIL:dns:DNS observation', result.stdout)
        self.assertNotIn('SCAN:containers', result.stdout)

    def test_storage_hold_allows_probes_and_blocks_every_reboot_and_container_recovery(self):
        result = self.run_checks(storage_status=20, access_state='critical', dns_status=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CHECK:remote-access', result.stdout)
        self.assertIn('CHECK:dns:', result.stdout.splitlines())
        self.assertIn('INCONCLUSIVE', result.stdout)
        self.assertNotIn('recovery', result.stdout)
        self.assertNotIn('FAIL:', result.stdout)
        self.assertNotIn('SCAN:containers', result.stdout)

    def test_storage_reset_evidence_uses_only_storage_budget(self):
        result = self.run_checks(storage_status=10, access_state='critical', dns_status=10)
        self.assertEqual(result.returncode, 42)
        self.assertIn('FAIL:storage:storage observation', result.stdout)
        self.assertNotIn('SCAN:containers', result.stdout)

    def test_public_dns_failure_alone_does_not_request_reboot(self):
        result = self.run_checks(container_failure=False, dns_status=20)
        self.assertEqual(result.returncode, 0)
        self.assertIn('CHECK:dns:--recover', result.stdout)
        self.assertIn('INCONCLUSIVE', result.stdout)
        self.assertNotIn('FAIL:', result.stdout)

    def test_healthy_is_only_reported_after_all_checks_pass(self):
        result = self.run_checks(container_failure=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], 'HEALTHY')
        self.assertIn('CHECK:dns:--recover', result.stdout)
        self.assertNotIn('FAIL:', result.stdout)

    def test_container_http_failure_uses_container_budget_and_continues_host_checks(self):
        result = self.run_checks('grafana health endpoint', container_failure=False)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn('CHECK:containers:grafana health endpoint', result.stdout)
        self.assertIn('CHECK:storage', result.stdout)
        self.assertIn('CHECK:dns:--recover', result.stdout)
        self.assertTrue(result.stdout.splitlines()[-1].startswith('FAIL:containers:'))

    def test_access_checks_run_even_when_docker_is_unavailable(self):
        result = self.run_checks('docker daemon responsiveness')
        self.assertIn('CHECK:remote-access', result.stdout.splitlines()[:2])
        self.assertEqual(result.returncode, 42)

    def test_access_failure_uses_its_own_budget_after_host_checks(self):
        result = self.run_checks(container_failure=False, access_state='critical')
        self.assertIn('CHECK:dns:--recover', result.stdout)
        self.assertTrue(result.stdout.splitlines()[-1].startswith('FAIL:access:'))
        self.assertNotIn('HEALTHY', result.stdout)

    def test_no_peers_is_inconclusive_not_healthy_or_reboot(self):
        result = self.run_checks(container_failure=False, access_state='unknown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], 'INCONCLUSIVE')
        self.assertNotIn('HEALTHY', result.stdout)
        self.assertNotIn('FAIL:', result.stdout)


class ProbeClassificationTests(unittest.TestCase):
    def run_probe(self, kind):
        prelude = r"""
set -eu
CONTAINER_FAILURE_REASON=""
log() { :; }
timeout() { return 1; }
fail() { echo CRITICAL_REBOOT_PATH; exit 42; }
"""
        return subprocess.run(['/bin/sh'], input=prelude + extract('run_check') +
                              '\nrun_check ' + kind + ' label reason fake-command\n' +
                              'printf \'PENDING:%s\\n\' "$CONTAINER_FAILURE_REASON"\n',
                              text=True, capture_output=True, timeout=5)

    def test_failed_container_probe_is_queued(self):
        result = self.run_probe('containers')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PENDING:reason;', result.stdout)
        self.assertNotIn('CRITICAL_REBOOT_PATH', result.stdout)

    def test_failed_host_probe_uses_critical_path(self):
        result = self.run_probe('critical')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn('CRITICAL_REBOOT_PATH', result.stdout)


class ShellDecisionTests(unittest.TestCase):
    def run_failure(self, decision='wait 3 1800 container-reboot-limit', state_error=False, reboot_error=False):
        function = extract('fail')
        # Only replace the irreversible command; assert the seam exists exactly
        # once. No test can ever execute the real systemctl reboot.
        self.assertEqual(function.count('/bin/systemctl reboot'), 1)
        function = function.replace('/bin/systemctl reboot', 'fake_systemctl reboot')
        prelude = r"""
set -eu
MAX_FAILURES=3
log() { printf 'LOG:%s\n' "$*"; }
recovery_decision() {
    echo RESERVATION_ATTEMPT >&2
    if [ "$TEST_STATE_ERROR" = yes ]; then return 1; fi
    printf '%s\n' "$TEST_DECISION"
}
write_report() { echo REPORT >&2; }
fake_systemctl() {
    echo REBOOT >&2
    if [ "$TEST_REBOOT_ERROR" = yes ]; then return 1; fi
}
"""
        env = dict(os.environ, TEST_DECISION=decision,
                   TEST_STATE_ERROR='yes' if state_error else 'no',
                   TEST_REBOOT_ERROR='yes' if reboot_error else 'no')
        return subprocess.run(['/bin/sh'], input=prelude + function + '\nfail problem containers\n',
                              text=True, capture_output=True, env=env, timeout=5)

    def test_limit_suppresses_reboot(self):
        result = self.run_failure()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('REBOOT', result.stderr)
        self.assertIn('container-reboot-limit', result.stdout)

    def test_unavailable_state_never_requests_reboot(self):
        result = self.run_failure(state_error=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('REBOOT', result.stderr)
        self.assertIn('recovery state unavailable', result.stdout)

    def test_reservation_precedes_report_and_single_reboot_request(self):
        result = self.run_failure('reboot 3 600 reserved')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.splitlines(), ['RESERVATION_ATTEMPT', 'REPORT', 'REBOOT'])

    def test_rejected_systemctl_request_is_not_retried(self):
        result = self.run_failure('reboot 3 600 reserved', reboot_error=True)
        self.assertEqual(result.stderr.splitlines().count('REBOOT'), 1)
        self.assertIn('no automatic repeat request', result.stdout)

    def test_invalid_protocol_or_counters_never_reboot(self):
        for decision in ('reboot 3', 'reboot invalid 600 reserved', 'unknown 3 600 invalid'):
            result = self.run_failure(decision)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('REBOOT', result.stderr)


if __name__ == '__main__':
    unittest.main()
