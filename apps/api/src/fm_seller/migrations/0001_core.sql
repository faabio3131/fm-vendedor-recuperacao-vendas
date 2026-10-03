-- 0001_core: identidade, clientes (tenants), planos, conexões e auditoria.
-- Isolamento por cliente: RLS forçada em toda tabela com dados de cliente.
-- O contexto da requisição vem de variáveis de transação (app.*), definidas só pelo código de db.py.

CREATE FUNCTION app_tenant() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION app_user() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;
CREATE FUNCTION app_sub() RETURNS text LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.google_sub', true), '') $$;
CREATE FUNCTION app_email() RETURNS text LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.login_email', true), '') $$;
CREATE FUNCTION app_session_hash() RETURNS text LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('app.session_hash', true), '') $$;
CREATE FUNCTION app_system() RETURNS boolean LANGUAGE sql STABLE AS
  $$ SELECT coalesce(current_setting('app.system', true), '') = 'on' $$;

CREATE TABLE tenants (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  google_sub  text NOT NULL UNIQUE,
  email       text NOT NULL,
  name        text NOT NULL DEFAULT '',
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX users_email_idx ON users (lower(email));

CREATE TABLE memberships (
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role        text NOT NULL CHECK (role IN ('owner', 'admin', 'agent')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE pending_invites (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email       text NOT NULL,
  role        text NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'admin', 'agent')),
  expires_at  timestamptz NOT NULL,
  accepted_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pending_invites_email_idx ON pending_invites (lower(email));

CREATE TABLE sessions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash  text NOT NULL UNIQUE,
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Planos: dado, não código. Cada plano libera um conjunto de recursos (features).
CREATE TABLE plans (
  key           text PRIMARY KEY,
  display_name  text NOT NULL,
  phase         int  NOT NULL,
  features      text[] NOT NULL,
  active        boolean NOT NULL DEFAULT true
);

CREATE TABLE tenant_plans (
  tenant_id     uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  plan_key      text NOT NULL REFERENCES plans(key),
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'past_due', 'canceled')),
  source        text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'cakto', 'hotmart')),
  external_ref  text,
  started_at    timestamptz NOT NULL DEFAULT now()
);

-- Conexões do cliente (WhatsApp, Meta, Cakto...). Credenciais ficam cifradas.
CREATE TABLE connections (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider          text NOT NULL,
  public_id         text NOT NULL UNIQUE,
  config_encrypted  bytea NOT NULL,
  config_hint       jsonb NOT NULL DEFAULT '{}'::jsonb,
  status            text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'connected', 'needs_attention', 'disconnected')),
  last_verified_at  timestamptz,
  last_error        text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, provider)
);

CREATE TABLE audit_log (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  actor_user_id  uuid,
  action         text NOT NULL,
  target         text NOT NULL DEFAULT '',
  detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
  at             timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS
  $$ BEGIN RAISE EXCEPTION 'audit_log é somente de inserção'; END $$;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

-- ---------- RLS ----------
ALTER TABLE tenants         ENABLE ROW LEVEL SECURITY; ALTER TABLE tenants         FORCE ROW LEVEL SECURITY;
ALTER TABLE users           ENABLE ROW LEVEL SECURITY; ALTER TABLE users           FORCE ROW LEVEL SECURITY;
ALTER TABLE memberships     ENABLE ROW LEVEL SECURITY; ALTER TABLE memberships     FORCE ROW LEVEL SECURITY;
ALTER TABLE pending_invites ENABLE ROW LEVEL SECURITY; ALTER TABLE pending_invites FORCE ROW LEVEL SECURITY;
ALTER TABLE sessions        ENABLE ROW LEVEL SECURITY; ALTER TABLE sessions        FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_plans    ENABLE ROW LEVEL SECURITY; ALTER TABLE tenant_plans    FORCE ROW LEVEL SECURITY;
ALTER TABLE connections     ENABLE ROW LEVEL SECURITY; ALTER TABLE connections     FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_log       ENABLE ROW LEVEL SECURITY; ALTER TABLE audit_log       FORCE ROW LEVEL SECURITY;

CREATE POLICY memberships_rw ON memberships
  USING (tenant_id = app_tenant() OR user_id = app_user() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());

CREATE POLICY tenants_sel ON tenants FOR SELECT
  USING (id = app_tenant()
         OR id IN (SELECT tenant_id FROM memberships WHERE user_id = app_user())
         OR app_system());
CREATE POLICY tenants_ins ON tenants FOR INSERT WITH CHECK (app_system());
CREATE POLICY tenants_upd ON tenants FOR UPDATE
  USING (app_system()) WITH CHECK (app_system());

CREATE POLICY users_sel ON users FOR SELECT
  USING (id = app_user() OR google_sub = app_sub() OR app_system()
         OR id IN (SELECT user_id FROM memberships WHERE tenant_id = app_tenant()));
CREATE POLICY users_ins ON users FOR INSERT WITH CHECK (google_sub = app_sub() OR app_system());
CREATE POLICY users_upd ON users FOR UPDATE
  USING (id = app_user()) WITH CHECK (id = app_user());

CREATE POLICY invites_rw ON pending_invites
  USING (lower(email) = lower(app_email()) OR tenant_id = app_tenant() OR app_system())
  WITH CHECK (lower(email) = lower(app_email()) OR tenant_id = app_tenant() OR app_system());

CREATE POLICY sessions_sel ON sessions FOR SELECT
  USING (token_hash = app_session_hash() OR user_id = app_user());
CREATE POLICY sessions_ins ON sessions FOR INSERT WITH CHECK (user_id = app_user());
CREATE POLICY sessions_upd ON sessions FOR UPDATE
  USING (user_id = app_user()) WITH CHECK (user_id = app_user());

CREATE POLICY tenant_plans_sel ON tenant_plans FOR SELECT
  USING (tenant_id = app_tenant() OR app_system());
CREATE POLICY tenant_plans_wr ON tenant_plans FOR ALL
  USING (app_system()) WITH CHECK (app_system());

CREATE POLICY connections_rw ON connections
  USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());

CREATE POLICY audit_sel ON audit_log FOR SELECT
  USING (tenant_id = app_tenant() OR app_system());
CREATE POLICY audit_ins ON audit_log FOR INSERT
  WITH CHECK (tenant_id = app_tenant() OR app_system());

-- ---------- Planos iniciais (editáveis como dado) ----------
INSERT INTO plans (key, display_name, phase, features) VALUES
  ('fase-1', 'Atender, vender e recuperar', 1, ARRAY[
     'channel.whatsapp', 'channel.messenger', 'channel.instagram',
     'checkout.cakto', 'checkout.hotmart', 'ai.seller', 'ai.custom_key', 'recovery.sequences']),
  ('fase-2', 'Cobrança própria', 2, ARRAY[
     'channel.whatsapp', 'channel.messenger', 'channel.instagram',
     'checkout.cakto', 'checkout.hotmart', 'ai.seller', 'ai.custom_key', 'recovery.sequences',
     'billing.gateway', 'billing.collections']),
  ('fase-3', 'Meta Ads', 3, ARRAY[
     'channel.whatsapp', 'channel.messenger', 'channel.instagram',
     'checkout.cakto', 'checkout.hotmart', 'ai.seller', 'ai.custom_key', 'recovery.sequences',
     'billing.gateway', 'billing.collections', 'ads.meta']),
  ('fase-4', 'Google Ads', 4, ARRAY[
     'channel.whatsapp', 'channel.messenger', 'channel.instagram',
     'checkout.cakto', 'checkout.hotmart', 'ai.seller', 'ai.custom_key', 'recovery.sequences',
     'billing.gateway', 'billing.collections', 'ads.meta', 'ads.google']);

-- ---------- Permissões do papel do app (sem dono, sem bypass de RLS) ----------
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT USAGE ON SCHEMA public TO fm_app;
    GRANT SELECT, INSERT, UPDATE ON tenants, users, sessions, memberships, pending_invites,
      tenant_plans TO fm_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON connections TO fm_app;
    GRANT SELECT ON plans TO fm_app;
    GRANT SELECT, INSERT ON audit_log TO fm_app;
  END IF;
END $$;
