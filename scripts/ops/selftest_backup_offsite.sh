#!/usr/bin/env bash
# Synthetic integration test: no live database or external storage.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(mktemp -d)"
trap 'rm -rf -- "$root"' EXIT
mkdir -p "$root/bin" "$root/remote"
export MOCK_REMOTE_DIR="$root/remote"
cat >"$root/bin/psql" <<'STUB'
#!/usr/bin/env bash
echo sessions
STUB
cat >"$root/bin/pg_dump" <<'STUB'
#!/usr/bin/env bash
while (( $# )); do
  if [[ "$1" == "--file" ]]; then
    shift
    printf 'synthetic-pg-dump' >"$1"
    break
  fi
  shift
done
STUB
cat >"$root/bin/pg_restore" <<'STUB'
#!/usr/bin/env bash
echo 'TABLE public schema_migrations'
STUB
cat >"$root/bin/age" <<'STUB'
#!/usr/bin/env bash
out=""
src=""
while (( $# )); do
  case "$1" in
    -o) shift; out="$1";;
    -r) shift;;
    *) src="$1";;
  esac
  shift
done
printf 'mock-age-encrypted:' >"$out"
cat "$src" >>"$out"
STUB
cat >"$root/bin/rclone" <<'STUB'
#!/usr/bin/env bash
op="$1"
shift
case "$op" in
  copyto)
    src="$1"
    target="$2"
    [[ "$target" == mock:* ]] || exit 11
    target="$MOCK_REMOTE_DIR/${target#mock:}"
    mkdir -p "$(dirname "$target")"
    cp -- "$src" "$target"
    ;;
  cat)
    target="$1"
    [[ "$target" == mock:* ]] || exit 12
    if [[ "${MOCK_TAMPER:-0}" == 1 ]]; then
      printf 'tampered-blob'
    else
      cat "$MOCK_REMOTE_DIR/${target#mock:}"
    fi
    ;;
  *) exit 13;;
esac
STUB
chmod +x "$root/bin/"*
export PATH="$root/bin:$PATH"
export FM_DATABASE_ADMIN_URL="postgresql://synthetic.invalid/test"
export AGE_RECIPIENT="age1synthetic"
export RCLONE_REMOTE="mock:store"
bash "$here/backup_offsite.sh" >"$root/result.log"
grep -q 'SHA256 readback verified' "$root/result.log"
test "$(find "$root/remote" -name '*.age' | wc -l)" -eq 1
test "$(find "$root/remote" -name '*.sha256' | wc -l)" -eq 1
if MOCK_TAMPER=1 bash "$here/backup_offsite.sh" >"$root/negative.log" 2>&1; then
  echo "FAIL: accepted tampered remote backup" >&2
  exit 1
fi
echo 'PASS: offsite backup encrypted-upload control flow and tamper rejection'
