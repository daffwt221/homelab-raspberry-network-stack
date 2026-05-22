<p align="center">
  <img src="img/logo/logo2.png" alt="Homelab Logo" width="340"/>
</p>

<h1 align="center">Homelab Stack</h1>

<p align="center">
  <strong>Containerized Network & Monitoring Stack on Low-Resource Hardware</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/daffwt221/homelab-raspberry-network-stack/main/cpu.json&cacheSeconds=60" />
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/daffwt221/homelab-raspberry-network-stack/main/ram.json&cacheSeconds=60" />
  <img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/daffwt221/homelab-raspberry-network-stack/main/updated.json&cacheSeconds=60" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Hardware-Raspberry%20Pi%202B-red" />
  <img src="https://img.shields.io/badge/Docker-Enabled-blue" />
  <img src="https://img.shields.io/badge/Monitoring-Prometheus%20%2B%20Grafana-green" />
  <img src="https://img.shields.io/badge/Network-Tailscale-purple" />
  <img src="https://img.shields.io/badge/Storage-NVMe-critical" />
</p>

<p align="center">
  <a href="#overview">Overview</a> •
  <a href="#stack">Stack</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#monitoring">Monitoring</a> •
  <a href="#design-rationale">Design</a> •
  <a href="#threat-model">Security</a>
</p>

---

## Overview

Homelab built on a Raspberry Pi 2B running as an always-on infrastructure node. The Pi handles DNS filtering, overlay networking, containerized services, lightweight NAS storage, and infrastructure monitoring, all without exposing any public ports.

---

## Stack

### Hardware

- Raspberry Pi 2B (1GB RAM, always-on)
- NVMe drive (container volumes and persistent data)

### Networking

- Tailscale (WireGuard-based mesh VPN)
- Subnet router for `192.168.1.0/24`
- Exit node capability for full-tunnel routing

### Containers

- Docker
- Portainer (management UI)

### Monitoring

- Prometheus + Node Exporter → Grafana
- Tracks CPU, memory, disk, network, and load metrics

### Services

- Pi-hole (DNS filtering + query logging)
- [4get](https://git.lolcat.ca/lolcat/4get) (self-hosted search frontend)

### Resilience

- log2ram (reduces SD card writes by buffering logs in RAM)
- Watchdog (automatic reboot on system hang)

### Provisioning

- Ansible (automated setup and deployment)

---

## Incidents & Troubleshooting

| Incident | Root Cause | Doc |
|---|---|---|
| System instability, DNS failures, container hangs | Swap thrashing on SD card under memory pressure | [swap-migration.md](docs/troubleshooting/swap-migration.md) |
| Docker containers unresponsive despite showing as Up | Memory pressure causing inconsistent Docker state | [docker-unresponsive-incident.md](docs/troubleshooting/docker-unresponsive-incident.md) |

---

## Getting Started

### Configuration

Before deploying, create your local Ansible config from the example:

```bash
cp group_vars/all.yml.example group_vars/all.yml
```

Then edit `group_vars/all.yml` to match your setup:

```yaml
user: pi
compose_path: /home/pi/homelab-stack
compose_file: docker-compose.yml

docker_data_root: /mnt/nvme/docker
service_bind_address: tailscale

tailscale_authkey: XXXXX
tailscale_hostname: homelab-pi
tailscale_advertise_routes: 192.168.1.0/24
tailscale_advertise_exit_node: true
```

You can generate a Tailscale auth key at [login.tailscale.com/admin/settings/keys](https://login.tailscale.com/admin/settings/keys).
The auth key can also be supplied with the `TAILSCALE_AUTHKEY` environment variable for the first provisioning run.

By default, the playbook resolves the node's Tailscale IPv4 address and binds the Docker-published services to that address only. This keeps access inside the tailnet and avoids router port forwarding entirely.

Prometheus config and alert rules live in `prometheus/` and are copied by the playbook.

Container data is stored under `docker_data_root`, so Grafana, Prometheus, and Portainer state can live on NVMe instead of the SD card. Volume permissions for Grafana and Prometheus are set automatically by the playbook. No manual `chown` required.

Memory limits are set per container in `docker-compose.yml` and tuned for the Pi 2B (1GB RAM). Adjust `mem_limit` values if running on different hardware.

The 4get scraper service is behind the optional Compose profile. To include it, run Compose with `--profile optional`.

### Automated provisioning (Ansible)

Once configured, provision and deploy everything with a single command:

```bash
ansible-playbook -i inventory.ini playbook.yml
```

The playbook handles installing Docker, Docker Compose, Tailscale, and Samba; authenticating the node; enabling IPv4 forwarding; advertising the subnet route and exit node; configuring log2ram and the hardware watchdog; copying Prometheus configuration; and deploying the container stack automatically.

After the first Tailscale run, approve the advertised subnet route and exit node in the Tailscale admin console if required by your tailnet policy.

### Manual deployment

If Docker is already set up, bring up the stack directly:

```bash
cp .env.example .env
docker compose up -d
```

For manual deployment, set `SERVICE_BIND_ADDRESS` in `.env` to the output of `tailscale ip -4` if you want Tailscale-only access. Persistent data is stored under `DOCKER_DATA_ROOT` from `.env`.

---

## Architecture

The Pi serves as subnet router, exit node, DNS server (Pi-hole), Docker host, and monitoring node.

```
                      ┌─────────────────────┐
                      │      Internet       │
                      └──────────┬──────────┘
                                 │
                      ┌──────────▼──────────┐
                      │     Tailscale       │
                      │   (WireGuard VPN)   │
                      └──────────┬──────────┘
                                 │
          ┌──────────────────────▼──────────────────────┐
          │              Raspberry Pi 2B                │
          │                                             │
          │  ┌────────────── Docker ───────────────┐    │
          │  │  Grafana        (dashboards)        │    │
          │  │  Prometheus     (metrics)           │    │
          │  │  Node Exporter  (host metrics)      │    │
          │  │  Portainer      (container mgmt)    │    │
          │  │  4get           (search frontend)   │    │
          │  └─────────────────────────────────────┘    │
          │                                             │
          │  Pi-hole + Unbound  (DNS / ad-blocking)     │
          │  Samba              (NAS)                   │
          │  log2ram            (SD card protection)    │
          │  Watchdog           (auto-reboot on hang)   │
          │                                             │
          │  Storage: SD card (OS) + NVMe (data/swap)   │
          └──────────────────┬──────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │    Home LAN     │
                    │ 192.168.1.0/24  │
                    └─────────────────┘

Clients (via Tailscale mesh):
  - Laptop
  - Phone
  - Restricted network  →  exit node routing
```

All services run as Docker containers. No inbound ports are open. Remote access goes exclusively through Tailscale's encrypted overlay network.

---

## Monitoring

Metrics pipeline: `Node Exporter → Prometheus → Grafana`

Prometheus scrapes host-level metrics from Node Exporter at regular intervals. Grafana provides dashboards for tracking resource usage and identifying bottlenecks on constrained hardware.

Basic Prometheus alert rules are included for unreachable Node Exporter, high memory usage, swap usage, low disk space, and sustained load.

---

## Network Behavior

**Normal operation:** Devices connect via Tailscale mesh. Traffic is peer-to-peer where possible. DNS queries go through Pi-hole.

**Restricted networks (e.g. university Wi-Fi):** Exit node is enabled, routing all traffic through the Pi. DNS filtering stays active.

No router port forwarding is required or expected. Remote access is handled through Tailscale, and container ports are bound to the Tailscale address by default when deployed with Ansible.

---

## Design Rationale

No port forwarding, no public-facing services. The overlay VPN handles all remote access, which keeps the attack surface minimal. Docker provides service isolation and portability. Portainer handles container lifecycle. Prometheus + Grafana give visibility into system health. log2ram reduces SD card wear, and the hardware watchdog ensures automatic recovery from hangs. Ansible ensures the whole setup is reproducible and version-controlled.

---

## Threat Model

| Threat | Mitigation |
|---|---|
| Automated internet scans | No public inbound ports |
| Open port exposure | Overlay VPN (Tailscale) for all access |
| Unencrypted traffic on public Wi-Fi | Exit node + WireGuard encryption |
| DNS tracking / malicious domains | Pi-hole DNS filtering |
| Container breakout | Docker isolation + limited permissions |
| Management plane exposure | Bind services to the Tailscale IP via `SERVICE_BIND_ADDRESS` |

---

## Limitations

- Pi 2B: constrained CPU and 1GB RAM
- USB 2.0 bottleneck for NVMe storage
- Single point of failure (no redundancy)
- Dependent on Tailscale's coordination server
- Not suitable for compute-heavy workloads
- Portainer requires Docker socket access, which should be treated as highly privileged

---

## TODO

- [x] Container memory limits (`mem_limit` / `--memory`)
- [x] NVMe-backed container data paths
- [x] Prometheus alert rules
- [x] Tailscale subnet route / exit node provisioning
- [ ] Syncthing for automated photo backups
- [ ] Reverse proxy for internal service routing over Tailscale (Caddy / Traefik)
- [ ] NAS backup automation
- [ ] Expand homelab with an additional node (offload heavy services)
- [x] Infrastructure as Code (Ansible / Docker Compose versioning)
