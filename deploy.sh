#!/usr/bin/env bash
# Karsa Trading System - Production Deployment Script
# Usage: ./deploy.sh [up|down|logs|status|update]

set -euo pipefail

COMPOSE="-f docker-compose.yml -f docker-compose.prod.yml"

case "${1:-up}" in
  up)
    echo "🚀 Starting Karsa in production mode..."
    docker compose $COMPOSE up -d --build
    echo "✅ All services started. Run './deploy.sh status' to check health."
    ;;
  down)
    echo "🛑 Stopping Karsa..."
    docker compose $COMPOSE down
    ;;
  logs)
    docker compose $COMPOSE logs -f --tail=100 ${2:-}
    ;;
  status)
    docker compose $COMPOSE ps
    echo ""
    echo "Health checks:"
    curl -sf http://localhost:20128/health && echo " 9Router: ✅" || echo " 9Router: ❌"
    curl -sf http://localhost:8443/health && echo " Telegram Bot: ✅" || echo " Telegram Bot: ❌"
    ;;
  update)
    echo "🔄 Updating and rebuilding..."
    docker compose $COMPOSE pull
    docker compose $COMPOSE up -d --build --force-recreate
    echo "✅ Updated."
    ;;
  *)
    echo "Usage: $0 {up|down|logs|status|update}"
    exit 1
    ;;
esac
