-- Follow-ups never send messages. Human review queue for long-term recovery.
-- One durable review task per skipped recovery step, scoped to the same tenant.
CREATE UNIQUE INDEX IF NOT EXISTS recovery_cases_tenant_id_uq
  ON recovery_cases (tenant_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS recovery_steps_tenant_id_uq
  ON recovery_steps (tenant_id, id);

CREATE TABLE recovery_followups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  case_id uuid NOT NULL,
  step_id uuid NOT NULL,
  reason text NOT NULL CHECK (reason IN (
    'evolution_outside_window', 'cloud_template_unavailable'
  )),
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'reviewed', 'dismissed', 'canceled')),
  created_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  closed_by uuid,
  UNIQUE (tenant_id, step_id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES recovery_cases (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, step_id) REFERENCES recovery_steps (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX recovery_followups_pending_idx
  ON recovery_followups (tenant_id, created_at DESC)
  WHERE status = 'pending';

ALTER TABLE recovery_followups ENABLE ROW LEVEL SECURITY;
ALTER TABLE recovery_followups FORCE ROW LEVEL SECURITY;
CREATE POLICY recovery_followups_tenant ON recovery_followups
  USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON recovery_followups TO fm_app;
  END IF;
END $$;
