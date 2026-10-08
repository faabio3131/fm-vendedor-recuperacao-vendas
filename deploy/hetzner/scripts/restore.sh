#!/usr/bin/env bash
# Restaura um backup para um banco NOVO (nunca por cima do banco em uso).
# Uso: restore.sh <arquivo.dump.age> <chave-privada-age> <nome-do-banco-novo>
# Para trocar o banco de produção por ele, siga docs/HETZNER_PRODUCAO.md (seção "Restaurar").
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$here/lib.sh"
load_env
file="${1:?arquivo .dump.age}"; key="${2:?chave privada age}"; target="${3:?nome do banco novo}"
case "$target" in
  "$DB_NAME") echo "Recusado: restaurar por cima do banco em uso ($DB_NAME) não é permitido aqui." >&2; exit 2 ;;
  *[!a-z0-9_]*|"") echo "Nome de banco inválido (só a-z, 0-9 e _)." >&2; exit 2 ;;
esac
if [ -f "$file.sha256" ]; then
  ( cd "$(dirname "$file")" && sha256sum -c "$(basename "$file").sha256" >/dev/null ) \
    || { echo "Checksum do backup NÃO confere." >&2; exit 3; }
fi
dbx psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$target\" OWNER \"$DB_USER\""
# Se a restauração falhar (chave errada, arquivo ruim), não deixa um banco pela metade.
trap 'dbx psql -U "$DB_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$target\"" >/dev/null 2>&1 || true' ERR
set -o pipefail
age -d -i "$key" "$file" | dbx pg_restore -U "$DB_USER" -d "$target" --no-owner --exit-on-error
trap - ERR
log "restaurado em $target"
