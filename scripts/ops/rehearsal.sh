#!/usr/bin/env bash
# Ensaio de subida e de rollback em Postgres LOCAL: sobe a versão anterior, faz backup, aplica a
# migration nova ("a subida"), volta pelo backup ("o rollback") e sobe de novo.
#
# Uso:  FM_DATABASE_ADMIN_URL=postgresql://fm_owner:...@localhost:5432/qualquer_banco \
#         [PYTHON=python3] scripts/ops/rehearsal.sh
#
# O papel fm_owner precisa poder criar banco (o bootstrap_roles.sql já dá CREATEDB). Cria dois
# bancos temporários (fm_ensaio_*) e apaga os dois no fim. Nunca aponte para um servidor de
# produção: o ensaio é para rodar na sua máquina ou no CI. Precisa de psql, pg_dump e pg_restore.
#
# O que o ensaio prova: o backup antes da subida restaura sozinho, o banco volta à versão
# anterior (sem a migration nova) com os dados e o isolamento por cliente intactos, e a subida
# pode ser refeita depois. O que NÃO prova: o rollback da imagem no Render nem o tempo de
# restauração de um banco grande.
set -euo pipefail

: "${FM_DATABASE_ADMIN_URL:?Defina FM_DATABASE_ADMIN_URL (papel fm_owner, banco local).}"
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
py="${PYTHON:-python3}"
base="${FM_DATABASE_ADMIN_URL%/*}"
stamp="$(date -u +%H%M%S)$RANDOM"
src="fm_ensaio_$stamp"
dst="fm_ensaio_restaurado_$stamp"
tmp="$(mktemp -d)"

cleanup() {
  psql "$FM_DATABASE_ADMIN_URL" -qAt -c "DROP DATABASE IF EXISTS \"$src\"" >/dev/null 2>&1 || true
  psql "$FM_DATABASE_ADMIN_URL" -qAt -c "DROP DATABASE IF EXISTS \"$dst\"" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
step() { echo; echo "== $*"; }
fail() { echo "ERRO: $*" >&2; exit 1; }
cli() { (cd "$root/apps/api" && FM_ENV=dev FM_DATABASE_ADMIN_URL="$1" "$py" -m fm_seller.cli "${@:2}"); }
one() { PGOPTIONS="-c app.system=on" psql "$1" -qAt -c "$2"; }  # RLS forçada: modo sistema

migrations_dir="$root/apps/api/src/fm_seller/migrations"
last="$(ls "$migrations_dir"/*.sql | sort | tail -1 | xargs basename)"
previous="$(ls "$migrations_dir"/*.sql | sort | tail -2 | head -1 | xargs basename | cut -d_ -f1)"

psql "$FM_DATABASE_ADMIN_URL" -qAt -c "CREATE DATABASE \"$src\"" >/dev/null
psql "$FM_DATABASE_ADMIN_URL" -qAt -c "CREATE DATABASE \"$dst\"" >/dev/null
src_url="$base/$src"
dst_url="$base/$dst"

step "1. Versão anterior no ar (migrations até $previous) e um cliente cadastrado"
cli "$src_url" migrate --until "$previous"
cli "$src_url" create-tenant --name "Cliente do ensaio" --email ensaio@example.test
[[ "$(one "$src_url" "SELECT count(*) FROM schema_migrations WHERE version = '$last'")" == "0" ]] \
  || fail "a migration nova já estava aplicada antes da subida"

step "2. Backup antes da subida"
FM_DATABASE_ADMIN_URL="$src_url" "$here/backup.sh" "$tmp"
dump="$(ls "$tmp"/*.dump)"

step "3. Subida: aplica a migration nova ($last)"
cli "$src_url" migrate
[[ "$(one "$src_url" "SELECT count(*) FROM schema_migrations WHERE version = '$last'")" == "1" ]] \
  || fail "a subida não aplicou $last"
[[ "$(one "$src_url" "SELECT count(*) FROM tenants")" == "1" ]] || fail "a subida mexeu nos clientes"

step "4. Rollback: restaura o backup em um banco novo e vazio"
RESTORE_DATABASE_URL="$dst_url" "$here/restore_check.sh" "$dump"
[[ "$(one "$dst_url" "SELECT count(*) FROM schema_migrations WHERE version = '$last'")" == "0" ]] \
  || fail "o banco restaurado ainda tem a migration nova: não voltou à versão anterior"
[[ "$(one "$dst_url" "SELECT count(*) FROM tenants")" == "1" ]] \
  || fail "o banco restaurado perdeu o cliente"
echo "Banco restaurado na versão anterior, com o cliente e sem $last."

step "5. Subir de novo a partir do banco restaurado"
cli "$dst_url" migrate
[[ "$(one "$dst_url" "SELECT count(*) FROM schema_migrations WHERE version = '$last'")" == "1" ]] \
  || fail "não foi possível subir de novo depois do rollback"
[[ "$(psql "$dst_url" -qAtf "$here/rls_invariant.sql")" == "" ]] \
  || fail "algum cliente ficou sem isolamento (RLS) depois da subida"

echo
echo "ENSAIO OK: backup, subida, rollback pelo backup e nova subida funcionam neste banco local."
