#!/usr/bin/env bash
# Atualiza a produção para o código da branch main, com backup antes e conferência depois.
# Uso (no servidor, como o usuário fmapp): /opt/fm-seller/deploy/hetzner/scripts/deploy.sh
# Se a conferência falhar, o script avisa e NÃO apaga nada: use rollback.sh para voltar.
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$here/lib.sh"
load_env
repo="$(cd "$DEPLOY_DIR/../.." && pwd)"

log "1/5 backup antes da atualização"
"$here/backup.sh"

before="$(git -C "$repo" rev-parse HEAD)"
echo "$before" > "$repo/.previous-release"
log "2/5 buscando o código (main)"
git -C "$repo" fetch --quiet origin main
git -C "$repo" merge --ff-only origin/main

log "3/5 construindo as imagens"
compose build

log "4/5 subindo (migrations rodam no serviço bootstrap)"
compose up -d --remove-orphans

log "5/5 conferindo a saúde"
for i in $(seq 1 30); do
  if curl -fsS -m 5 "https://${API_HOST}/v1/health" >/dev/null 2>&1; then
    log "deploy OK ($(git -C "$repo" rev-parse --short HEAD))"
    alert "deploy ok $(git -C "$repo" rev-parse --short HEAD)"
    exit 0
  fi
  sleep 5
done
alert "deploy FALHOU: API sem resposta. Anterior: $before"
log "API não respondeu em 150 s. Para voltar: scripts/rollback.sh"
exit 1
