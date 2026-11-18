"""Run the actual shell function with fakes; never execute the healthcheck body."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'homelab-healthcheck'


def function_under_test():
    script = SCRIPT.read_text()
    start = script.index('check_unhealthy_containers() {')
    end = script.index('\n}\n', start) + len('\n}\n')
    return script[start:end]


@unittest.skipUnless(os.name == 'posix', 'POSIX shell required')
class ContainerRecoveryTests(unittest.TestCase):
    def scan(self, unhealthy, states=None, restart_failures=()):
        states = states or {}
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp) / 'calls'
            calls.touch()
            # Each fake handles only the invocation expected from this function.
            # Unknown commands fail instead of reaching the host Docker daemon.
            cases = []
            for name, state in states.items():
                action = 'return 1' if state == 'inspect-error' else "printf '%s\\n' " + shlex.quote(state)
                cases.append(shlex.quote(name) + ') ' + action + ' ;;')
            prelude = r"""
set -eu
CRITICAL_CONTAINERS="caddy prometheus node-exporter grafana portainer"
log() { printf 'log: %s\n' "$*"; }
timeout() { shift; "$@"; }
sleep() { :; }
capture_container_logs() { printf 'capture %s\n' "$1" >> "$TEST_CALLS"; }
fail() {
    printf 'fail\n' >> "$TEST_CALLS"
    printf 'failure: %s\n' "$1"
    exit 42
}
docker() {
    case "$1" in
        ps) printf '%s\n' "$TEST_UNHEALTHY" ;;
        restart)
            printf 'restart %s\n' "$2" >> "$TEST_CALLS"
            case " $TEST_RESTART_FAILURES " in
                *" $2 "*) return 1 ;;
            esac
            return 0 ;;
        inspect)
            for inspected do :; done
            printf 'inspect %s\n' "$inspected" >> "$TEST_CALLS"
            case "$inspected" in
"""
            prelude += '\n'.join(cases)
            prelude += r"""
                *) printf 'healthy\n' ;;
            esac ;;
        *) return 99 ;;
    esac
}
"""
            env = dict(os.environ, TEST_CALLS=str(calls), TEST_UNHEALTHY='\n'.join(unhealthy),
                       TEST_RESTART_FAILURES=' '.join(restart_failures))
            result = subprocess.run(['/bin/sh'], input=prelude + function_under_test() +
                                    '\nif check_unhealthy_containers; then :; else fail \"$CONTAINER_FAILURE_REASON\"; fi\n', text=True,
                                    capture_output=True, env=env, timeout=5)
            self.last_all_calls = calls.read_text().splitlines()
            return result, [call for call in self.last_all_calls if not call.startswith('capture ')]

    def test_recovered_first_does_not_hide_second_failure(self):
        result, calls = self.scan(['caddy', 'prometheus'], {'prometheus': 'unhealthy'})
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(calls, ['restart caddy', 'inspect caddy', 'restart prometheus', 'inspect prometheus', 'fail'])
        self.assertIn('prometheus (health=unhealthy)', result.stdout)

    def test_all_recovered_containers_are_checked(self):
        result, calls = self.scan(['caddy', 'prometheus'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['restart caddy', 'inspect caddy', 'restart prometheus', 'inspect prometheus'])
        self.assertNotIn('non-critical unhealthy containers ignored', result.stdout)

    def test_failed_restart_does_not_skip_remaining_containers(self):
        result, calls = self.scan(['caddy', 'prometheus'], restart_failures=['caddy'])
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(calls, ['restart caddy', 'restart prometheus', 'inspect prometheus', 'fail'])
        self.assertIn('caddy (restart failed)', result.stdout)

    def test_multiple_failures_count_once_and_report_every_container(self):
        result, calls = self.scan(['caddy', 'prometheus'], {'caddy': 'unhealthy', 'prometheus': 'starting'})
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(calls.count('fail'), 1)
        summary = [line for line in result.stdout.splitlines() if line.startswith('failure:')][0]
        self.assertIn('caddy (health=unhealthy)', summary)
        self.assertIn('prometheus (health=starting)', summary)

    def test_noncritical_containers_are_not_restarted_or_misreported(self):
        result, calls = self.scan(['optional-app', 'caddy'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['restart caddy', 'inspect caddy'])
        ignored = [line for line in result.stdout.splitlines() if 'non-critical unhealthy containers ignored' in line]
        self.assertEqual(len(ignored), 1)
        self.assertIn('optional-app', ignored[0])
        self.assertNotIn('caddy', ignored[0])

    def test_no_unhealthy_containers(self):
        result, calls = self.scan([])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_only_noncritical_containers(self):
        result, calls = self.scan(['optional-app'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])
        self.assertIn('non-critical unhealthy containers ignored', result.stdout)

    def test_inspection_failure_counts_once_and_scan_continues(self):
        result, calls = self.scan(['caddy', 'prometheus'], {'caddy': 'inspect-error'})
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(calls, ['restart caddy', 'inspect caddy', 'restart prometheus', 'inspect prometheus', 'fail'])
        self.assertIn('caddy (health=unknown)', result.stdout)

    def test_existing_no_healthcheck_policy_is_preserved(self):
        result, calls = self.scan(['caddy', 'prometheus'], {'caddy': 'none'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('inspect prometheus', calls)

    def test_logs_captured_before_any_restart_including_noncritical_containers(self):
        result, _ = self.scan(['optional-app', 'caddy', 'prometheus'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.last_all_calls, ['capture optional-app', 'capture caddy',
                         'restart caddy', 'inspect caddy', 'capture prometheus',
                         'restart prometheus', 'inspect prometheus'])


if __name__ == '__main__':
    unittest.main()
