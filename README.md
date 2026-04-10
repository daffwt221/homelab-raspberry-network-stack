# Raspberry Pi Homelab – Containerized Network & Services Stack

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

---

## Getting Started

All services are defined in `docker-compose.yml` and can be brought up with:

```bash
docker compose up -d
```

To include optional services (e.g. 4get):

```bash
docker compose --profile fourget up -d
```

> **Prerequisites:** Docker and Docker Compose installed. Prometheus configuration lives in `prometheus.yml` at the repo root, make sure it exists before starting.

Persistent data is stored in named Docker volumes. No manual permission setup required.

---

## Architecture

The Pi serves as subnet router, exit node, DNS server (Pi-hole), Docker host, and monitoring node.

![Homelab Architecture](diagrams/architecture.png)

All services run as Docker containers. No inbound ports are open. Remote access goes exclusively through Tailscale's encrypted overlay network.

---

## Monitoring

Metrics pipeline: `Node Exporter → Prometheus → Grafana`

Prometheus scrapes host-level metrics from Node Exporter at regular intervals. Grafana provides dashboards for tracking resource usage and identifying bottlenecks on constrained hardware.

---

## Network Behavior

**Normal operation:** Devices connect via Tailscale mesh. Traffic is peer-to-peer where possible. DNS queries go through Pi-hole.

**Restricted networks (e.g. university Wi-Fi):** Exit node is enabled, routing all traffic through the Pi. DNS filtering stays active.

---

## Design Rationale

No port forwarding, no public-facing services. The overlay VPN handles all remote access, which keeps the attack surface minimal. Docker provides service isolation and portability. Portainer handles container lifecycle. Prometheus + Grafana give visibility into system health.

---

## Threat Model

| Threat | Mitigation |
|---|---|
| Automated internet scans | No public inbound ports |
| Open port exposure | Overlay VPN (Tailscale) for all access |
| Unencrypted traffic on public Wi-Fi | Exit node + WireGuard encryption |
| DNS tracking / malicious domains | Pi-hole DNS filtering |
| Container breakout | Docker isolation + limited permissions |

---

## Limitations

- Pi 2B: constrained CPU and 1GB RAM
- USB 2.0 bottleneck for NVMe storage
- Single point of failure (no redundancy)
- Dependent on Tailscale's coordination server
- Not suitable for compute-heavy workloads

---

## TODO

- [ ] Container memory limits (`mem_limit` / `--memory`)
- [ ] Syncthing for automated photo backups
- [ ] Reverse proxy for internal service routing (Caddy / Traefik)
- [ ] NAS backup automation
- [ ] Hardware upgrade (Pi 4 / mini PC)
- [ ] Infrastructure as Code (Ansible / Docker Compose versioning)
