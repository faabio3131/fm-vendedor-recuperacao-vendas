-- 0008: Messenger e Instagram. Contato de rede social não tem telefone nem e-mail: ele é
-- identificado pelo ID que a Meta entrega por canal (PSID no Messenger, IGSID no Instagram).

-- Conversas passam a aceitar os dois novos canais.
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'conversations'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%channel%'
  LOOP
    EXECUTE format('ALTER TABLE conversations DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE conversations
  ADD CONSTRAINT conversations_channel_check CHECK (channel IN ('whatsapp', 'messenger', 'instagram'));

-- Contato só de canal: sem telefone e sem e-mail, desde que tenha um ID de canal (tabela abaixo).
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'contacts'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%phone IS NOT NULL%'
  LOOP
    EXECUTE format('ALTER TABLE contacts DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE contacts ADD COLUMN channel_only boolean NOT NULL DEFAULT false;
ALTER TABLE contacts
  ADD CONSTRAINT contacts_identity_check CHECK (phone IS NOT NULL OR email IS NOT NULL OR channel_only);

CREATE TABLE contact_channels (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  contact_id   uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  channel      text NOT NULL CHECK (channel IN ('messenger', 'instagram')),
  external_id  text NOT NULL CHECK (length(external_id) BETWEEN 1 AND 100),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, channel, external_id),
  UNIQUE (contact_id, channel)
);

ALTER TABLE contact_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_channels FORCE ROW LEVEL SECURITY;
CREATE POLICY contact_channels_rw ON contact_channels USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON contact_channels TO fm_app;
  END IF;
END $$;
