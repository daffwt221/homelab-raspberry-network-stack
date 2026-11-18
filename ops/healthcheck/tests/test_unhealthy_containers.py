"""Exercise Docker recovery across timer ticks without touching real containers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_coordinator import extract


@unittest.skipUnless(os.name == 'posix', 'POSIX shell required')
class ContainerRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.calls = self.root / 'calls'
        self.calls.touch()

    def scan(self, *, healthy='', starting='', unhealthy='', restart_failure='', list_failure=''):
        prelude = r"""
set -eu
CRITICAL_CONTAINERS="caddy prometheus node-exporter grafana portainer"
log() { printf 'log: %s\n' "$*"; }
timeout() { shift; "$@"; }
capture_container_logs() { printf 'capture %s\n' "$1" >> "$TEST_CALLS"; }
docker() {
    case "$1" in
        ps)
            for arg do
                case "$arg" in
                    "health=$TEST_LIST_FAILURE") return 1 ;;
                    health=healthy) printf '%s\n' "$TEST_HEALTHY"; return ;;
                    health=starting) printf '%s\n' "$TEST_STARTING"; return ;;
                    health=unhealthy) printf '%s\n' "$TEST_UNHEALTHY"; return ;;
                esac
            done
            return 99 ;;
        restart)
            printf 'restart %s\n' "$2" >> "$TEST_CALLS"
            [ "$2" != "$TEST_RESTART_FAILURE" ] ;;
        *) return 99 ;;
    esac
}
"""
        env = dict(os.environ, TEST_CALLS=str(self.calls), TEST_HEALTHY=healthy,
                   TEST_STARTING=starting, TEST_UNHEALTHY=unhealthy,
                   TEST_RESTART_FAILURE=restart_failure, TEST_LIST_FAILURE=list_failure,
                   CONTAINER_RESTART_DIR=str(self.root / 'recovery'))
        result = subprocess.run(
            ['/bin/sh'], input=prelude + extract('check_unhealthy_containers') + r"""
status=0
check_unhealthy_containers || status=$?
printf 'RESULT:%s:%s:%s\n' "$status" "$CONTAINERS_STARTING" "$CONTAINER_FAILURE_REASON"
""", text=True, capture_output=True, env=env, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        _, status, waiting, failures = result.stdout.splitlines()[-1].split(':', 3)
        return int(status), waiting.split(), failures

    def test_slow_start_does_not_repeat_restart_or_count_as_failure(self):
        self.assertEqual(self.scan(unhealthy='grafana id-1'), (0, ['grafana'], ''))
        for _ in range(4):
            self.assertEqual(self.scan(starting='grafana'), (0, ['grafana'], ''))
        self.assertEqual(self.scan(healthy='grafana'), (0, [], ''))
        self.assertEqual(self.calls.read_text().splitlines(), ['capture grafana', 'restart id-1'])

    def test_persistent_failures_escalate_without_restarting_every_tick(self):
        unhealthy = 'caddy caddy-id\nprometheus prometheus-id'
        self.assertEqual(self.scan(unhealthy=unhealthy), (0, ['caddy', 'prometheus'], ''))
        for _ in range(3):
            status, waiting, failures = self.scan(unhealthy=unhealthy)
            self.assertEqual((status, waiting), (1, []))
            self.assertIn('caddy still unhealthy after restart attempt', failures)
            self.assertIn('prometheus still unhealthy after restart attempt', failures)
        self.assertEqual(self.calls.read_text().splitlines(), [
            'capture caddy', 'restart caddy-id', 'capture prometheus', 'restart prometheus-id',
        ])

    def test_healthy_observation_allows_restart_of_a_later_failure(self):
        self.scan(unhealthy='grafana id-1')
        self.scan(healthy='grafana')
        self.assertEqual(self.scan(unhealthy='grafana id-1'), (0, ['grafana'], ''))
        self.assertEqual(self.calls.read_text().splitlines().count('restart id-1'), 2)

    def test_recreated_container_has_its_own_restart_attempt(self):
        self.scan(unhealthy='grafana id-1')
        self.assertEqual(self.scan(unhealthy='grafana id-2'), (0, ['grafana'], ''))
        self.assertEqual(self.calls.read_text().splitlines(), [
            'capture grafana', 'restart id-1', 'capture grafana', 'restart id-2',
        ])

    def test_failed_restart_does_not_hide_other_containers(self):
        status, waiting, failures = self.scan(
            unhealthy='caddy caddy-id\nprometheus prometheus-id', restart_failure='caddy-id')
        self.assertEqual((status, waiting), (1, ['prometheus']))
        self.assertIn('caddy restart failed', failures)
        self.assertEqual(self.calls.read_text().splitlines(), [
            'capture caddy', 'restart caddy-id', 'capture prometheus', 'restart prometheus-id',
        ])

    def test_failed_restart_is_not_retried_on_the_next_tick(self):
        self.scan(unhealthy='caddy caddy-id', restart_failure='caddy-id')
        status, waiting, failures = self.scan(unhealthy='caddy caddy-id')
        self.assertEqual((status, waiting), (1, []))
        self.assertIn('caddy still unhealthy after restart attempt', failures)
        self.assertEqual(self.calls.read_text().splitlines(), ['capture caddy', 'restart caddy-id'])

    def test_optional_container_is_logged_but_does_not_restart_or_hold_checks(self):
        self.assertEqual(self.scan(starting='optional-starting', unhealthy='optional-app optional-id'),
                         (0, [], ''))
        self.assertEqual(self.calls.read_text().splitlines(), ['capture optional-app'])

    def test_docker_status_error_is_not_reported_as_healthy(self):
        status, waiting, failures = self.scan(list_failure='starting')
        self.assertEqual((status, waiting), (1, []))
        self.assertIn('could not list Docker container health', failures)
        self.assertEqual(self.calls.read_text(), '')


if __name__ == '__main__':
    unittest.main()
