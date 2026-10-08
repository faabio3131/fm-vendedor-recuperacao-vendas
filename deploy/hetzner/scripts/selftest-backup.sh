#!/usr/bin/env bash
# Autoteste do ciclo backup → restauração, num Postgres LOCAL descartável (não toca produção).
# Precisa de: postgres 16 rodando, usuário que cria bancos (PGHOST/PGUSER/PGPASSWORD), age, e o
# banco de teste já migrado (python -m fm_seller.cli migrate). Uso:
#   PGHOST=127.0.0.1 PGUSER=postgres PGPASSWORD=... DB_NAME=fm_selftest DB_USER=postgres \
#     deploy/hetzner/scripts/selftest-backup.sh
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
export FM_DB_MODE=local ENV_FILE="$tmp/none.env" BACKUP_DIR="$tmp/bk"
age-keygen -o "$tmp/key.txt" >/dev/null 2>&1
export AGE_RECIPIENT; AGE_RECIPIENT="$(grep 'public key' "$tmp/key.txt" | awk '{print $NF}')"
export RESTORE_KEY="$tmp/key.txt"
"$here/backup.sh"
"$here/restore-test.sh"
# Falhas que PRECISAM ser recusadas:
f="$(ls "$BACKUP_DIR"/*.dump.age | head -1)"
! "$here/restore.sh" "$f" "$tmp/key.txt" "${DB_NAME:-fm_seller}" 2>/dev/null   # por cima do banco em uso
printf 'X' | dd of="$f" bs=1 seek=300 conv=notrunc 2>/dev/null
! "$here/restore.sh" "$f" "$tmp/key.txt" selftest_bad 2>/dev/null                # arquivo adulterado
echo "AUTOTESTE OK"
