#!/usr/bin/env bash
# Restore an encrypted offsite backup into a PRE-EXISTING EMPTY test DB only.
# Run on an isolated recovery host holding AGE_IDENTITY_FILE, never on production.
# Required: BACKUP_REMOTE_OBJECT (full rclone remote URI ending in .age),
# AGE_IDENTITY_FILE, RESTORE_DATABASE_URL (test database).
set -euo pipefail
umask 077
: "${BACKUP_REMOTE_OBJECT:?BACKUP_REMOTE_OBJECT required}"
: "${AGE_IDENTITY_FILE:?AGE_IDENTITY_FILE required}"
: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL required}"
[[ "$BACKUP_REMOTE_OBJECT" == *:*.age ]] || {
  echo "expected an explicit encrypted .age rclone object" >&2
  exit 2
}
[[ -r "$AGE_IDENTITY_FILE" ]] || { echo "age identity not readable" >&2; exit 2; }
for cmd in rclone age psql pg_restore sha256sum; do
  command -v "$cmd" >/dev/null || { echo "missing executable: $cmd" >&2; exit 2; }
done
# A database named restore_test_* is mandatory. Do not rely on a URL string,
# which can use aliases or default db names.
actual_db="$(psql "$RESTORE_DATABASE_URL" -Atc 'SELECT current_database()')"
[[ "$actual_db" == restore_test_* ]] || {
  echo "refusing restore: target database name must begin with restore_test_" >&2
  exit 2
}
scratch="$(mktemp -d)"
cleanup() { rm -rf -- "$scratch"; }
trap cleanup EXIT
trap 'echo "ERROR: isolated offsite restore check failed" >&2' ERR
object="$(basename "$BACKUP_REMOTE_OBJECT")"
encrypted="$scratch/$object"
rclone copyto "$BACKUP_REMOTE_OBJECT" "$encrypted"
rclone copyto "$BACKUP_REMOTE_OBJECT.sha256" "$encrypted.sha256"
( cd "$scratch" && sha256sum --check "$(basename "$encrypted").sha256" )
plaintext="$scratch/${object%.age}"
age -d -i "$AGE_IDENTITY_FILE" -o "$plaintext" "$encrypted"
[[ -s "$plaintext" ]] || { echo "empty decrypted dump" >&2; exit 1; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$here/restore_check.sh" "$plaintext"
echo "PASS: encrypted offsite backup restored and verified in isolated database $actual_db"
