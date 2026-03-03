# Raspberry Pi Homelab – Secure Remote Network Architecture

## Overview

This repository documents my personal homelab network architecture built around a Raspberry Pi 2B acting as a permanent infrastructure node.

The system provides:

- Secure remote access to my home LAN
- Encrypted mesh networking between devices
- Centralized DNS filtering
- Exit-node routing for restricted networks
- Zero exposed services to the public internet

The goal was to design a minimal, secure, and practical home infrastructure without relying on port forwarding or publicly exposed services.

---

## Architecture Summary

Core components:

- **Raspberry Pi 2B** (always-on infrastructure node)
- **Tailscale** (WireGuard-based overlay VPN)
- **Pi-hole** (centralized DNS filtering)
- Subnet routing for `192.168.1.0/24`
- Exit node enabled selectively when required

### Design Principles

- No port forwarding on the home router
- Minimize attack surface
- Encrypt all traffic over untrusted networks
- Keep latency low by avoiding unnecessary full-tunnel routing
- Centralize DNS control

---

## Network Model

The Raspberry Pi performs multiple roles:

1. Subnet router for the local LAN (`192.168.1.0/24`)
2. Exit node for full internet routing (used only in restrictive environments)
3. DNS server via Pi-hole for all Tailscale-connected devices

### Traffic Behavior

- Normal usage: direct mesh connections via Tailscale
- Restricted networks (e.g., university Wi-Fi): full-tunnel through exit node
- DNS queries routed to Pi-hole for filtering and visibility

This ensures flexibility without permanently increasing latency.

---

## Why Overlay Instead of Port Forwarding?

Instead of exposing services through router port forwarding, I chose an overlay VPN model because it:

- Eliminates publicly exposed services
- Reduces exposure to automated internet scanning
- Maintains encrypted device-to-device communication
- Simplifies firewall management

This approach prioritizes security and architectural cleanliness over convenience.

---

## Threat Model (Simplified)

Primary concerns:

- Automated internet scans
- Unencrypted traffic over public networks
- Misconfigured open ports
- DNS-based tracking or malicious domains

Mitigation strategy:

- No open inbound ports
- Encrypted overlay network
- Centralized DNS filtering
- Controlled exit-node usage

---

## Technical Configuration

### Tailscale

- Installed on Raspberry Pi 2B
- Subnet routing enabled for `192.168.1.0/24`
- Exit node enabled
- MagicDNS active

### Pi-hole

- Running locally on Raspberry Pi
- Configured as DNS server for Tailscale devices
- Ad-blocking and domain filtering enabled
- Provides DNS-level traffic visibility

---

## Trade-offs and Limitations

- Raspberry Pi 2B has limited CPU and throughput
- Single point of failure
- Dependent on external control plane
- Not designed for high bandwidth workloads
- No high availability setup

This is a learning-focused infrastructure, not a production-grade HA system.

---

## Lessons Learned

- Overlay networking simplifies secure remote access
- Exit nodes should be used strategically to avoid unnecessary latency
- DNS centralization increases both control and observability
- Minimizing exposure is often more effective than adding complexity

---

## Future Improvements

- Monitoring stack (Prometheus + Grafana)
- Hardware upgrade for better throughput
- High-availability DNS
- Comparative implementation using self-hosted control plane (e.g., NetBird)
- Automated infrastructure documentation

---

## Project Intent

This project is part of my ongoing exploration of:

- Network architecture
- Overlay networking models
- Secure infrastructure design
- DevOps-oriented documentation practices

The objective is not just to deploy tools, but to understand system design decisions and their trade-offs.
