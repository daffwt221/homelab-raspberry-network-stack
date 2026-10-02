# Rive Healthcheck

Host-level recovery check for the Raspberry Pi stack.

The timer runs once per minute and calls `rive-healthcheck`. The script reboots
the node after 3 consecutive failures across Docker, Node Exporter, Grafana,
Prometheus, the NVMe mount, or recent USB/storage kernel errors.

Before rebooting, it writes a persistent report under:

```text
/var/log/rive-healthcheck/
```

## Install

```bash
sudo install -m 0755 ops/healthcheck/rive-healthcheck /usr/local/sbin/rive-healthcheck
sudo install -m 0644 ops/healthcheck/rive-healthcheck.service /etc/systemd/system/rive-healthcheck.service
sudo install -m 0644 ops/healthcheck/rive-healthcheck.timer /etc/systemd/system/rive-healthcheck.timer
sudo systemctl daemon-reload
sudo systemctl enable --now rive-healthcheck.timer
```

## Operate

```bash
systemctl status rive-healthcheck.timer
journalctl -t rive-healthcheck -n 50 --no-pager
sudo ls -lah /var/log/rive-healthcheck/
sudo /usr/local/sbin/rive-healthcheck
```

Disable it with:

```bash
sudo systemctl disable --now rive-healthcheck.timer
```
