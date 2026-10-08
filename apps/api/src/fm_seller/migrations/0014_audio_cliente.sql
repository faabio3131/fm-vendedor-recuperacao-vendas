-- Áudio do cliente: a mensagem entra como pendente e o worker troca o texto pela transcrição.
-- Aditiva. O áudio em si NUNCA é guardado: só a referência da Meta (apagada ao terminar) e o texto.
ALTER TABLE messages
  ADD COLUMN media_id       text,
  ADD COLUMN media_status   text CHECK (media_status IN ('pending', 'done', 'failed')),
  ADD COLUMN media_attempts smallint NOT NULL DEFAULT 0;
CREATE INDEX messages_media_pending_idx ON messages (created_at) WHERE media_status = 'pending';
