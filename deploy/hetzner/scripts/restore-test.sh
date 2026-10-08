#!/usr/bin/env bash
# Prova de que o backup mais recente restaura: restaura num banco descartável, confere o conteúdo
# e apaga. Precisa da chave privada age (guardada com o Diretor): RESTORE_KEY=/caminho/chave.txt
# Saída 0 = backup restaurável; diferente de 0 = há algo errado (avise e investigue).
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$here/lib.sh"
load_env
key="${RESTORE_KEY:?defina RESTORE_KEY (caminho da chave privada age)}"
latest="$(ls -1t "$BACKUP_DIR"/fm_seller-*.dump.age 2>/dev/null | head -1 || true)"
[ -n "$latest" ] || { log "nenhum backup encontrado em $BACKUP_DIR"; alert "restore-test: sem backup"; exit 1; }
target="restore_test_$(date -u +%Y%m%d%H%M%S)"
cleanup() { dbx psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$target\"" >/dev/null 2>&1 || true; }
trap cleanup EXIT
"$here/restore.sh" "$latest" "$key" "$target"

q() { dbx psql -U "$DB_USER" -d "$target" -At -c "$1"; }
migrations="$(q "SELECT count(*) FROM schema_migrations" 2>/dev/null || echo 0)"
tables="$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")"
rls="$(q "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity")"
tenants="$(q "SELECT count(*) FROM tenants")"
log "restore-test: arquivo=$(basename "$latest") migrations=$migrations tabelas=$tables com_RLS_forcada=$rls clientes=$tenants"
if [ "$migrations" -lt 1 ] || [ "$tables" -lt 10 ] || [ "$rls" -lt 5 ]; then
  alert "restore-test FALHOU: conteúdo incompleto"; log "restore-test FALHOU"; exit 1
fi
alert "restore-test ok"
log "restore-test OK"
