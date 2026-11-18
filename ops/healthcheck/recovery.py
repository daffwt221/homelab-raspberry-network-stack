#!/usr/bin/python3
"""Global reboot policy. This helper records decisions; it NEVER reboots the host."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


@dataclass(frozen=True)
class Policy:
    failures: int = 3
    base_cooldown: int = 600
    max_cooldown: int = 1800
    stable_seconds: int = 1800
    max_healthy_gap: int = 180
    container_reboot_limit: int = 3
    access_reboot_limit: int = 1
    dns_reboot_limit: int = 1
    storage_reboot_limit: int = 1

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in self.__dict__.values()):
            raise ValueError('Policy values must be positive integers')
        if self.base_cooldown > self.max_cooldown:
            raise ValueError('Base cooldown exceeds maximum')


KINDS = ('critical', 'containers', 'access', 'dns', 'storage')
ATTEMPT_FIELDS = {
    'containers': 'container_reboot_attempts', 'access': 'access_reboot_attempts',
    'dns': 'dns_reboot_attempts', 'storage': 'storage_reboot_attempts',
}


def initial(boot_id, uptime):
    return {'version': 3, 'boot_id': boot_id, 'last_seen': uptime,
            'failures': 0, 'last_failure_kind': None, 'kind_failures': 0,
            'reboot_attempts': 0, **dict.fromkeys(ATTEMPT_FIELDS.values(), 0),
            'requested_boot_id': None, 'healthy_since': None, 'last_healthy': None}


def validate(state):
    if not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] != 3:
        raise ValueError('Invalid recovery state version; automatic reboot suppressed')
    if set(state) != set(initial('boot', 0)):
        raise ValueError('Incomplete recovery state; automatic reboot suppressed')
    for name in ('failures', 'reboot_attempts', 'kind_failures', 'last_seen', *ATTEMPT_FIELDS.values()):
        if type(state[name]) is not int or not 0 <= state[name] <= 10**12:
            raise ValueError('Invalid state value: ' + name)
    if not isinstance(state['boot_id'], str) or not state['boot_id']:
        raise ValueError('Invalid boot identity')
    if state['requested_boot_id'] is not None and not isinstance(state['requested_boot_id'], str):
        raise ValueError('Invalid reboot reservation')
    if sum(state[field] for field in ATTEMPT_FIELDS.values()) > state['reboot_attempts']:
        raise ValueError('Invalid reboot attempt counts')
    if (state['last_failure_kind'] not in (None, *KINDS)
            or state['kind_failures'] > state['failures']
            or (state['last_failure_kind'] is None) != (state['kind_failures'] == 0)):
        raise ValueError('Invalid failure streak')
    since, last = state['healthy_since'], state['last_healthy']
    if (since is None) != (last is None):
        raise ValueError('Incomplete stability interval')
    if since is not None and not (type(since) is int and type(last) is int and 0 <= since <= last <= state['last_seen']):
        raise ValueError('Invalid stability interval')


def upgrade_state(state):
    if isinstance(state, dict) and type(state.get('version')) is int and state['version'] in (1, 2):
        added = {'dns_reboot_attempts', 'storage_reboot_attempts', 'last_failure_kind', 'kind_failures'}
        if state['version'] == 1:
            added.add('access_reboot_attempts')
        defaults = initial('boot', 0)
        if set(state) != set(defaults) - added:
            raise ValueError('Incomplete legacy recovery state')
        state = dict(state, **{key: defaults[key] for key in added}, version=3)
    validate(state)
    return state


def cooldown(attempts, policy):
    delay = policy.base_cooldown
    while attempts > 0 and delay < policy.max_cooldown:
        delay = min(delay * 2, policy.max_cooldown)
        attempts -= 1
    return delay


def transition(state, event, boot_id, uptime, policy, kind='critical'):
    """Return (new state, action/failures/cooldown/reason). No I/O or wall clock."""
    if event not in ('failure', 'healthy', 'degraded') or kind not in KINDS:
        raise ValueError('Unknown event or failure category')
    if type(uptime) is not int or uptime < 0 or not boot_id:
        raise ValueError('Missing boot clock')
    if state is None:
        state = initial(boot_id, uptime)
    state = dict(upgrade_state(state))
    if state['boot_id'] != boot_id:
        # Keep episode-wide counters even after a manual reboot.
        state.update(boot_id=boot_id, failures=0, last_failure_kind=None, kind_failures=0,
                     healthy_since=None, last_healthy=None)
    elif uptime < state['last_seen']:
        raise ValueError('Boot uptime moved backwards; automatic reboot suppressed')
    state['last_seen'] = uptime
    if event == 'degraded':
        state.update(failures=0, last_failure_kind=None, kind_failures=0,
                     healthy_since=None, last_healthy=None)
        return state, ('wait', 0, cooldown(state['reboot_attempts'], policy), 'observation-inconclusive')
    if event == 'healthy':
        previous = state['last_healthy']
        if previous is None or uptime - previous > policy.max_healthy_gap:
            state['healthy_since'] = uptime
        state['last_healthy'] = uptime
        state.update(failures=0, last_failure_kind=None, kind_failures=0)
        reason = 'observing-stability'
        if uptime - state['healthy_since'] >= policy.stable_seconds:
            state.update(reboot_attempts=0, requested_boot_id=None,
                         **dict.fromkeys(ATTEMPT_FIELDS.values(), 0))
            reason = 'stable-reset'
        return state, ('healthy', 0, cooldown(state['reboot_attempts'], policy), reason)
    state['failures'] += 1
    state['kind_failures'] = state['kind_failures'] + 1 if state['last_failure_kind'] == kind else 1
    state['last_failure_kind'] = kind
    state.update(healthy_since=None, last_healthy=None)
    delay = cooldown(state['reboot_attempts'], policy)
    reason = 'below-failure-threshold'
    # Newly observed DNS/storage failures cannot inherit another fault's streak.
    streak = state['kind_failures'] if kind in ('dns', 'storage') else state['failures']
    limits = {'containers': policy.container_reboot_limit, 'access': policy.access_reboot_limit,
              'dns': policy.dns_reboot_limit, 'storage': policy.storage_reboot_limit}
    attempt_field = ATTEMPT_FIELDS.get(kind)
    if streak >= policy.failures:
        if state['requested_boot_id'] == boot_id:
            reason = 'already-requested-this-boot'
        elif attempt_field is not None and state[attempt_field] >= limits[kind]:
            limit_name = 'container' if kind == 'containers' else kind
            reason = f'{limit_name}-reboot-limit'
        elif uptime < delay:
            reason = 'boot-cooldown'
        else:
            # Persist BEFORE the caller requests reboot. Another request in this
            # boot is blocked, even if systemctl fails or the process is killed.
            state['reboot_attempts'] += 1
            if attempt_field is not None:
                state[attempt_field] += 1
            state['requested_boot_id'] = boot_id
            return state, ('reboot', state['failures'], delay, 'reserved')
    return state, ('wait', state['failures'], delay, reason)


def read_state(path):
    if not path.exists() and not path.is_symlink():
        return None
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('State must be a regular file')
    if path.stat().st_size > 16384:
        raise ValueError('Oversized recovery state')
    state = json.loads(path.read_text())
    return upgrade_state(state)


def atomic_write(path, state):
    validate(state)
    fd, temporary = tempfile.mkstemp(prefix='.recovery-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked_directory(directory):
    if directory.is_symlink():
        raise ValueError('State directory must not be a symlink')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_uid != os.geteuid():
        raise ValueError('State directory belongs to another user')
    directory.chmod(0o700)
    fd = os.open(directory / 'recovery.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('/var/lib/homelab-healthcheck'))
    for name, default in Policy().__dict__.items():
        parser.add_argument('--' + name.replace('_', '-'), type=int, default=default)
    parser.add_argument('--kind', choices=KINDS, default='critical')
    parser.add_argument('event', choices=('failure', 'healthy', 'degraded', 'status', 'reset'))
    args = parser.parse_args()
    policy = Policy(**{name: getattr(args, name) for name in Policy().__dict__})
    path = args.state_dir / 'recovery.json'
    with locked_directory(args.state_dir):
        if args.event == 'status':
            print(json.dumps(read_state(path), indent=2))
            return
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        uptime = int(float(Path('/proc/uptime').read_text().split()[0]))
        if args.event == 'reset':
            atomic_write(path, initial(boot, uptime))
            print('Recovery episode explicitly reset')
            return
        state, decision = transition(read_state(path), args.event, boot, uptime, policy, args.kind)
        atomic_write(path, state)
        print(' '.join(map(str, decision)))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('recovery state error: ' + str(error), file=sys.stderr)
        sys.exit(1)
