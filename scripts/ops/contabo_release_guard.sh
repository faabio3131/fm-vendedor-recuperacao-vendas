#!/usr/bin/env bash
# Root-owned, operator-installed trust boundary for the limited fm_deploy SSH role.
# Installed once with restrictive sudoers; never writable by fm_deploy.
set -euo pipefail
umask 077

root=/home/SaaS/atendevendeia
script=/usr/local/libexec/fm-atendevendeia-pilot-release.sh
mode="${1:-}"
sha="${2:-}"
digest="${3:-}"

case "$mode" in
  inspect)
    [[ "$#" -eq 1 ]] || exit 2
    exec /bin/bash "$script" inspect
    ;;
  stage)
    [[ "$#" -eq 3 ]] || exit 2
    [[ "$sha" =~ ^[0-9a-f]{40}$ && "$digest" =~ ^[0-9a-f]{64}$ ]] || exit 2
    mkdir -p "$root/incoming"
    temp="$(mktemp "$root/incoming/.staging.XXXXXXXX")"
    trap 'rm -f -- "$temp"' EXIT
    # No shell interpolation or path supplied by the SSH client beyond a checked SHA.
    timeout 120 cat > "$temp"
    [[ -s "$temp" && "$(sha256sum "$temp" | cut -d ' ' -f 1)" == "$digest" ]] || {
      echo "Archive checksum mismatch or empty archive" >&2; exit 1;
    }
    mv -f -- "$temp" "$root/incoming/$sha.tar.gz"
    trap - EXIT
    echo "STAGE_OK:$sha"
    ;;
  deploy)
    [[ "$#" -eq 3 ]] || exit 2
    [[ "$sha" =~ ^[0-9a-f]{40}$ && "$digest" =~ ^[0-9a-f]{64}$ ]] || exit 2
    # Only the root-owned script may execute privileged application operations.
    # Workflow approval flags do NOT elevate an arbitrary SSH command.
    export FM_DEPLOY_SHA="$sha" FM_DEPLOY_DIGEST="$digest"
    export FM_DEPLOY_APPROVE_MIGRATIONS=YES FM_DEPLOY_APPROVE_RELEASE=YES
    exec /bin/bash "$script" deploy
    ;;
  *)
    echo "Unsupported guarded release operation" >&2; exit 2;
    ;;
esac
