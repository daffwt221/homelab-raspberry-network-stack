# Incident: NVMe USB Power Instability
29-09-2026

## What happened

The Raspberry Pi stopped responding through Tailscale and the monitoring stack became unavailable. GitHub Actions started failing because the workflow could no longer curl Node Exporter metrics from the Pi.

Symptoms reported during the incident:

- Node Exporter metrics were unreachable
- Docker-backed services, including Grafana, were unavailable
- SSH was unavailable
- Tailscale peer connectivity became unreliable

The affected journal window was reviewed from `2026-09-29 13:51` until midnight. The previous boot stopped logging at `2026-09-29 19:19:31 BST` without a normal shutdown sequence. The next recorded boot was on `2026-10-02 12:17:01 BST`.

## Evidence

The next boot showed signs of an unclean shutdown:

```text
system.journal corrupted or uncleanly shut down
Dirty bit is set. Fs was not properly unmounted
NAS: recovering journal
```

No clear OOM killer event, kernel panic, or explicit RAM exhaustion event was found in the journal.

Tailscale logs included a Go runtime crash with `SIGBUS`, which is consistent with an invalid memory-mapped file or storage/I/O becoming unavailable underneath a running process.

SMART data for the USB-attached NVMe showed the drive itself was healthy, but had a very high unsafe shutdown count:

```text
SMART overall-health self-assessment test result: PASSED
Percentage Used: 36%
Media and Data Integrity Errors: 0
Error Information Log Entries: 0
Power Cycles: 9186
Unsafe Shutdowns: 7676
```

The disk is an SK hynix PC401 NVMe behind a Realtek RTL9210 USB bridge. The NVMe reports a maximum power state of 6.00W, which is a large load for an older Raspberry Pi USB power path.

## Likely cause

The most likely root cause is unstable power or USB storage behavior around the NVMe bridge, not RAM pressure.

Root cause chain:

```text
NVMe/USB power or bridge instability
-> storage or I/O becomes unreliable
-> running processes hit I/O failures or SIGBUS
-> Docker, SSH, Tailscale, and monitoring become unavailable
-> filesystem and journal recover on next boot
```

The NVMe is not considered failed based on the available SMART data. The issue is more likely the power path, USB bridge behavior, heat, or a hard system freeze that prevents clean shutdown.

## Mitigations applied

USB autosuspend was disabled persistently:

```text
/boot/firmware/cmdline.txt
usbcore.autosuspend=-1
```

Current runtime value can be checked with:

```bash
cat /sys/module/usbcore/parameters/autosuspend
```

Expected output:

```text
-1
```

The hardware watchdog remains enabled, but it did not catch this incident because the system can still feed the watchdog while Docker, Tailscale, or storage are partially broken.

A stack-level healthcheck was added as a systemd timer:

```text
/usr/local/sbin/rive-healthcheck
/etc/systemd/system/rive-healthcheck.service
/etc/systemd/system/rive-healthcheck.timer
```

The timer checks once per minute:

- Docker daemon responsiveness
- Node Exporter metrics on `127.0.0.1:9100`
- Grafana health on `127.0.0.1:3000`
- Prometheus health on `127.0.0.1:9090`
- `/mnt/nvme` mount presence
- recent kernel logs for USB, storage, I/O, or EXT4 errors

It reboots only after 3 consecutive failed checks.

## Operations

Check healthcheck timer status:

```bash
systemctl status rive-healthcheck.timer
```

View healthcheck logs:

```bash
journalctl -t rive-healthcheck -n 50 --no-pager
```

Run the healthcheck manually:

```bash
sudo /usr/local/sbin/rive-healthcheck
```

Disable the healthcheck timer:

```bash
sudo systemctl disable --now rive-healthcheck.timer
```

Track whether unsafe shutdowns keep increasing:

```bash
sudo smartctl -a -T permissive /dev/sda | egrep 'Power Cycles|Unsafe Shutdowns|Media and Data|Error Information|Temperature|Percentage Used'
```

## Remaining risk

The software mitigations reduce the chance of USB power-state issues and improve automatic recovery, but they do not fully solve an electrical or bridge-level failure.

Recommended hardware follow-up:

- use a powered USB hub or externally powered NVMe enclosure
- improve NVMe cooling
- monitor whether `Unsafe Shutdowns` continues to increase
- keep backups of data stored under `/mnt/nvme`

## Takeaways

- The first visible failure may be monitoring, but the root cause can be below Docker
- A healthy SMART status does not rule out power or USB bridge instability
- Hardware watchdogs are useful, but they do not catch every partial failure mode
- A stack-level healthcheck can recover from failures where the kernel is alive but services are unusable
