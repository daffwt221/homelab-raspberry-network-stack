# Swap Migration (SD → NVMe)

## Context

The Raspberry Pi 2B (1GB RAM) was experiencing system instability under load — DNS resolution failures, unresponsive containers, Tailscale disconnections, and eventual full system hangs requiring manual reboot.

## Root Cause

Grafana dashboards showed sustained high RAM usage with swap utilization reaching ~24%. Since swap was allocated on the SD card, the slow I/O throughput created a bottleneck under memory pressure.

![Grafana](https://raw.githubusercontent.com/daffwt221/homelab-raspberry-network-stack/main/img/grafana_monitor_18-03-2026.png)

System logs confirmed unclean filesystem unmounts and journal recovery on boot, consistent with swap-induced I/O stalls leading to system freezes.

Root cause: memory pressure → swap thrashing on SD card → I/O saturation → system hang.

## Fix

Migrated swap from SD card to NVMe-attached storage.

1. Disabled default SD-based swap:

```bash
sudo dphys-swapfile swapoff
sudo systemctl disable dphys-swapfile
```

2. Created a 512M swapfile on NVMe:

```bash
sudo fallocate -l 512M /mnt/nvme/swapfile
sudo chmod 600 /mnt/nvme/swapfile
sudo mkswap /mnt/nvme/swapfile
sudo swapon /mnt/nvme/swapfile
```

3. Persisted across reboots:

```bash
echo "/mnt/nvme/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
```

4. Reduced swappiness to minimize unnecessary swap usage:

```bash
# /etc/sysctl.conf
vm.swappiness=10
```

## Result

Swap usage dropped significantly post-migration. System remains stable under normal workload, with no further unresponsive episodes or unclean shutdowns. SD card I/O load reduced.

## Takeaways

- 1GB of RAM is tight when running multiple containers, swap placement matters a lot
- SD card swap is unusable under sustained memory pressure due to I/O limitations
- Grafana + Prometheus monitoring was critical for identifying the root cause

## TODO

- Set memory limits on Docker containers (`--memory` / `mem_limit`)
- Reduce Prometheus scrape intervals to lower resource consumption
- Evaluate hardware upgrade (Pi 4 / mini PC) for long-term scalability
