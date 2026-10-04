-- 0010: captura segura de eventos reais de checkout (Cakto/Hotmart), desligada por padrão.
-- Serve para comparar o que a plataforma realmente manda com o que o normalizador espera.
-- O corpo é guardado cifrado, SEM segredos (redigidos antes), e expira sozinho.

CREATE TABLE event_captures (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- NULL = compra do próprio SaaS (plataforma); preenchido = evento da conexão de um cliente.
  tenant_id          uuid REFERENCES tenants(id) ON DELETE CASCADE,
  provider           text NOT NULL CHECK (provider IN ('cakto', 'hotmart')),
  event_type         text NOT NULL,
  -- Como o evento provou a origem: assinatura_hmac, segredo_no_corpo, hottok_cabecalho, hottok_corpo.
  auth_method        text,
  headers_encrypted  bytea NOT NULL,
  payload_encrypted  bytea NOT NULL,
  captured_at        timestamptz NOT NULL DEFAULT now(),
  expires_at         timestamptz NOT NULL
);
CREATE INDEX event_captures_scope_idx ON event_captures (tenant_id, provider, captured_at DESC);
CREATE INDEX event_captures_expiry_idx ON event_captures (expires_at);

ALTER TABLE event_captures ENABLE ROW LEVEL SECURITY;
ALTER TABLE event_captures FORCE ROW LEVEL SECURITY;
-- Do cliente: só o próprio. Da plataforma (tenant_id nulo): só o modo sistema.
CREATE POLICY event_captures_rw ON event_captures
  USING ((tenant_id IS NOT NULL AND tenant_id = app_tenant()) OR app_system())
  WITH CHECK ((tenant_id IS NOT NULL AND tenant_id = app_tenant()) OR app_system());

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, DELETE ON event_captures TO fm_app;
  END IF;
END $$;
