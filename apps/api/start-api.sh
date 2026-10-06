#!/bin/sh
# Início da API no contêiner. Existe para não depender de aspas dentro do `dockerCommand` do Render
# (que passou o comando inteiro como nome de programa: "not found", status 127).
# - RUN_BOOTSTRAP_ON_START=1: roda o `bootstrap` (idempotente) antes de abrir a porta; é o modo do
#   staging grátis, sem Shell nem pre-deploy (docs/STAGING_RENDER.md). Se falhar, a API NÃO sobe.
# - A porta vem de $PORT (o Render define); sem ela, 8000.
set -e
if [ "${RUN_BOOTSTRAP_ON_START:-}" = "1" ]; then
  python -m fm_seller.cli bootstrap
fi
exec uvicorn fm_seller.api.app:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}"
