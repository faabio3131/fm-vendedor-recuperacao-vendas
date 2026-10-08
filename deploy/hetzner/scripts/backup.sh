#!/usr/bin/env bash
# Backup completo do banco, cifrado com a chave PÚBLICA do age (a privada não fica no servidor).
# Saída: $BACKUP_DIR/fm_seller-AAAAMMDDTHHMMSSZ.dump.age + .sha256
# Retenção: apaga os mais antigos que RETENTION_DAYS (padrão 14). Cópia externa: RCLONE_REMOTE.
# Um backup só vale depois de restaurado: rode restore-test.sh toda semana.
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$here/lib.sh"
load_env
: "${AGE_RECIPIENT:?defina AGE_RECIPIENT (chave pública age) no .env}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

umask 077
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/fm_seller-$stamp.dump.age"
tmp="$out.partial"

trap 'rm -f "$tmp"; alert "backup FALHOU em $stamp"; log "backup FALHOU"' ERR
dbx pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom --no-owner --compress=6 \
  | age -r "$AGE_RECIPIENT" -o "$tmp"
[ -s "$tmp" ] || { log "backup vazio"; exit 1; }
mv "$tmp" "$out"
( cd "$BACKUP_DIR" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )
trap - ERR

find "$BACKUP_DIR" -name 'fm_seller-*.dump.age*' -mtime +"$RETENTION_DAYS" -delete

if [ -n "${RCLONE_REMOTE:-}" ]; then
  rclone copyto "$out" "$RCLONE_REMOTE/$(basename "$out")" --immutable \
    && rclone copyto "$out.sha256" "$RCLONE_REMOTE/$(basename "$out").sha256" --immutable \
    || { alert "cópia externa do backup FALHOU"; log "cópia externa FALHOU"; exit 1; }
fi
log "backup ok: $out ($(du -h "$out" | cut -f1))"
alert "backup ok $stamp"
