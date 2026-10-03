-- 0002: recebimento de eventos, contatos, recuperação de vendas e provisionamento por compra.

CREATE TABLE tenant_settings (
  tenant_id              uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  timezone               text NOT NULL DEFAULT 'America/Sao_Paulo',
  quiet_start            smallint NOT NULL DEFAULT 21 CHECK (quiet_start BETWEEN 0 AND 23),
  quiet_end              smallint NOT NULL DEFAULT 8  CHECK (quiet_end BETWEEN 0 AND 23),
  daily_cap              smallint NOT NULL DEFAULT 1  CHECK (daily_cap BETWEEN 1 AND 10),
  max_contacts_per_case  smallint NOT NULL DEFAULT 4  CHECK (max_contacts_per_case BETWEEN 1 AND 10),
  recovery_enabled       boolean NOT NULL DEFAULT false,
  consent_declared_at    timestamptz,
  consent_declared_by    uuid,
  updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE webhook_events (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connection_id      uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  provider           text NOT NULL,
  event_type         text NOT NULL,
  dedupe_key         text NOT NULL,
  payload_encrypted  bytea NOT NULL,
  status             text NOT NULL DEFAULT 'received'
                     CHECK (status IN ('received', 'processed', 'ignored', 'failed')),
  error              text,
  received_at        timestamptz NOT NULL DEFAULT now(),
  processed_at       timestamptz,
  UNIQUE (connection_id, dedupe_key)
);

CREATE TABLE contacts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name        text NOT NULL DEFAULT '',
  phone       text,
  email       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CHECK (phone IS NOT NULL OR email IS NOT NULL)
);
CREATE UNIQUE INDEX contacts_phone_uq ON contacts (tenant_id, phone) WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX contacts_email_uq ON contacts (tenant_id, lower(email)) WHERE email IS NOT NULL;

-- Quem pediu para não ser contatado. Identidade = telefone (só dígitos) ou e-mail em minúsculas.
CREATE TABLE suppressions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  identity    text NOT NULL,
  reason      text NOT NULL CHECK (reason IN ('opt_out', 'manual', 'complaint')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, identity)
);

-- Sequências por gatilho, configuráveis pelo cliente. Sem linha, vale o padrão da plataforma.
CREATE TABLE recovery_sequences (
  tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  trigger_kind  text NOT NULL,
  enabled       boolean NOT NULL DEFAULT true,
  steps         jsonb NOT NULL,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, trigger_kind)
);

-- Textos de mensagem. No WhatsApp, fora da janela de 24 h só vale template aprovado pela Meta.
CREATE TABLE message_templates (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  key         text NOT NULL,
  channel     text NOT NULL DEFAULT 'whatsapp' CHECK (channel IN ('whatsapp')),
  body        text NOT NULL CHECK (length(body) BETWEEN 1 AND 1024),
  meta_status text NOT NULL DEFAULT 'draft'
              CHECK (meta_status IN ('draft', 'submitted', 'approved', 'rejected')),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, key)
);

CREATE TABLE recovery_cases (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id               uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id              uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  trigger_kind            text NOT NULL,
  external_ref            text NOT NULL,
  product_name            text NOT NULL DEFAULT '',
  amount_cents            bigint NOT NULL DEFAULT 0 CHECK (amount_cents >= 0),
  currency                text NOT NULL DEFAULT 'BRL',
  payment_url             text,
  status                  text NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'recovered', 'purchased', 'stopped', 'exhausted')),
  closed_reason           text,
  recovered_amount_cents  bigint,
  opened_at               timestamptz NOT NULL DEFAULT now(),
  closed_at               timestamptz,
  UNIQUE (tenant_id, trigger_kind, external_ref)
);
CREATE INDEX recovery_cases_open_idx ON recovery_cases (tenant_id, contact_id) WHERE status = 'open';

CREATE TABLE recovery_steps (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  case_id              uuid NOT NULL REFERENCES recovery_cases(id) ON DELETE CASCADE,
  step_no              int NOT NULL,
  template_key         text NOT NULL,
  scheduled_at         timestamptz NOT NULL,
  first_scheduled_at   timestamptz NOT NULL,
  -- 'sending' é gravado antes do envio: um passo nunca é enviado duas vezes (no máximo uma).
  status               text NOT NULL DEFAULT 'scheduled'
                       CHECK (status IN ('scheduled', 'sending', 'sent', 'skipped', 'canceled', 'failed')),
  detail               text,
  provider_message_id  text,
  sent_at              timestamptz,
  UNIQUE (case_id, step_no)
);
CREATE INDEX recovery_steps_due_idx ON recovery_steps (scheduled_at) WHERE status = 'scheduled';

-- Plataforma: produto vendido na Cakto/Hotmart → plano. Sem dado de cliente.
CREATE TABLE plan_products (
  provider            text NOT NULL CHECK (provider IN ('cakto', 'hotmart')),
  external_product_id text NOT NULL,
  plan_key            text NOT NULL REFERENCES plans(key),
  PRIMARY KEY (provider, external_product_id)
);

-- Eventos de compra do próprio SaaS (idempotência do provisionamento).
CREATE TABLE platform_events (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider    text NOT NULL,
  dedupe_key  text NOT NULL,
  event_type  text NOT NULL,
  buyer_email text,
  tenant_id   uuid REFERENCES tenants(id) ON DELETE SET NULL,
  outcome     text NOT NULL DEFAULT 'received',
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, dedupe_key)
);

-- ---------- RLS ----------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['tenant_settings', 'webhook_events', 'contacts', 'suppressions',
                           'recovery_sequences', 'message_templates', 'recovery_cases',
                           'recovery_steps']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY %I ON %I USING (tenant_id = app_tenant() OR app_system()) '
      'WITH CHECK (tenant_id = app_tenant() OR app_system())', t || '_rw', t);
  END LOOP;
END $$;

ALTER TABLE platform_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_events FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_events_sys ON platform_events
  USING (app_system()) WITH CHECK (app_system());

-- Rotina de plataforma (sem cliente no contexto) precisa achar a conexão pelo identificador público.
CREATE POLICY connections_system_lookup ON connections FOR SELECT USING (app_system());

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON tenant_settings, webhook_events, contacts, recovery_sequences,
      message_templates, recovery_cases, recovery_steps, platform_events TO fm_app;
    GRANT SELECT, INSERT ON suppressions TO fm_app;
    GRANT SELECT ON plan_products TO fm_app;
  END IF;
END $$;
