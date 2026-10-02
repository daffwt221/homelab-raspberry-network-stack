# Homelab Thermal Monitor

Logs Raspberry Pi and NVMe temperature/storage health samples and exports them
to Prometheus through Node Exporter's textfile collector.

Runtime paths:

```text
/var/log/homelab-thermal/thermal.log
/var/lib/node_exporter/textfile/homelab_thermal.prom
```

Install:

```bash
sudo install -m 0755 ops/thermal-monitor/homelab-thermal-monitor /usr/local/sbin/homelab-thermal-monitor
sudo install -m 0644 ops/thermal-monitor/homelab-thermal-monitor.service /etc/systemd/system/homelab-thermal-monitor.service
sudo install -m 0644 ops/thermal-monitor/homelab-thermal-monitor.timer /etc/systemd/system/homelab-thermal-monitor.timer
sudo systemctl daemon-reload
sudo systemctl enable --now homelab-thermal-monitor.timer
```

Operate:

```bash
systemctl status homelab-thermal-monitor.timer
journalctl -t homelab-thermal-monitor -n 50 --no-pager
sudo tail -n 20 /var/log/homelab-thermal/thermal.log
cat /var/lib/node_exporter/textfile/homelab_thermal.prom
```
