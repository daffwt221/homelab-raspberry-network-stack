#!/usr/bin/env bash
set -euo pipefail

sudo tailscale serve --bg --https=443  http://127.0.0.1:8080
sudo tailscale serve --bg --https=3000 http://127.0.0.1:3000
sudo tailscale serve --bg --https=9090 http://127.0.0.1:9090
sudo tailscale serve --bg --https=9443 http://127.0.0.1:9000

tailscale serve status
