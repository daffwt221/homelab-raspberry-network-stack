# Incident: Docker Containers Unresponsive
12-04-2026
## What happened

All Docker services (Grafana, Prometheus, Portainer) became unreachable simultaneously. Containers were marked as `Up` in `docker ps`, but no web interfaces were accessible and `curl localhost:<port>` failed across the board, ruling out DNS or external networking as the cause.

At the same time, SSH, Tailscale, and Pi-hole were all working normally, which isolated the issue to Docker itself.

## Likely cause

The Pi 2B has 1GB of RAM. Memory pressure and swap usage likely caused Docker to enter an inconsistent state, pcontainers alive on paper, but not actually functional. Internal networking or port binding may have silently broken under resource exhaustion.

## Resolution

Restarting Docker restored everything immediately:

```bash
sudo systemctl restart docker
```

All services became reachable again with no further intervention.

## Takeaway

A container showing as `Up` does not mean it is healthy or reachable. On constrained hardware like the Pi 2B, resource pressure can silently break container networking without killing the containers themselves. If services are unreachable but containers appear running, restarting Docker is the first thing to try.
To guarantee long-term usage, it is highly recommended to get recent and stable hardware.