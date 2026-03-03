# Raspberry Pi Homelab – Containerized Network & Services Stack

## Overview

This repository documents my personal homelab architecture built around a Raspberry Pi 2B acting as a permanent infrastructure node.

The system provides:

- Secure remote access to my home LAN
- Containerized self-hosted services
- Centralized DNS filtering
- Lightweight NAS functionality
- Overlay networking without exposing public ports

The goal was to design a secure, minimal, and practical home infrastructure using containerization while avoiding direct internet exposure.

---

## Core Components

### Hardware

- Raspberry Pi 2B (always-on node)

### Networking

- Tailscale (WireGuard-based overlay VPN)
- Subnet routing for `192.168.1.0/24`
- Exit node (used selectively in restricted networks)

### Containerization

- Docker (service orchestration)
- Portainer (container management interface)

### Services

- Pi-hole (DNS filtering and network visibility)
- Samba (lightweight NAS functionality)
- 4get scraper (self-hosted privacy-focused search frontend)

---

## Architecture Model

The Raspberry Pi acts as:

- Subnet router for local LAN
- Exit node (on-demand full tunnel routing)
- DNS authority (Pi-hole)
- Container host (Docker)
- File sharing server (Samba)

All services run inside Docker containers except low-level networking components.

No inbound ports are exposed to the public internet.

Remote access is achieved exclusively through encrypted overlay networking.

---

## Network Behavior

### Normal Operation

Devices connect via Tailscale mesh.
Traffic remains peer-to-peer when possible.
DNS queries are routed to Pi-hole.

### Restricted Networks (e.g. university Wi-Fi)

Exit node is enabled.
All traffic is tunneled through the Raspberry Pi.
DNS continues to be filtered via Pi-hole.

---

## Why This Design?

Instead of exposing services through port forwarding, this architecture:

- Avoids public-facing services
- Reduces attack surface
- Keeps infrastructure private
- Centralizes service management
- Uses containerization for isolation and portability

Docker ensures services are modular and replaceable.
Portainer simplifies container lifecycle management.

---

## Threat Model (Simplified)

Primary concerns:

- Automated internet scans
- Open port exposure
- Unencrypted traffic on public Wi-Fi
- DNS-level tracking or malicious domains

Mitigation strategy:

- No public inbound ports
- Overlay VPN for all remote access
- Centralized DNS filtering
- Container isolation

---

## Trade-offs and Limitations

- Raspberry Pi 2B has limited CPU and RAM
- Single point of failure
- Dependent on external VPN control plane
- Not designed for high-performance workloads
- No high-availability setup

This environment prioritizes learning and architectural understanding over performance.

---

## Lessons Learned

- Overlay networking simplifies secure remote access
- Containerization improves service modularity
- DNS centralization increases observability
- Minimizing exposure is often more effective than adding complexity
- Exit nodes should be used strategically to avoid unnecessary latency

---

## Future Improvements

- Monitoring stack (Prometheus + Grafana)
- Hardware upgrade
- Backup automation for NAS data
- High-availability DNS
- Comparative self-hosted control plane implementation
- Infrastructure as Code approach for reproducibility

---

## Project Intent

This project focuses on understanding:

- Overlay networking architecture
- Container-based service design
- Security-first infrastructure decisions
- Trade-off analysis in constrained hardware environments

The objective is not just to run services, but to design and document a small-scale infrastructure with clear architectural reasoning.
