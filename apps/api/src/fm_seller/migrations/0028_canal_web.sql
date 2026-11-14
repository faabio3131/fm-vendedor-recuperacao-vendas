-- 0028: canal "web" (widget de chat embutido em site, usado para o uso interno da própria FM
-- nos seus sites institucionais — FM, NFCore, Iron Fit, Kordena — e disponível para qualquer
-- tenant que queira o mesmo widget). Identidade por `channel:session_id`, igual a
-- Messenger/Instagram: sem telefone nem e-mail, contato "channel_only".

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
  ADD CONSTRAINT conversations_channel_check
  CHECK (channel IN ('whatsapp', 'messenger', 'instagram', 'web'));

DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'contact_channels'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%channel%'
  LOOP
    EXECUTE format('ALTER TABLE contact_channels DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE contact_channels
  ADD CONSTRAINT contact_channels_channel_check
  CHECK (channel IN ('messenger', 'instagram', 'web'));
