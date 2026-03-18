# Swap Migration (SD → NVMe)

## Context

The Raspberry Pi (2B, 1GB RAM) was experiencing severe slowdowns and eventual crashes.

### Observed symptoms:

- Services (DNS, scraper) wasbecoming unresponsive
- Very High latency across containerized services
- Tailscale disconnecting after a delay
- System required manual reboot

---

## Root Cause Analysis

Monitoring (Grafana) showed:

- High RAM usage
- Swap usage reaching ~24%
- System heavily relying on swap memory

![Grafana Memory Basic Monitoring](../img/grafana_monitor_18-03-2026.png)

System logs confirmed:

- Filesystem not properly unmounted
- Journal recovery on boot

### Conclusion:

The system was under **memory pressure**, causing:

- Swap usage on SD card (very slow I/O)
- I/O bottlenecks
- System freeze / crash

---

## Problem

Swap was configured on the SD card:

- Very slow
- Causes high latency under memory pressure
- Leads to system instability

---

## Approached Solution

Moved swap from SD card to NVMe storage, by

1. Disabling default swap:

```bash
sudo dphys-swapfile swapoff
sudo systemctl disable dphys-swapfile
```

2. Creating swapfile on external NVMe:

```bash
sudo fallocate -l 512M /mnt/nvme/swapfile
sudo chmod 600 /mnt/nvme/swapfile
sudo mkswap /mnt/nvme/swapfile
```

3. Enabling swap:

```bash
sudo swapon /mnt/nvme/swapfile
```

4. Persist configuration:

```bash
echo "/mnt/nvme/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
```

---

## Optimization

Reduced swap aggressiveness:

```bash
vm.swappiness=10
```

---

## Observed Result

- Swap usage significantly reduced
- System responsiveness improved
- No more crashes under normal load
- SD card no longer under heavy I/O stress

---

## Lessons Learned

- Raspberry Pi 2B has limited RAM (1GB)
- Swap on SD card is a major bottleneck
- Monitoring (Grafana + Prometheus) is essential
- Infrastructure decisions must match hardware limitations

---

## Future Improvements

- Limit Docker container memory usage
- Optimize Prometheus scrape intervals
- Consider homelab hardware upgrade (Pi 4 / mini PC)
