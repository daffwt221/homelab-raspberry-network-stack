# Homelab healthcheck

Host-level checks run every minute. Container recovery is best-effort; host reboot
requests are governed by one persistent policy, shared by all failure causes.
The thermal monitor only exports metrics and does not request reboots.

## Recovery policy

Three consecutive failing aggregate runs make a reboot eligible. A successful
aggregate run clears that consecutive-failure counter, but does not immediately
clear the recovery episode or its reboot budgets.

| Previous reboot requests in the episode | Minimum uptime before another request |
| --- | --- |
| 0 | 10 minutes |
| 1 | 20 minutes |
| 2 or more | 30 minutes |

The cooldown applies globally: switching from a container failure to DNS/storage
failure cannot bypass it. It is measured from the current boot's uptime, rather
than the wall clock, so NTP adjustments do not shorten it.

Container failures share a budget of **three host reboot requests** across all
containers and boots. This includes the unhealthy-container scan and HTTP probes
for Node Exporter, Grafana and Prometheus. Once exhausted, container restarts,
checks and diagnostics continue, but those failures no longer request host reboots.
The budget counts durable requests, even if systemctl rejects a request.

DNS and storage each have a budget of **one host reboot request per episode**.
They must reach the threshold on consecutive checks of their own category, and
share the same global uptime cooldown. Access recovery has its own limit.
Only an entirely healthy run records a healthy observation.

The episode resets after **30 minutes of continuous successful aggregate checks
within one boot**, with no gap longer than three minutes between observations.
A failure, missing observations or a new boot restarts that stability interval.
A manual reboot alone never clears the persistent reboot counters.

## DNS and NVMe

The DNS probe queries the configured local Pi-hole record at 127.0.0.1:53 and
Unbound's built-in localhost record on its configured port. Neither lookup needs
the Internet. For an unavailable local daemon, the healthcheck attempts service
recovery and checks again. A missing unit, failed Unbound config validation,
missing record or wrong address is reported for investigation. A confirmed local
failure after a restart attempt can use the DNS reboot budget.

When both local lookups work, public queries check the recursive path. The helper
compares Pi-hole and Unbound and, if needed, attempts short TCP connections to
public IPs without doing DNS lookups. Public DNS or TCP probe failures alone only
mark the run degraded. TCP success demonstrates egress to the tested endpoint.

Storage is checked before Docker. The exact mount must be ext4 and use the
configured NVMe UUID. The SMART reading follows the verified block device instead
of a fixed /dev/sda path. An increase in Unsafe Shutdowns or unavailable SMART
information is a warning. The last observed counter and USB port are saved in
nvme-observation.json under the private recovery directory.

Read-only mounts, wrong volumes, block I/O or ext4 errors, and unexplained missing
mounts hold all automatic host reboots. During the hold, the script still probes
SSH, Tailscale and DNS. It skips service restarts and container recovery. Existing
workloads are left running for operator diagnosis. It never remounts ext4 or
runs fsck. The one NVMe reboot is eligible only when a recent USB reset matches
the observed disk port, the expected UUID has a configured ext4 mount, systemd
reports that mount failed and the kernel journal for the boot has no storage
errors. This allows a bounded retry after a plausible USB transport fault.

## Tailscale and SSH availability

`remote_access.py` runs after the storage probe and before Docker and DNS probes. It always checks both
components, even if the first fails, and never executes a host reboot itself.

Peers are discovered on every run from `tailscale status --json`: only peers marked
`Online: true`, excluding the local node and duplicate addresses. IPv4 is preferred,
with IPv6 supported when IPv4 is absent. No device names or peer IPs are configured.
The probe verifies the TUN interface is UP with the primary local Tailscale address,
and checks the effective kernel route (`ip route get`) to each tested peer. A route
must use the expected TUN; an internal TSMP response cannot mask a broken Linux route.
It then uses bounded TSMP pings to discovered literal addresses. DERP relay responses
are accepted; a direct path is not a health requirement.

Health also includes the managed `tailscaled.service` state, BackendState, kernel
TUN mode, the node's control-plane Online flag and the daemon's `Health` messages.
An empty Health list means no daemon-reported problem, not proof of end-to-end access.
Messages are retained verbatim as evidence, not regex-matched to decide reboots.
A reachable peer plus health/control/service warnings produces `degraded`, without
restarting an already working service or rearming episode budgets. Userspace mode
requires manual configuration for this kernel-based router setup.

IPv4/IPv6 forwarding values are recorded directly from `/proc`. Forwarding is not
required for every ordinary tailnet client; the daemon reports when routing roles
require it. A router warning such as disabled forwarding must remain visible even
if direct access to the Pi works. The probe never changes sysctl or announced routes.

For confirmed repeated failures, `tailscale netcheck --format=json` is attached as
bounded diagnostic evidence before recovery: UDP/IPv4/IPv6, captive portal indication
and reachable relay-region count. An unavailable netcheck does not erase the original
fault. UDP being blocked or a DERP path alone is not a reboot criterion. Control-plane
warnings may coexist with working cached peer connections; reachability and control
health are therefore reported separately.

- One peer responding proves a working tailnet path; other failed peers are reported.
- All discovered online peers failing is critical. Discovery and probes are repeated
  before a service restart. An unrecovered confirmed failure is eligible for escalation.
- No online peers, an exhausted probe time budget or login/authorization requirements
  are inconclusive/manual intervention, rather than evidence that reboot will help.
- These pings verify the encrypted Tailscale path, not remote SSH authentication or
  firewall permissions. A local TUN check is separate because TSMP bypasses host TUN.

SSH availability requires `ssh.service` active and an SSH protocol banner on the
configured local endpoint. Before any repair, `sshd -t` must pass. An invalid config
or missing host keys requires manual intervention; restarting the host cannot fix
those files. Successful configuration permits one restart and bounded rechecks.

The full healthcheck permits service recovery; standalone invocation defaults to
read-only. Neither mode changes Tailscale login, auth keys, routes, DNS preferences,
ACLs, SSH credentials or firewall rules. A tailscaled restart during real deployment
may briefly interrupt the remote connection; no such restart is needed for diagnosis.

Unrecovered SSH/Tailscale failures share **one host reboot request per unstable
episode**, controlled by `ACCESS_REBOOT_LIMIT=1`, with the same global threshold and
cooldown. This is one shared access budget, not one per peer or service. Container
and existing critical host policies remain separate. Version 1 and 2 recovery files upgrade without clearing existing budgets or reboot reservations.

Inconclusive observations pause stability and never rearm budgets. They produce
`status=unknown` without requesting reboot, while the timer remains operational.
A local successful check is not proof that another device can log in: independent
TCP/SSH monitoring from another tailnet node remains a separate deployment task.

Per-host local settings are `healthcheck_ssh_host` (default `127.0.0.1`),
`healthcheck_ssh_port` (22), and `healthcheck_tailscale_interface` (`tailscale0`).
Ansible validates them and writes `/etc/default/homelab-healthcheck`. These settings
are local endpoints only; tailnet peer discovery remains automatic.

## Implementation and files

- `homelab-healthcheck`: shell probes, service recovery and the only reboot call.
- `recovery.py`: standard-library Python policy, validation, global lock and atomic
  JSON replacement with file/directory fsync. It never executes reboot commands.
- `diagnostics.py`: bounded Docker log snapshots and report retention.
- `remote_access.py`: local SSH and automatically discovered tailnet reachability.
- `infrastructure.py`: local DNS, recursive DNS and verified NVMe mount probes.
- `/var/lib/homelab-healthcheck/recovery.json`: global state, mode 0600 inside a
  root-owned 0700 directory. This is outside `/run`, the NVMe and log2ram.

A reboot decision is reserved and persisted before writing diagnostics and invoking
systemctl. Further requests in that same boot are suppressed, including after a
failed systemctl call. This prevents races or repeated requests while shutdown is
pending. Corrupt/unreadable/unwritable state suppresses automatic reboots instead
of silently resetting the budget. Inspect and resolve the error manually.

Policy defaults are explicit constants near the top of the shell script:
`MAX_FAILURES`, `BOOT_COOLDOWN_SECONDS`, `MAX_REBOOT_COOLDOWN_SECONDS`,
`STABLE_OPERATION_SECONDS`, `MAX_HEALTHY_GAP_SECONDS`, `CONTAINER_REBOOT_LIMIT`,
`ACCESS_REBOOT_LIMIT`. The 10/20/30-minute cooldown values have not been changed.
The systemd unit creates the persistent state directory; the helper also supports
manual invocation when that unit has not created it yet. Python 3 is required;
no third-party Python packages are used.

## Diagnostics

Each unhealthy container, including non-critical ones, gets a snapshot before any
restart attempt. The legacy Docker-based Pi-hole recovery path also captures logs
before restarting that container. Snapshots request the last 200 timestamped log
lines and cap the complete file at 256 KiB, marking truncation and capture status.
Command failures are saved when possible. A snapshot failure is logged and does
not prevent recovery of the service.

Files are private (0600) under `/var/log/homelab-healthcheck/` (0700). Container
snapshots and reboot reports share a retention budget of **40 files / 10 MiB**;
oldest managed reports are removed first. Unrelated files and symlinks are not
removed. Root may retain evidence elsewhere before the budget rotates it out.
Suppression reports are written once at the failure threshold, rather than every
minute. Snapshot names are unique even if the wall clock repeats.
If log2ram or a volatile `/var/log` is enabled, report persistence depends on its
sync policy; recovery state in `/var/lib` remains independent.

## Deployment and operation

Set the expected NVMe filesystem UUID in the untracked group_vars/all.yml
before enabling the healthcheck. Read the UUID of the already mounted volume
with the command in group_vars/all.yml.example. The DNS probes expect working
host Pi-hole and Unbound installations. The default probes target
pihole.home.arpa and localhost respectively. Set install_homelab_healthcheck to
false until those prerequisites are available.
For an existing installation, stop the timer and any active run BEFORE provisioning:

```sh
sudo systemctl stop homelab-healthcheck.timer
sudo systemctl stop homelab-healthcheck.service
sudo ansible-playbook -i inventory.ini playbook.yml
```

The playbook installs the helpers and starts/enables the healthcheck timer only at
the end, after the stack and DNS tasks succeed. It does not automatically stop an
already-running old timer; the maintenance steps above are required on existing
hosts. If provisioning fails, leave the timer stopped until services are ready.

For a standalone manual installation, install the helpers before the shell
script. Before enabling the timer, create a root-managed /etc/default/homelab-healthcheck
with NVME_UUID, PIHOLE_PROBE_NAME, PIHOLE_PROBE_ADDRESS and UNBOUND_PROBE_PORT
set to the live mount and resolver configuration. Ansible writes this file when
using the playbook.

```sh
sudo install -d -m 0700 /var/lib/homelab-healthcheck
sudo install -m 0755 ops/healthcheck/recovery.py /usr/local/sbin/homelab-healthcheck-recovery
sudo install -m 0755 ops/healthcheck/diagnostics.py /usr/local/sbin/homelab-healthcheck-diagnostics
sudo install -m 0755 ops/healthcheck/remote_access.py /usr/local/sbin/homelab-healthcheck-access
sudo install -m 0755 ops/healthcheck/infrastructure.py /usr/local/sbin/homelab-healthcheck-infrastructure
sudo install -m 0755 ops/healthcheck/homelab-healthcheck /usr/local/sbin/homelab-healthcheck
sudo install -m 0644 ops/healthcheck/homelab-healthcheck.service /etc/systemd/system/
sudo install -m 0644 ops/healthcheck/homelab-healthcheck.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-healthcheck.timer
```

Inspect status and state without running recovery probes:

```sh
systemctl status homelab-healthcheck.timer
journalctl -t homelab-healthcheck -n 50 --no-pager
sudo /usr/local/sbin/homelab-healthcheck-recovery status
# Read-only (no --recover): actively probes SSH and discovered online peers.
sudo /usr/local/sbin/homelab-healthcheck-access
sudo ls -lah /var/log/homelab-healthcheck/
```

After investigating and resolving the fault, an operator may explicitly rearm the
budgets with `sudo /usr/local/sbin/homelab-healthcheck-recovery reset`. This is a
manual override; save the old state first if needed for diagnosis. The full
`homelab-healthcheck` command is active recovery, not a read-only status command.

## Tests

```sh
python3 -m unittest discover -s ops/healthcheck/tests -v
sh -n ops/healthcheck/homelab-healthcheck
shellcheck ops/healthcheck/homelab-healthcheck
```

The tests use temporary state/report directories, fake Docker/Tailscale/SSH probes and a
replacement for the single reboot command. They cover cross-boot budgets, clock
behavior, continuous stability, critical-check priority, bounded snapshots,
retention, DNS timeout and upstream scenarios, storage holds, file-write failures
and real concurrent state reservations. No test
runs the complete live healthcheck, restarts a real service or reboots the host.
The existing container scan still examines its entire snapshot. Its treatment of
`none` (no Docker healthcheck) is preserved; redesigning that policy is separate.
