-- Batimento do worker: permite alertar quando o worker para. Tabela de plataforma (sem cliente):
-- só rotina de sistema lê e escreve, como platform_events.
CREATE TABLE worker_heartbeat (
  worker        text PRIMARY KEY,
  last_cycle_at timestamptz NOT NULL,
  cycles        bigint NOT NULL DEFAULT 0,
  last_error    text
);

ALTER TABLE worker_heartbeat ENABLE ROW LEVEL SECURITY;
ALTER TABLE worker_heartbeat FORCE ROW LEVEL SECURITY;
CREATE POLICY worker_heartbeat_sys ON worker_heartbeat
  USING (app_system()) WITH CHECK (app_system());

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON worker_heartbeat TO fm_app;
  END IF;
END $$;
