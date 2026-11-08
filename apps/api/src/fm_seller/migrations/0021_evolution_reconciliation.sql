-- Safe additive state for read-only reconciliation of ambiguous Evolution sends.
-- Never sends messages a second time. The scheduler rechecks at most every 10m.
ALTER TABLE messages
  ADD COLUMN evolution_reconcile_checked_at TIMESTAMPTZ;
ALTER TABLE messages
  ADD COLUMN evolution_reconcile_attempts INTEGER NOT NULL DEFAULT 0
  CHECK (evolution_reconcile_attempts >= 0);

CREATE INDEX idx_messages_evolution_uncertain_reconcile
  ON messages (evolution_reconcile_checked_at, created_at)
  WHERE direction = 'out' AND author = 'bot'
    AND status = 'uncertain' AND provider_message_id IS NULL;
