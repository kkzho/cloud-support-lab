#!/usr/bin/env bash
# Pull every drill image through a China-friendly mirror and retag to the
# canonical name expected by docker-compose.yml. Use this when Docker Hub
# (registry-1.docker.io) times out even after configuring registry-mirrors.
#
# Usage:
#   MIRROR=https://docker.m.daocloud.io bash scripts/pull-images.sh
#   # then:  docker compose up -d
set -euo pipefail

MIRROR="${MIRROR:-https://docker.m.daocloud.io}"
# strip trailing slash and scheme prefix for the docker pull argument
MIRROR_HOST="${MIRROR#https://}"
MIRROR_HOST="${MIRROR_HOST#http://}"
MIRROR_HOST="${MIRROR_HOST%/}"

# Canonical images referenced by docker-compose.yml (excludes the locally
# built `app` service, but INCLUDES its python base image so `docker compose
# build` also succeeds without touching Docker Hub directly).
IMAGES=(
  "python:3.12-slim"
  "nginx:1.27-alpine"
  "redis:7-alpine"
  "mysql:8.0"
  "prom/prometheus:v2.54.1"
  "prom/alertmanager:v0.27.0"
  "grafana/grafana:11.2.0"
  "nginx/nginx-prometheus-exporter:1.3.0"
  "prom/node-exporter:v1.8.2"
  "prom/mysqld-exporter:v0.15.1"
  "oliver006/redis_exporter:v1.62.0"
  "alpine:3.20"
)

echo "==> pulling ${#IMAGES[@]} images via mirror: ${MIRROR_HOST}"
for img in "${IMAGES[@]}"; do
  # skip images that already exist locally (idempotent)
  if docker image inspect "${img}" >/dev/null 2>&1; then
    echo "    [skip] ${img} already present"
    continue
  fi
  echo "    [pull] ${MIRROR_HOST}/${img}"
  if docker pull "${MIRROR_HOST}/${img}"; then
    docker tag "${MIRROR_HOST}/${img}" "${img}"
    echo "    [ ok ] retagged -> ${img}"
  else
    echo "    [warn] failed to pull ${img} from ${MIRROR_HOST}; will fall back to Docker Hub"
    docker pull "${img}" || echo "    [fail] ${img} could not be pulled at all"
  fi
done

echo "==> done. Verify with: docker images | grep -E 'nginx|redis|mysql|prom|grafana|alpine|python'"
