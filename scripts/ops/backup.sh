#!/usr/bin/env bash
# Backup lógico do banco (formato custom do pg_dump), com conferência de que o arquivo é legível.
#
# Uso:   FM_DATABASE_ADMIN_URL=postgresql://fm_owner:...@host/db scripts/ops/backup.sh [pasta]
# Saída: <pasta>/fm_seller_AAAAMMDDTHHMMSSZ.dump (padrão: ./backups), permissão 600.
#
# O dump traz as credenciais dos clientes JÁ CIFRADAS. A chave (FM_SECRETS_KEYS) NÃO vai no backup
# e deve ficar em cofre separado: sem ela o backup restaura, mas as credenciais não abrem.
# Guarde o arquivo fora do servidor do banco (outro provedor/bucket) e teste a restauração com
# scripts/ops/restore_check.sh. Backup que nunca foi restaurado não conta como backup.
set -euo pipefail

: "${FM_DATABASE_ADMIN_URL:?Defina FM_DATABASE_ADMIN_URL (papel fm_owner).}"
dir="${1:-backups}"
mkdir -p "$dir"
umask 077
out="$dir/fm_seller_$(date -u +%Y%m%dT%H%M%SZ).dump"

# As tabelas têm RLS FORÇADA (vale até para o dono). Sem o modo sistema o pg_dump falha; com ele,
# --enable-row-security faz o dump enxergar todas as linhas. Risco: tabela cuja política não libera
# o modo sistema sairia incompleta SEM erro. Por isso o backup confere isso antes e só aceita as
# exceções abaixo. `sessions` é de propósito: quem restaura precisa entrar de novo (login Google).
export PGOPTIONS="-c app.system=on"
expected_gaps="sessions"
gaps="$(psql "$FM_DATABASE_ADMIN_URL" -Atf "$(dirname "$0")/rls_not_system_readable.sql" | paste -sd, -)"
if [[ "$gaps" != "$expected_gaps" ]]; then
  echo "ERRO: tabelas que o backup não leria por inteiro: '${gaps:-nenhuma}' (esperado: '$expected_gaps')." >&2
  echo "Ajuste a política da tabela nova (liberar app_system()) antes de confiar neste backup." >&2
  exit 1
fi

# --no-owner: quem restaura vira dono (use o papel fm_owner). Grants (fm_app) são mantidos.
pg_dump --format=custom --no-owner --enable-row-security --file "$out" "$FM_DATABASE_ADMIN_URL"

# Conferência mínima: o arquivo precisa listar o conteúdo e conter a tabela de migrations.
pg_restore --list "$out" | grep -q "TABLE public schema_migrations" || {
  echo "ERRO: backup gerado mas sem a tabela schema_migrations; descartando $out" >&2
  rm -f "$out"
  exit 1
}
echo "Backup gerado: $out ($(wc -c <"$out") bytes)"
