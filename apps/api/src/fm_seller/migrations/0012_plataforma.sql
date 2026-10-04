-- 0012: administração da plataforma (F&M). Quem opera os clientes não é dono de nenhum cliente:
-- é um papel à parte, concedido só por convite criado pela linha de comando (`create-platform-admin`).

CREATE TABLE platform_admins (
  user_id     uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- Convite para virar administrador: vale para o e-mail do login Google, expira e só vale uma vez.
CREATE TABLE platform_admin_invites (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email        text NOT NULL,
  expires_at   timestamptz NOT NULL,
  accepted_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX platform_admin_invites_email_idx ON platform_admin_invites (lower(email));

ALTER TABLE platform_admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_admins FORCE ROW LEVEL SECURITY;
ALTER TABLE platform_admin_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_admin_invites FORCE ROW LEVEL SECURITY;

-- Cada pessoa só enxerga a própria linha. Só entra como administrador quem tem convite vigente para
-- o e-mail do login (ou a rotina de plataforma, pela linha de comando).
CREATE POLICY platform_admins_sel ON platform_admins FOR SELECT
  USING (user_id = app_user() OR app_system());
CREATE POLICY platform_admins_ins ON platform_admins FOR INSERT
  WITH CHECK (
    app_system()
    OR (user_id = app_user() AND EXISTS (
          SELECT 1 FROM platform_admin_invites i
          WHERE lower(i.email) = lower(app_email()) AND i.accepted_at IS NULL
            AND i.expires_at > now()))
  );
CREATE POLICY platform_admins_del ON platform_admins FOR DELETE USING (app_system());
CREATE POLICY platform_admin_invites_rw ON platform_admin_invites
  USING (lower(email) = lower(app_email()) OR app_system())
  WITH CHECK (lower(email) = lower(app_email()) OR app_system());

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT ON platform_admins TO fm_app;
    GRANT SELECT, UPDATE ON platform_admin_invites TO fm_app;
  END IF;
END $$;
