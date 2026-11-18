# Pi-hole and Unbound provisioning

Run locally on Raspberry Pi OS Bookworm/Trixie (Python 3.11+). The `pihole`
tag prepares Unbound, installs/repairs Pi-hole if needed, configures DNS and web
settings, enables the required blocklists, and checks the running services.

## Desired state recovered from the original SD

`defaults.yml` reproduces all nonsecret changed Pi-hole settings: six home.arpa
names, eth0, listeningMode ALL, web port 8081o, HTTP headers and darker theme.
The names use the discovered Tailscale IP rather than a hardcoded old address.
Unbound binds only to 127.0.0.1:5335; the template reproduces the original IPv4,
UDP/TCP, privacy and cache settings. The Debian package supplies trust-anchor
configuration. Unbound is tested before Pi-hole is pointed at it.
The tasks preserve an existing root.key, bootstrap one only when absent, and
correct state-directory/key ownership by account name after an SD restoration.

Override settings with the variables illustrated in `group_vars/all.yml.example`.
`pihole_dns_names` owns the entire dns.hosts list: omitted names are removed.
Other TOML keys, including the admin password, are preserved. No password is
embedded in the repository; set a new password separately for a fresh system.
The existing password hash in recovered config is left unchanged.

## Installation and application order

The official installer supplies Core, web files and the architecture-specific FTL
binary. The installation guard checks expected files and whether FTL runs, not
full service health. `pihole_repair_installation: true` forces the installer;
`pihole_installer_version` selects its checkout, not a complete release lock.
Installer execution may start/restart services. A failed install retains its
temporary checkout for diagnosis; a successful one removes that checkout only.

The sequence is Unbound configuration/check/start/probe, Pi-hole installation,
owned TOML settings, conditional FTL restart/start, adlists, gravity, DNS and HTTP
probes. It does not change the gateway or the Tailscale control-plane DNS setting.
Keep DNS override disabled until an intentional deployment passes those probes.

## Blocklists and state preservation

The original SD had one enabled source: StevenBlack. There were no individual
allow/deny exceptions or extra groups. `manage.py` ensures configured deny-list
URLs are enabled and assigned to Default. It preserves additional administrator
lists, groups, clients and exceptions; removing a URL from the desired list does
not delete it from the database. This additive policy deliberately avoids losing
future manual additions. Remove obsolete lists explicitly through Pi-hole.

The helper uses parameterized transactions against the documented adlist tables
because provisioning must also work without an API password. It refuses to
create an absent gravity.db or operate on an unexpected schema. Before changes,
SQLite's backup API saves committed DB state (including WAL) as
`gravity.db.homelab-before-adlists.bak`, mode 0600. This is a pre-change snapshot,
not an ongoing backup policy. It never copies an old database over the live one.
`pihole -g` runs after a list change or if gravity is empty. Download failures fail
deployment. The project helper is only for this local provisioning workflow.

## Review and verification

```sh
ansible-playbook -i inventory.ini playbook.yml --syntax-check
python3 -m unittest discover -s ops/pihole/tests -v
ansible-playbook -i inventory.ini playbook.yml --tags pihole --check
```

Check mode reads the current Pi-hole config/database, reports predicted settings
and adlist changes, and skips installer, Unbound changes, service operations,
gravity downloads and network probes. It cannot prove a clean install succeeds.

After review, an intentional deployment is:

```sh
ansible-playbook -i inventory.ini playbook.yml --tags pihole
```

This command changes the system and starts services. The included probes check
every local name, recursive external resolution and the local admin page.
Caddy's existing route targets localhost:8081. External HTTPS access through
Caddy, certificate trust, and Tailscale/gateway policies need a client-side test.
ALL listening reproduces the original LAN/VPN setup; network exposure controls
remain the responsibility of the host/network configuration.
