#!/usr/bin/env bash
# Volta o CÓDIGO para a versão anterior ao último deploy. O banco não é desfeito: migrations são
# só aditivas (ver docs/HETZNER_PRODUCAO.md); se uma migration causar dano, restaure o backup.
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$here/lib.sh"
load_env
repo="$(cd "$DEPLOY_DIR/../.." && pwd)"
prev="$(cat "$repo/.previous-release" 2>/dev/null || true)"
[ -n "$prev" ] || { log "sem versão anterior registrada"; exit 1; }
git -C "$repo" checkout --quiet "$prev"
compose build
compose up -d --remove-orphans
log "código voltou para $prev (HEAD solto; o próximo deploy.sh volta para main)"
