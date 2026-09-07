## GitHub Actions Metrics Pipeline

The repository uses GitHub Actions to securely collect metrics from the Raspberry Pi and automatically update the monitoring badges.

The workflow runs every 10 minutes and follows this path:

```text
┌─────────────────────────────┐
│       GitHub Actions        │
│       Ubuntu Runner         │
└──────────────┬──────────────┘
               │
               │ 1. Install Tailscale
               │ 2. Join private tailnet
               ▼
┌─────────────────────────────┐
│        Tailscale VPN        │
│       WireGuard tunnel      │
└──────────────┬──────────────┘
               │
               │ Internal DNS query:
               │ node-exporter.home.arpa
               ▼
┌─────────────────────────────┐
│          Pi-hole            │
│     Internal DNS Server     │
│                             │
│ home.arpa → 100.70.214.76   │
└──────────────┬──────────────┘
               │
               │ HTTPS request
               ▼
┌─────────────────────────────┐
│           Caddy             │
│   Internal HTTPS Gateway    │
│                             │
│ TLS verification using      │
│ internal CA (root.crt)      │
└──────────────┬──────────────┘
               │
               │ Reverse proxy
               ▼
┌─────────────────────────────┐
│       Node Exporter         │
│                             │
│     :9100/metrics           │
└──────────────┬──────────────┘
               │
               │ Parse metrics
               ▼
┌─────────────────────────────┐
│      Metrics Generator      │
│                             │
│  cpu.json                   │
│  ram.json                   │
│  updated.json               │
└──────────────┬──────────────┘
               │
               │ git commit
               ▼
┌─────────────────────────────┐
│       GitHub Repository     │
│                             │
│       Badge data updated    │
└─────────────────────────────┘
