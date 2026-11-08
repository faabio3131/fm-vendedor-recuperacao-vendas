-- Additive import support: menu categories and daily AI vision budget per tenant.
ALTER TABLE offers ADD COLUMN category text NOT NULL DEFAULT ''
  CHECK (char_length(category) <= 80);

CREATE TABLE menu_import_usage (
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  day date NOT NULL,
  used integer NOT NULL DEFAULT 0 CHECK (used BETWEEN 0 AND 20),
  PRIMARY KEY (tenant_id, day)
);
CREATE INDEX menu_import_usage_day_idx ON menu_import_usage(day);
ALTER TABLE menu_import_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE menu_import_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY menu_import_usage_tenant ON menu_import_usage
  USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON menu_import_usage TO fm_app;
  END IF;
END $$;
