# Homelab Healthcheck

Host-level recovery check for the Raspberry Pi stack.

The timer runs once per minute and calls `homelab-healthcheck`. The script reboots
the node after 3 consecutive failures across Docker, Node Exporter, Grafana,
Prometheus, the NVMe mount, or recent USB/storage kernel errors.

Before rebooting, it writes a persistent report under:

```text
/var/log/homelab-healthcheck/
```

## Install

```bash
sudo install -m 0755 ops/healthcheck/homelab-healthcheck /usr/local/sbin/homelab-healthcheck
sudo install -m 0644 ops/healthcheck/homelab-healthcheck.service /etc/systemd/system/homelab-healthcheck.service
sudo install -m 0644 ops/healthcheck/homelab-healthcheck.timer /etc/systemd/system/homelab-healthcheck.timer
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-healthcheck.timer
```

## Operate

```bash
systemctl status homelab-healthcheck.timer
journalctl -t homelab-healthcheck -n 50 --no-pager
sudo ls -lah /var/log/homelab-healthcheck/
sudo /usr/local/sbin/homelab-healthcheck
```

Disable it with:

```bash
sudo systemctl disable --now homelab-healthcheck.timer
```
