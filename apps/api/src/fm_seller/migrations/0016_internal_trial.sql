-- FM Tecnologia internal dogfooding trial: seven days OR 500 automated sends, whichever comes first.
-- Nothing here enrolls an existing tenant, turns on messaging, or publishes the trial.
-- Paid plans, legacy customers and their historical values are preserved.
INSERT INTO plans (key, display_name, phase, features, limits, active, price_monthly_cents)
SELECT 'av-trial', 'Teste grátis', 8, features,
       jsonb_build_object('ai_replies_per_month', 500), false, NULL
FROM plans WHERE key = 'fase-1'
ON CONFLICT (key) DO NOTHING;

CREATE TABLE trial_policy (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  duration_days integer NOT NULL CHECK (duration_days BETWEEN 1 AND 90),
  message_cap integer NOT NULL CHECK (message_cap BETWEEN 1 AND 1000000),
  warn_at integer NOT NULL CHECK (warn_at >= 1 AND warn_at < message_cap),
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO trial_policy (singleton, duration_days, message_cap, warn_at)
VALUES (true, 7, 500, 400);

ALTER TABLE tenant_plans
  ADD COLUMN trial_started_at timestamptz,
  ADD COLUMN trial_expires_at timestamptz,
  ADD COLUMN trial_message_cap integer,
  ADD COLUMN trial_warn_at integer,
  ADD COLUMN trial_messages_sent integer NOT NULL DEFAULT 0 CHECK (trial_messages_sent >= 0),
  ADD COLUMN trial_messages_reserved integer NOT NULL DEFAULT 0 CHECK (trial_messages_reserved >= 0),
  ADD CONSTRAINT tenant_plans_trial_window_chk CHECK (
    (trial_started_at IS NULL AND trial_expires_at IS NULL AND trial_message_cap IS NULL
     AND trial_warn_at IS NULL AND trial_messages_sent = 0 AND trial_messages_reserved = 0)
    OR (trial_started_at IS NOT NULL AND trial_expires_at > trial_started_at
        AND trial_message_cap > 0 AND trial_warn_at > 0
        AND trial_warn_at < trial_message_cap
        AND trial_messages_sent + trial_messages_reserved <= trial_message_cap)
  );

-- One reservation per provider-bound message/step. Uncertain results conservatively
-- consume the slot; a confirmed failure releases it. No automatic retries.
CREATE TABLE trial_message_ledger (
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  source_kind text NOT NULL CHECK (source_kind IN ('bot', 'recovery')),
  source_id uuid NOT NULL,
  status text NOT NULL CHECK (status IN ('reserved', 'sent', 'failed', 'uncertain')),
  reserved_at timestamptz NOT NULL DEFAULT now(),
  finalized_at timestamptz,
  PRIMARY KEY (tenant_id, source_kind, source_id)
);

ALTER TABLE trial_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE trial_policy FORCE ROW LEVEL SECURITY;
CREATE POLICY trial_policy_read ON trial_policy FOR SELECT USING (true);
CREATE POLICY trial_policy_system ON trial_policy FOR ALL
  USING (app_system()) WITH CHECK (app_system());

ALTER TABLE trial_message_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE trial_message_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY trial_message_ledger_system ON trial_message_ledger
  USING (app_system()) WITH CHECK (app_system());

DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fm_app') THEN
   GRANT SELECT, UPDATE ON trial_policy TO fm_app;
   GRANT SELECT, INSERT, UPDATE ON trial_message_ledger TO fm_app;
 END IF;
END $$;
