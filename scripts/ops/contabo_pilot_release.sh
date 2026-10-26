#!/usr/bin/env bash
# FM Tecnologia — only the EXISTING Contabo AtendeVendeIA application.
# Does not create tenants, alter NPM/Evolution, enable sends or run a public launch.
# Transport: pinned SSH from a manually approved GitHub Actions environment.
set -euo pipefail
umask 077

mode="${1:-}"
root="/home/SaaS/atendevendeia"
db="fm-atendevendeia-db-1"
db_name="fm_atendevendeia"
compose_project="fm-atendevendeia"

if [[ "$mode" == "inspect" ]]; then
  echo "Contabo pilot / current commit:"
  if [[ -f "$root/SOURCE_COMMIT" ]]; then cat "$root/SOURCE_COMMIT"; else echo "unknown"; fi
  echo "Container states:"
  docker ps --filter "name=^/fm-atendevendeia-" --format '{{.Names}} | {{.Status}}'
  echo "Latest DB migration:"
  docker exec "$db" psql -U fm_owner -d "$db_name" -Atc \
    "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
  echo "No changes performed."
  exit 0
fi

[[ "$mode" == "deploy" ]] || { echo "Unsupported operation" >&2; exit 2; }
sha="${FM_DEPLOY_SHA:?Missing approved SHA}"
digest="${FM_DEPLOY_DIGEST:?Missing pinned source digest}"
[[ "$sha" =~ ^[0-9a-f]{40}$ && "$digest" =~ ^[0-9a-f]{64}$ ]] || exit 2
# Approval = Fabio's chat "sim" before merge to main; guard injects YES server-side.
[[ "${FM_DEPLOY_APPROVE_MIGRATIONS:-}" == "YES" ]] || {
  echo "Production migrations were not explicitly approved." >&2; exit 2;
}
[[ "${FM_DEPLOY_APPROVE_RELEASE:-}" == "YES" ]] || {
  echo "Production release was not explicitly approved." >&2; exit 2;
}
for bin in docker tar sha256sum flock curl timeout; do
  command -v "$bin" >/dev/null || { echo "Missing prerequisite: $bin" >&2; exit 2; }
done

# A host-side lock prevents overlapping manual and automated promotions.
install -d -m 700 "$root/releases" "$root/backups" "$root/incoming"
exec 9>"$root/.fm_pilot_deploy.lock"
flock -n 9 || { echo "Another Contabo release is already running" >&2; exit 2; }

incoming="$root/incoming/$sha.tar.gz"
release="$root/releases/$sha"
source="$release/source"
[[ -s "$incoming" ]] || { echo "Source archive not staged" >&2; exit 1; }
actual="$(sha256sum "$incoming" | cut -d ' ' -f 1)"
[[ "$actual" == "$digest" ]] || { echo "Source digest mismatch" >&2; exit 1; }
[[ ! -e "$release" ]] || { echo "This SHA already has a release directory" >&2; exit 1; }

mkdir -m 700 "$release" "$source"
tar -xzf "$incoming" --no-same-owner -C "$source"
for item in "$source/deploy/contabo/compose.yml" "$source/scripts/ops/rls_invariant.sql" \
  "$source/scripts/ops/rls_not_system_readable.sql" \
  "$source/apps/api/src/fm_seller/migrations/0017_commercial_quotas_60pct.sql"; do
  [[ -f "$item" ]] || { echo "Incomplete source archive" >&2; exit 1; }
done
printf '%s\n' "$digest" > "$release/source.sha256"

[[ -f "$root/.env" && -d "$root/repo" ]] || {
  echo "Existing deployment/environment not found; refusing fresh bootstrap" >&2; exit 1;
}
chmod_go="$(stat -c %a "$root/.env")"
[[ "${chmod_go: -2}" == "00" ]] || {
  echo "Existing .env must not be group/world-readable" >&2; exit 1;
}
docker compose --env-file "$root/.env" -f "$source/deploy/contabo/compose.yml" config --quiet
[[ "$(docker ps --filter "name=^/$db$" --format '{{.Names}}')" == "$db" ]] || {
  echo "Canonical production database container not healthy/available" >&2; exit 1;
}
[[ "$(docker inspect --format '{{.State.Health.Status}}' "$db")" == "healthy" ]] || {
  echo "Production database is not healthy" >&2; exit 1;
}

last="$(docker exec "$db" psql -U fm_owner -d "$db_name" -Atc \
  'SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1')"
migdir="$source/apps/api/src/fm_seller/migrations"
target="$(find "$migdir" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]_*.sql' -printf '%f\n' | sort | tail -1)"
[[ -n "$target" ]] || { echo "No migrations in source" >&2; exit 1; }
# Baseline must be a migration known to this source (never newer / unknown).
[[ -f "$migdir/$last" ]] || { echo "Unexpected schema baseline: $last" >&2; exit 1; }
# Block destructive SQL in pending migrations: automatic deploy never drops/renames data.
pending=0
while IFS= read -r f; do
  [[ "$f" > "$last" ]] || continue
  pending=$((pending+1))
  if grep -Eiq 'DROP[[:space:]]+(TABLE|COLUMN|SCHEMA|DATABASE|CONSTRAINT|INDEX|TYPE)|TRUNCATE|DELETE[[:space:]]+FROM|ALTER[[:space:]]+COLUMN.*TYPE|RENAME' "$migdir/$f"; then
    echo "Destructive SQL in pending migration $f; manual review required" >&2; exit 1
  fi
done < <(find "$migdir" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]_*.sql' -printf '%f\n' | sort)
echo "Pending migrations: $pending (target $target)"
echo "Checked DB baseline: $last"

# Validate canonical backup coverage; forced tenant RLS must not silently truncate data.
gaps="$(docker exec -i -e PGOPTIONS='-c app.system=on' "$db" \
  psql -U fm_owner -d "$db_name" -At \
  < "$source/scripts/ops/rls_not_system_readable.sql" | paste -sd, -)"
[[ "$gaps" == "sessions" ]] || {
  echo "Unsafe backup RLS coverage; refusing deployment" >&2; exit 1;
}
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$root/backups/pre-ci-release-$stamp-$sha.tar.dump"
echo "Creating RLS-safe production backup (data retained only on protected host)."
docker exec -e PGOPTIONS='-c app.system=on' "$db" \
  pg_dump -U fm_owner -d "$db_name" --format=custom --no-owner \
    --enable-row-security > "$backup"
[[ -s "$backup" ]] || { echo "Empty backup" >&2; exit 1; }
toc="$(docker exec -i "$db" pg_restore --list < "$backup")"
[[ "$toc" == *"TABLE public schema_migrations"* ]] || {
  echo "Backup missing schema_migrations" >&2; exit 1;
}

# Restore the exact backup into an ephemeral, separate database. Never into production.
short="${sha:0:8}"
testdb="fm_release_restore_${short}_$$"
[[ "$testdb" =~ ^[a-z0-9_]+$ ]] || exit 2
restore_created=0
worker_stopped=0
cleanup() {
  if [[ "$restore_created" == "1" ]]; then
    docker exec "$db" dropdb -U fm_owner --if-exists --force "$testdb" >/dev/null 2>&1 || true
  fi
  if [[ "$worker_stopped" == "1" ]]; then
    docker start fm-atendevendeia-worker-1 >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
docker exec "$db" psql -U fm_owner -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $testdb" >/dev/null
restore_created=1
docker exec -i -e PGOPTIONS='-c app.system=on' "$db" \
  pg_restore -U fm_owner --exit-on-error --no-owner --enable-row-security \
  --dbname "$testdb" < "$backup" >/dev/null
bad_rls="$(docker exec -i "$db" psql -U fm_owner -d "$testdb" -At \
  < "$source/scripts/ops/rls_invariant.sql")"
[[ -z "$bad_rls" ]] || { echo "RLS invariant failed after restore" >&2; exit 1; }
restored_last="$(docker exec "$db" psql -U fm_owner -d "$testdb" -Atc \
  'SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1')"
[[ "$restored_last" == "$last" ]] || { echo "Restore schema version mismatch" >&2; exit 1; }
echo "Isolated PostgreSQL backup restore and RLS verified."
docker exec "$db" dropdb -U fm_owner --if-exists --force "$testdb" >/dev/null
restore_created=0

# Preserve the exact old runnable images for operator-controlled recovery.
old_api="$(docker inspect --format '{{.Image}}' fm-atendevendeia-api-1)"
old_web="$(docker inspect --format '{{.Image}}' fm-atendevendeia-web-1)"
docker tag "$old_api" "fm-atendevendeia-api:rollback-$short"
docker tag "$old_web" "fm-atendevendeia-web:rollback-$short"
echo "Rollback image tags recorded: rollback-$short."

# Builds never touch the Evolution API, NPM or their databases.
docker compose --env-file "$root/.env" -f "$source/deploy/contabo/compose.yml" \
  build api web

# Pause the sender while migrating and switching app processes.
docker stop fm-atendevendeia-worker-1 >/dev/null
worker_stopped=1
docker compose --env-file "$root/.env" -f "$source/deploy/contabo/compose.yml" \
  run --rm --no-deps api python -m fm_seller.cli migrate
latest="$(docker exec "$db" psql -U fm_owner -d "$db_name" -Atc \
  'SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1')"
[[ "$latest" == "$target" ]] || {
  echo "Database did not reach the expected target migration" >&2; exit 1;
}

docker compose --env-file "$root/.env" -f "$source/deploy/contabo/compose.yml" \
  up -d --no-deps --no-build api worker web
worker_stopped=0
for attempt in {1..30}; do
  a="$(docker inspect --format '{{.State.Health.Status}}' fm-atendevendeia-api-1 || true)"
  w="$(docker inspect --format '{{.State.Health.Status}}' fm-atendevendeia-web-1 || true)"
  r="$(docker inspect --format '{{.State.Running}}' fm-atendevendeia-worker-1 || true)"
  if [[ "$a" == "healthy" && "$w" == "healthy" && "$r" == "true" ]]; then break; fi
  sleep 4
done
[[ "$a" == "healthy" && "$w" == "healthy" && "$r" == "true" ]] || {
  echo "Application health failed. Use preserved images; NEVER auto-restore DB." >&2
  exit 1
}
curl --fail --silent --show-error --max-time 15 \
  'https://app.atendevendeia.com.br/v1/ready' >/dev/null

# Update the operator source tree only after the new processes passed health gates.
# Preserve previous code snapshot; .env and persistent DB volume remain OUTSIDE source.
legacy="$root/releases/operator-repo-before-$short-$stamp"
mv "$root/repo" "$legacy"
ln -s "$source" "$root/repo"
printf '%s\n' "$sha" > "$root/SOURCE_COMMIT"
echo "DEPLOY_OK: $sha; DB migration: $latest; rollback images retained."
echo "Evolution/NPM/payment credentials and service flags were not modified."
