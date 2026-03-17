#!/usr/bin/env sh
set -e

echo "Preparing uploads directory..."

mkdir -p /code/app/static/uploads
mkdir -p /code/seed_uploads

cp -n /code/seed_uploads/* /code/app/static/uploads/ 2>/dev/null || true

echo "Starting FastAPI..."
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 4 \
  --proxy-headers \
  --forwarded-allow-ips="*"