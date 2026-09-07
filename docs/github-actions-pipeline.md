## GitHub Actions Metrics Pipeline

The repository uses GitHub Actions to securely collect metrics from the Raspberry Pi and automatically update monitoring badges.

The workflow runs every 10 minutes and temporarily connects the GitHub runner to the private homelab network through Tailscale.

The complete data flow:

```text
┌─────────────────────────────┐
│       GitHub Actions        │
│       Ubuntu Runner         │
│                             │
│  Scheduled workflow         │
│  (every 10 minutes)         │
└──────────────┬──────────────┘
               │
               │ Authenticate with Tailscale
               │ Create private WireGuard tunnel
               ▼
┌─────────────────────────────┐
│        Tailscale VPN        │
│                             │
│      Private tailnet        │
│      Encrypted transport    │
└──────────────┬──────────────┘
               │
               │ DNS query:
               │ node-exporter.home.arpa
               ▼
┌─────────────────────────────┐
│          Pi-hole            │
│                             │
│      Internal DNS           │
│                             │
│ home.arpa                   │
│        ↓                    │
│ 100.70.214.76               │
└──────────────┬──────────────┘
               │
               │ HTTPS request
               │ https://node-exporter.home.arpa/metrics
               ▼
┌─────────────────────────────┐
│           Caddy             │
│                             │
│ Internal HTTPS Gateway      │
│                             │
│ TLS termination             │
│ Certificate validation      │
│ using internal CA           │
│ (caddy/root.crt)            │
└──────────────┬──────────────┘
               │
               │ Reverse proxy
               ▼
┌─────────────────────────────┐
│       Node Exporter         │
│                             │
│       :9100/metrics         │
│                             │
│ Host metrics source         │
└──────────────┬──────────────┘
               │
               │ Parse Prometheus metrics
               ▼
┌─────────────────────────────┐
│      Metrics Generator      │
│                             │
│ Calculates:                 │
│  - CPU usage                │
│  - RAM usage                │
│                             │
│ Generates:                  │
│  cpu.json                   │
│  ram.json                   │
│  updated.json               │
└──────────────┬──────────────┘
               │
               │ Commit generated files
               ▼
┌─────────────────────────────┐
│       GitHub Repository     │
│                             │
│       Badge data updated    │
└─────────────────────────────┘
```

### Security model

The metrics endpoint is never exposed publicly.

The workflow relies on multiple private layers:

```text
GitHub Actions
        |
        | Encrypted WireGuard tunnel
        |
    Tailscale
        |
        | Internal DNS resolution
        |
    Pi-hole
        |
        | Verified HTTPS connection
        |
    Caddy
        |
        | Reverse proxy
        |
 Node Exporter
```

TLS verification remains enabled. The GitHub runner trusts the internal Caddy Certificate Authority by installing the public root certificate:

```text
caddy/root.crt
        |
        ▼
Ubuntu CA trust store
        |
        ▼
HTTPS certificate verification
```

Private CA keys never leave the Raspberry Pi.
```
