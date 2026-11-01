#!/bin/sh
# Update the live site: run on the droplet from the repo folder.
set -eu
cd "$(dirname "$0")"
git pull --ff-only
docker compose up -d --build
docker image prune -f >/dev/null
sleep 3
curl -fsS "https://$(grep '^SITE_ADDRESS=' .env | cut -d= -f2)/api/health" && echo " deployed"
