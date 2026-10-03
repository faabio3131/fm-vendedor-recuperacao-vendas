-- 0003: conversas, mensagens (com fila de saída), ofertas do cliente e ajustes do vendedor IA.

CREATE TABLE conversations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id       uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  channel          text NOT NULL DEFAULT 'whatsapp' CHECK (channel IN ('whatsapp')),
  -- bot: a IA responde · human: aguardando/atendido por pessoa · closed: encerrada
  status           text NOT NULL DEFAULT 'bot' CHECK (status IN ('bot', 'human', 'closed')),
  handoff_reason   text,
  last_inbound_at  timestamptz,
  last_message_at  timestamptz NOT NULL DEFAULT now(),
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, contact_id, channel)
);

CREATE TABLE messages (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id      uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  direction            text NOT NULL CHECK (direction IN ('in', 'out')),
  author               text NOT NULL CHECK (author IN ('customer', 'bot', 'human', 'recovery', 'system')),
  body                 text NOT NULL,
  -- Saída: queued → sending → sent → delivered/read, ou failed. O estado vem do provedor.
  status               text NOT NULL DEFAULT 'received'
                       CHECK (status IN ('received', 'queued', 'sending', 'sent', 'delivered', 'read', 'failed')),
  error                text,
  provider_message_id  text,
  -- Entrada ainda não tratada pelo vendedor IA.
  handled              boolean NOT NULL DEFAULT true,
  created_at           timestamptz NOT NULL DEFAULT now(),
  claimed_at           timestamptz
);
CREATE UNIQUE INDEX messages_provider_uq ON messages (tenant_id, provider_message_id)
  WHERE provider_message_id IS NOT NULL;
CREATE INDEX messages_conv_idx ON messages (conversation_id, created_at);
CREATE INDEX messages_outbox_idx ON messages (created_at) WHERE direction = 'out' AND status = 'queued';
CREATE INDEX messages_unhandled_idx ON messages (created_at) WHERE direction = 'in' AND NOT handled;

-- Ofertas: preço e link de pagamento SEMPRE vêm daqui, nunca do texto do modelo.
CREATE TABLE offers (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  description  text NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
  price_cents  bigint NOT NULL CHECK (price_cents >= 0),
  currency     text NOT NULL DEFAULT 'BRL',
  payment_url  text NOT NULL CHECK (payment_url ~ '^https://'),
  active       boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE tenant_settings
  ADD COLUMN ai_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN ai_persona text NOT NULL DEFAULT '' CHECK (length(ai_persona) <= 600);

-- Estado de entrega informado pelo WhatsApp para as mensagens de recuperação.
ALTER TABLE recovery_steps
  ADD COLUMN delivery_status text CHECK (delivery_status IN ('sent', 'delivered', 'read', 'failed')),
  ADD COLUMN delivery_error text;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['conversations', 'messages', 'offers']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY %I ON %I USING (tenant_id = app_tenant() OR app_system()) '
      'WITH CHECK (tenant_id = app_tenant() OR app_system())', t || '_rw', t);
  END LOOP;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON conversations, messages, offers TO fm_app;
    GRANT DELETE ON offers TO fm_app;
  END IF;
END $$;
