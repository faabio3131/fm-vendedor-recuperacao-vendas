#!/usr/bin/env bash
# Verificação de alertas (worker parado, fila, falha de envio, licenças). Roda a cada 5 minutos.
# Saída 0 ok, 1 aviso, 2 crítico; avisa pelo ALERT_URL quando não for 0.
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$here/lib.sh"
load_env
set +e
out="$(compose run --rm -T --no-deps worker python -m fm_seller.cli ops-check 2>&1)"
code=$?
set -e
if [ "$code" -ne 0 ]; then
  log "ops-check código $code"; echo "$out" | tail -n 20
  alert "ops-check código $code: $(echo "$out" | tail -n 3 | tr '\n' ' ' | cut -c1-300)"
fi
exit "$code"
