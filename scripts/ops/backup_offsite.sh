#!/usr/bin/env bash
# Encrypt and transfer a canonical pg_dump off-host; NEVER store plaintext off-host.
# Required environment: FM_DATABASE_ADMIN_URL, AGE_RECIPIENT (public key),
# RCLONE_REMOTE (existing rclone destination, e.g. remote:bucket/atendevendeia).
# Keep the age identity/restore key OFF this database server.
set -euo pipefail
umask 077
: "${FM_DATABASE_ADMIN_URL:?FM_DATABASE_ADMIN_URL required}"
: "${AGE_RECIPIENT:?AGE_RECIPIENT required}"
: "${RCLONE_REMOTE:?RCLONE_REMOTE required}"
[[ "$RCLONE_REMOTE" == *:* ]] || { echo "RCLONE_REMOTE must name an explicit remote" >&2; exit 2; }
for program in age rclone sha256sum pg_dump pg_restore psql; do
  command -v "$program" >/dev/null || { echo "missing executable: $program" >&2; exit 2; }
done
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scratch="$(mktemp -d)"
cleanup() { rm -rf -- "$scratch"; }
trap cleanup EXIT
trap 'echo "ERROR: offsite backup failed; restore and offsite verification NOT certified" >&2' ERR

# Reuse the canonical RLS-aware, migration-checked PostgreSQL backup.
"$here/backup.sh" "$scratch"
shopt -s nullglob
dumps=("$scratch"/*.dump)
(( ${#dumps[@]} == 1 )) || { echo "expected exactly one dump" >&2; exit 1; }
dump="${dumps[0]}"
encrypted="$scratch/$(basename "$dump").age"
age -r "$AGE_RECIPIENT" -o "$encrypted.partial" "$dump"
test -s "$encrypted.partial"
mv -- "$encrypted.partial" "$encrypted"
rm -f -- "$dump"
digest="$(sha256sum "$encrypted" | cut -d ' ' -f 1)"
printf '%s  %s\n' "$digest" "$(basename "$encrypted")" > "$encrypted.sha256"
target="${RCLONE_REMOTE%/}/$(basename "$encrypted")"
rclone copyto "$encrypted" "$target" --immutable
# Read back the encrypted blob from the independent destination, not just file metadata.
remote_digest="$(rclone cat "$target" | sha256sum | cut -d ' ' -f 1)"
[[ "$remote_digest" == "$digest" ]] || { echo "offsite SHA256 mismatch" >&2; exit 1; }
rclone copyto "$encrypted.sha256" "$target.sha256" --immutable
echo "OK: encrypted offsite backup uploaded and SHA256 readback verified: $(basename "$encrypted")"
echo "Restore-test on isolated PostgreSQL remains required before declaring recoverability."
