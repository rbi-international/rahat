#!/usr/bin/env bash
# Point Prometheus at a RAHAT instance and start Prometheus + Grafana.
# Usage: ./infra/observability.sh <host[:port]> <metrics-token>
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && (pwd -W 2>/dev/null || pwd))"  # pwd -W: a Windows path Docker Desktop understands
TARGET="${1:?host of the RAHAT app, e.g. rahat.<region>.azurecontainerapps.io}"
TOKEN="${2:-}"
SCHEME=https
case "$TARGET" in localhost*|127.*|host.docker.internal*) SCHEME=http ;; esac
printf '%s' "$TOKEN" > "$HERE/prometheus/token"
cat > "$HERE/prometheus/prometheus.yml" <<YML
global:
  scrape_interval: 5s
scrape_configs:
  - job_name: rahat
    scheme: $SCHEME
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/token
    static_configs:
      - targets: ["$TARGET"]
YML
docker compose -f "$HERE/docker-compose.observability.yml" up -d
echo "Grafana: http://localhost:3000   Prometheus: http://localhost:9090"
