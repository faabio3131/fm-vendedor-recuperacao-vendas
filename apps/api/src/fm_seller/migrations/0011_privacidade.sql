-- 0011: privacidade (LGPD): retenção, registro de consentimento, exclusão de contato e de cliente.

-- Retenção por cliente (PROVISÓRIO: 365 dias, decisão do Diretor) e pedido de exclusão da conta.
ALTER TABLE tenant_settings
  ADD COLUMN retention_days smallint NOT NULL DEFAULT 365
    CHECK (retention_days BETWEEN 30 AND 3650),
  ADD COLUMN deletion_requested_at timestamptz,
  ADD COLUMN deletion_due_at timestamptz,
  ADD COLUMN deletion_requested_by uuid,
  ADD COLUMN deletion_prev_plan_status text;

-- Quem declarou o consentimento, quando e por onde. Só contagem por registro, nunca o contato.
CREATE TABLE consent_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id     uuid,
  action      text NOT NULL CHECK (action IN ('declared', 'revoked', 'registered')),
  origin      text NOT NULL CHECK (origin IN ('painel', 'registro_avulso', 'importacao')),
  count       int NOT NULL DEFAULT 1 CHECK (count >= 1),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consent_events_tenant_idx ON consent_events (tenant_id, created_at DESC);

-- Registro de clientes excluídos (fim de contrato). Sem FK de propósito: sobrevive ao cliente e
-- guarda só o id e as datas, nenhum dado pessoal. Só a rotina de plataforma enxerga.
CREATE TABLE deletion_log (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL,
  requested_at   timestamptz,
  executed_at    timestamptz NOT NULL DEFAULT now(),
  users_removed  int NOT NULL DEFAULT 0
);

ALTER TABLE consent_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_events FORCE ROW LEVEL SECURITY;
CREATE POLICY consent_events_rw ON consent_events USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());
ALTER TABLE deletion_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE deletion_log FORCE ROW LEVEL SECURITY;
CREATE POLICY deletion_log_sys ON deletion_log USING (app_system()) WITH CHECK (app_system());

-- Só a rotina de plataforma apaga cliente e usuário que ficou sem nenhum cliente.
CREATE POLICY tenants_del ON tenants FOR DELETE USING (app_system());
CREATE POLICY users_del ON users FOR DELETE USING (app_system());

-- A auditoria continua somente de inserção. A única exceção é a exclusão do próprio cliente (fim
-- de contrato): a rotina declara qual cliente está sendo removido e só as linhas dele podem sair.
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS
  $$
  BEGIN
    IF TG_OP = 'DELETE' AND OLD.tenant_id::text = current_setting('app.purge_tenant', true) THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_log é somente de inserção';
  END
  $$;

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT ON consent_events, deletion_log TO fm_app;
    GRANT DELETE ON conversations, messages, contacts, contact_channels, recovery_cases,
      recovery_steps, webhook_events, platform_events, tenants, users TO fm_app;
  END IF;
END $$;
