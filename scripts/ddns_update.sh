#!/usr/bin/env bash
# DuckDNS DDNS update.
# Использование (cron): ddns_update.sh <duckdns_subdomain> <duckdns_token>
# Бесплатная регистрация: https://www.duckdns.org/

set -euo pipefail

SUBDOMAIN="${1:?usage: ddns_update.sh <subdomain> <token>}"
TOKEN="${2:?usage: ddns_update.sh <subdomain> <token>}"

URL="https://www.duckdns.org/update?domains=${SUBDOMAIN}&token=${TOKEN}&ip="
RESPONSE=$(curl -fsS "$URL" || echo "ERROR")

if [ "$RESPONSE" = "OK" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) DDNS updated: ${SUBDOMAIN}.duckdns.org"
else
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) DDNS update FAILED: $RESPONSE" >&2
  exit 1
fi
