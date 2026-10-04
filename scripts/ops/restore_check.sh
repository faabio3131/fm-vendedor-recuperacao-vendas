#!/usr/bin/env bash
# Restaura um backup em um banco SEPARADO e confere o essencial. Nunca aponte para o banco real.
#
# Uso:  RESTORE_DATABASE_URL=postgresql://fm_owner:...@host/banco_vazio_de_teste \
#         [SOURCE_DATABASE_URL=...  (opcional: compara contagens com o banco de origem)] \
#         scripts/ops/restore_check.sh arquivo.dump
#
# O banco de destino precisa existir e estar VAZIO. Confere: restauração sem erro, migrations
# presentes, RLS ativada e forçada em toda tabela com tenant_id e, se SOURCE_DATABASE_URL vier,
# contagens de linhas iguais (só valem se ninguém escreveu na origem depois do backup).
set -euo pipefail

dump="${1:?Informe o arquivo .dump}"
: "${RESTORE_DATABASE_URL:?Defina RESTORE_DATABASE_URL (banco vazio de teste).}"
here="$(cd "$(dirname "$0")" && pwd)"

if [[ -n "${SOURCE_DATABASE_URL:-}" && "$SOURCE_DATABASE_URL" == "$RESTORE_DATABASE_URL" ]]; then
  echo "ERRO: origem e destino são o mesmo banco." >&2
  exit 2
fi

existing=$(psql "$RESTORE_DATABASE_URL" -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
if [[ "$existing" != "0" ]]; then
  echo "ERRO: o banco de destino não está vazio ($existing tabelas). Use um banco novo." >&2
  exit 2
fi

# Se o papel fm_app não existe neste servidor, os GRANTs falhariam: restaura sem permissões
# e avisa que elas não foram conferidas.
acl_flag=()
has_app=$(psql "$RESTORE_DATABASE_URL" -Atc "SELECT count(*) FROM pg_roles WHERE rolname = 'fm_app'")
if [[ "$has_app" == "0" ]]; then
  acl_flag=(--no-acl)
  echo "AVISO: papel fm_app não existe neste servidor; permissões (GRANT) não foram restauradas."
fi

# Os dados entram antes de a RLS ser ligada (a ordem do dump garante); o modo sistema é só reforço.
PGOPTIONS="-c app.system=on" pg_restore --exit-on-error --no-owner --enable-row-security \
  "${acl_flag[@]}" --dbname "$RESTORE_DATABASE_URL" "$dump"
echo "Restauração concluída sem erro."

migs=$(psql "$RESTORE_DATABASE_URL" -Atc "SELECT count(*) FROM schema_migrations")
last=$(psql "$RESTORE_DATABASE_URL" -Atc "SELECT max(version) FROM schema_migrations")
[[ "$migs" -gt 0 ]] || { echo "ERRO: schema_migrations vazia." >&2; exit 1; }
echo "Migrations restauradas: $migs (última: $last)"

bad=$(psql "$RESTORE_DATABASE_URL" -Atf "$here/rls_invariant.sql")
if [[ -n "$bad" ]]; then
  echo "ERRO: tabelas com tenant_id sem RLS forçada após restaurar:" >&2
  echo "$bad" >&2
  exit 1
fi
echo "RLS ativada e forçada em todas as tabelas com tenant_id."

if [[ -n "${SOURCE_DATABASE_URL:-}" ]]; then
  # RLS vale também para o dono; o modo sistema libera a contagem na sessão.
  count_sql="SELECT set_config('app.system','on',false);
    SELECT 'tenants=' || count(*) FROM tenants UNION ALL
    SELECT 'users=' || count(*) FROM users UNION ALL
    SELECT 'connections=' || count(*) FROM connections UNION ALL
    SELECT 'recovery_cases=' || count(*) FROM recovery_cases UNION ALL
    SELECT 'messages=' || count(*) FROM messages ORDER BY 1;"
  a=$(psql "$SOURCE_DATABASE_URL" -At -f - <<<"$count_sql" | grep '=')
  b=$(psql "$RESTORE_DATABASE_URL" -At -f - <<<"$count_sql" | grep '=')
  if [[ "$a" != "$b" ]]; then
    echo "ERRO: contagens diferentes (origem vs restaurado):" >&2
    diff <(echo "$a") <(echo "$b") >&2 || true
    exit 1
  fi
  echo "Contagens iguais à origem: $(echo "$a" | tr '\n' ' ')"
fi
echo "OK: restauração verificada."
