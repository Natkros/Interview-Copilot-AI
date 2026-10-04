#!/bin/sh
set -e

# Apply database migrations before starting (PostgreSQL deployments).
case "$DATABASE_URL" in
  postgres*)
    echo "Running database migrations..."
    alembic upgrade head
    ;;
esac

exec "$@"
