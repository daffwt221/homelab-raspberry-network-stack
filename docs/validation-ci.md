# Validation CI

The `Validate` workflow lives at:

```text
.github/workflows/validate.yml
```

It runs on pull requests and on pushes to `main`. Its job is to catch broken
infrastructure configuration before it is merged or deployed.

## What It Checks

### Shell scripts

```bash
shellcheck ops/healthcheck/homelab-healthcheck
shellcheck ops/thermal-monitor/homelab-thermal-monitor
shellcheck ops/photo-backup/entrypoint.sh
```

Checks the operational scripts for common shell bugs, quoting mistakes,
undefined variables, unsafe command usage, and portability issues.

### YAML files

```bash
yamllint -c .yamllint ...
```

Checks syntax and basic formatting for:

- `docker-compose.yml`
- Prometheus config
- Ansible playbook and example vars
- Photo backup provisioning and deployment tasks
- GitHub Actions workflows

The repository's `.yamllint` configuration controls the formatting rules.

### Docker Compose

```bash
docker compose config --quiet
```

Parses and renders the Compose file. This catches invalid Compose syntax,
broken interpolation, malformed service definitions, and mount/command mistakes
before the stack is deployed.

### Photo upload behavior and data safety

The workflow runs 12 unit tests against temporary fixtures, including the recovered
UID/GID migration, refusal of symlinks and unexpected owners, and independent-copy
guards. A native Docker test uses disposable keys, an inbox and a random localhost
port to verify SFTP upload/download checksums after container recreation, persistent
host identity, unknown-key rejection, chroot isolation and shell/password restrictions.
It never mounts production photos. Real phone connectivity and physical Pi runtime
still require manual deployment and verification.

### Photo SFTP ARMv7 image

The workflow sets up QEMU and Buildx, then builds the photo SFTP image with
`--platform linux/arm/v7`. The image build installs OpenSSH and validates its
configuration with `sshd -t`. This catches build failures for the Pi's
architecture; it does not test phone authentication or actual uploads.

### Ansible

```bash
ansible-playbook -i inventory.ini playbook.yml --syntax-check
```

Checks that the playbook is syntactically valid and that Ansible can parse all
tasks, handlers, variables, and module arguments.

It does not connect to the Raspberry Pi or make changes. It is a static syntax
check only.

### Prometheus

```bash
promtool check config /etc/prometheus/prometheus.yml
```

Runs Prometheus' own config validator inside the pinned Prometheus container.
This catches invalid Prometheus config and rule file references using the same
Prometheus version used by the stack.

## What It Does Not Do

The workflow does not:

- deploy to the Raspberry Pi
- connect to Tailscale
- restart containers
- verify live service health
- test alert behavior against real metrics

Those are runtime checks handled by the homelab itself through systemd timers,
Prometheus, Node Exporter, and the healthcheck scripts.

## Why It Exists

This repository contains infrastructure code, not just application code. A
small syntax error in Compose, Ansible, Prometheus, or shell scripts can break
monitoring or recovery on the Pi.

The validation workflow gives every pull request a basic safety gate:

```text
configuration parses
scripts lint
playbook parses
Prometheus config is valid
```

If this workflow is green, the change is not guaranteed to be correct, but it
has passed the first layer of operational sanity checks.
