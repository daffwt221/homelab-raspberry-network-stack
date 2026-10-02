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
```

Checks the operational scripts for common shell bugs, quoting mistakes,
undefined variables, unsafe command usage, and portability issues.

### YAML files

```bash
yamllint -d relaxed ...
```

Checks syntax and basic formatting for:

- `docker-compose.yml`
- Prometheus config
- Ansible playbook and example vars
- GitHub Actions workflows

The `relaxed` profile is used to avoid turning style preferences into noisy
build failures.

### Docker Compose

```bash
docker compose config --quiet
```

Parses and renders the Compose file. This catches invalid Compose syntax,
broken interpolation, malformed service definitions, and mount/command mistakes
before the stack is deployed.

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
