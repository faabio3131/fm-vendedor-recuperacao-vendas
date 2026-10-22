-- Commercial Launch: three usage-tiered plans with identical existing feature set.
-- Historic fase-1..4 rows and existing subscriptions are preserved unchanged.
ALTER TABLE plans
 ADD COLUMN price_monthly_cents integer CHECK (price_monthly_cents IS NULL OR price_monthly_cents > 0);

INSERT INTO plans (key, display_name, phase, features, limits, active, price_monthly_cents)
SELECT candidate.key, candidate.name, candidate.phase, base.features,
       jsonb_build_object('ai_replies_per_month', candidate.quota), true, candidate.price
FROM plans AS base
CROSS JOIN (
 VALUES
 ('av-essencial', 'Essencial', 10, 500, 12990),
 ('av-profissional', 'Profissional', 11, 5000, 19990),
 ('av-escala', 'Escala', 12, 15000, 39990)
) AS candidate(key, name, phase, quota, price)
WHERE base.key = 'fase-1'
ON CONFLICT (key) DO NOTHING;

-- Immutable actor + old/new commercial values for every operator change.
CREATE TABLE platform_plan_changes (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor_user_id uuid NOT NULL REFERENCES users(id),
 plan_key text NOT NULL REFERENCES plans(key),
 old_price_cents integer NOT NULL,
 new_price_cents integer NOT NULL,
 old_quota integer NOT NULL,
 new_quota integer NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE platform_plan_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_plan_changes FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_plan_changes_system ON platform_plan_changes
 USING (app_system()) WITH CHECK (app_system());

DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fm_app') THEN
   GRANT UPDATE (price_monthly_cents, limits) ON plans TO fm_app;
   GRANT SELECT, INSERT ON platform_plan_changes TO fm_app;
   GRANT USAGE ON SEQUENCE platform_plan_changes_id_seq TO fm_app;
 END IF;
END $$;
