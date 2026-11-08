-- Distinguish a definitive provider rejection from an unknown send outcome.
-- An uncertain attempt must never be resent without independent provider evidence.
ALTER TABLE messages DROP CONSTRAINT messages_status_check;
ALTER TABLE messages ADD CONSTRAINT messages_status_check CHECK (
  status IN ('received', 'queued', 'sending', 'sent', 'delivered', 'read', 'failed', 'uncertain')
);
-- Historical provider timeouts did not prove the message was unsent.
UPDATE messages
SET status = 'uncertain'
WHERE direction = 'out'
  AND status = 'failed'
  AND error LIKE 'resultado_incerto%';
