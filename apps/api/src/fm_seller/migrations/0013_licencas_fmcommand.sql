-- 0013: vínculo com o Billing Central do FM Command (ADR-0005, contrato fmcc.license.v1).
-- ADITIVA e INERTE: nada é ativado por esta migration. O recebimento fica desligado até
-- FM_FMCOMMAND_MODE sair de `off`. Nenhuma linha de cliente existente é alterada.
-- Todas as tabelas novas são de sistema: só rotina com contexto `system` lê e escreve (como platform_events).

-- Origem do plano: passa a admitir `fmcommand` (as origens atuais continuam valendo).
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'tenant_plans'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%hotmart%'
  LOOP
    EXECUTE format('ALTER TABLE tenant_plans DROP CONSTRAINT %I', c);
  END LOOP;
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'plan_products'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%hotmart%'
  LOOP
    EXECUTE format('ALTER TABLE plan_products DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE tenant_plans
  ADD CONSTRAINT tenant_plans_source_check CHECK (source IN ('manual', 'cakto', 'hotmart', 'fmcommand'));
ALTER TABLE plan_products
  ADD CONSTRAINT plan_products_provider_check CHECK (provider IN ('cakto', 'hotmart', 'fmcommand'));

-- Vínculo entre o tenant local e os identificadores comerciais emitidos pelo Command (UUIDv7).
-- E-mail NÃO é chave. `authority` diz quem governa o estado comercial desta assinatura:
-- `direct` (Cakto/Hotmart como hoje) ou `fmcommand`. Nunca as duas.
CREATE TABLE commercial_links (
  tenant_id            uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  issuer               text NOT NULL DEFAULT 'fmcommand' CHECK (issuer = 'fmcommand'),
  product_code         text NOT NULL,
  customer_id          uuid NOT NULL,
  subscription_id      uuid NOT NULL,
  license_id           uuid NOT NULL,
  authority            text NOT NULL CHECK (authority IN ('direct', 'fmcommand')),
  previous_source      text CHECK (previous_source IN ('manual', 'cakto', 'hotmart')),
  previous_external_ref text,
  license_version      bigint NOT NULL CHECK (license_version >= 1),
  state                text NOT NULL CHECK (state IN
                         ('pending', 'active', 'past_due', 'suspended', 'canceled', 'refunded', 'expired')),
  plan_code            text NOT NULL,
  valid_from           timestamptz NOT NULL,
  valid_until          timestamptz NOT NULL,
  grace_ends_at        timestamptz,
  last_validated_at    timestamptz NOT NULL,
  -- Contingência: uma janela por versão de licença, persistida, nunca renovada sozinha.
  contingency_version      bigint,
  contingency_started_at   timestamptz,
  contingency_until        timestamptz,
  contingency_exhausted_at timestamptz,
  authority_changed_at timestamptz,
  authority_approved_by text,
  authority_evidence    text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (valid_from < valid_until),
  CHECK (contingency_until IS NULL OR contingency_started_at IS NULL
         OR contingency_until - contingency_started_at <= interval '72 hours')
);
CREATE UNIQUE INDEX commercial_links_license_uq ON commercial_links (issuer, license_id);
CREATE UNIQUE INDEX commercial_links_subscription_uq ON commercial_links (issuer, subscription_id);
CREATE INDEX commercial_links_customer_idx ON commercial_links (issuer, customer_id);

-- Eventos de licença recebidos (ou vistos na reconciliação). Corpo bruto cifrado. `event_id` único
-- garante a idempotência. Tabela própria: não mistura com platform_events (Cakto/Hotmart intactos).
CREATE TABLE license_events (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id          text NOT NULL UNIQUE,
  event_type        text NOT NULL,
  channel           text NOT NULL CHECK (channel IN ('webhook', 'reconciliation')),
  mode              text NOT NULL CHECK (mode IN ('shadow', 'enforce')),
  license_id        uuid,
  license_version   bigint,
  tenant_id         uuid REFERENCES tenants(id) ON DELETE SET NULL,
  payload_encrypted bytea NOT NULL,
  outcome           text NOT NULL DEFAULT 'received',
  error             text,
  received_at       timestamptz NOT NULL DEFAULT now(),
  processed_at      timestamptz
);
CREATE INDEX license_events_license_idx ON license_events (license_id, received_at);
CREATE INDEX license_events_outcome_idx ON license_events (outcome, received_at);

-- Modo sombra: o que o Command diz, comparado com o estado real. NUNCA altera licença real.
CREATE TABLE license_shadow (
  license_id        uuid PRIMARY KEY,
  customer_id       uuid NOT NULL,
  subscription_id   uuid NOT NULL,
  product_code      text NOT NULL,
  license_version   bigint NOT NULL,
  state             text NOT NULL,
  plan_code         text NOT NULL,
  valid_until       timestamptz NOT NULL,
  grace_ends_at     timestamptz,
  matched_tenant_id uuid REFERENCES tenants(id) ON DELETE SET NULL,
  real_status       text,
  expected_status   text,
  diverges          boolean NOT NULL DEFAULT false,
  observed_at       timestamptz NOT NULL DEFAULT now()
);

-- Estado da reconciliação (uma linha só).
CREATE TABLE license_sync_state (
  id                   boolean PRIMARY KEY DEFAULT true CHECK (id),
  last_attempt_at      timestamptz,
  last_success_at      timestamptz,
  last_error           text,
  consecutive_failures integer NOT NULL DEFAULT 0
);
INSERT INTO license_sync_state (id) VALUES (true);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['commercial_links', 'license_events', 'license_shadow', 'license_sync_state']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY %I ON %I USING (app_system()) WITH CHECK (app_system())',
                   t || '_sys', t);
  END LOOP;
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON commercial_links, license_events, license_shadow,
      license_sync_state TO fm_app;
  END IF;
END $$;
