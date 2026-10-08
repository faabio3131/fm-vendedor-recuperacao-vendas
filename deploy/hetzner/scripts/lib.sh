#!/usr/bin/env bash
# Funções comuns. Dois modos de falar com o banco:
#   FM_DB_MODE=docker (padrão): usa o contêiner `db` do compose de produção.
#   FM_DB_MODE=local: usa pg_dump/psql da máquina (PGHOST, PGUSER, PGPASSWORD) — usado nos testes.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/fm-seller/deploy/hetzner}"
ENV_FILE="${ENV_FILE:-/opt/fm-seller/.env}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/fm-seller}"
DB_NAME="${DB_NAME:-fm_seller}"
DB_USER="${DB_USER:-fm_owner}"
FM_DB_MODE="${FM_DB_MODE:-docker}"

load_env() {
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
  fi
}

compose() {
  docker compose --env-file "$ENV_FILE" -f "$DEPLOY_DIR/docker-compose.prod.yml" "$@"
}

# dbx <comando> [args]: roda um programa do Postgres (pg_dump, pg_restore, psql...) no banco.
dbx() {
  if [ "$FM_DB_MODE" = "local" ]; then
    "$@"
  else
    compose exec -T db "$@"
  fi
}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

alert() {
  # Aviso opcional de falha: ping no ALERT_URL (nunca inclui dados do banco).
  if [ -n "${ALERT_URL:-}" ]; then
    curl -fsS -m 10 --retry 2 -d "$1" "$ALERT_URL" >/dev/null 2>&1 || true
  fi
}
