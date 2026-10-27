-- Base de conhecimento do vendedor IA: perguntas e respostas que o próprio cliente cadastra.
-- A IA responde dúvidas comuns a partir daqui (sem inventar) e só transfere para uma pessoa
-- quando a resposta não está nem aqui nem nas ofertas e o cliente insiste. Aditiva.
CREATE TABLE knowledge_items (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  question    text NOT NULL CHECK (length(question) BETWEEN 3 AND 200),
  answer      text NOT NULL CHECK (length(answer) BETWEEN 1 AND 700),
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX knowledge_items_tenant_idx ON knowledge_items (tenant_id, created_at);

ALTER TABLE knowledge_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_items FORCE ROW LEVEL SECURITY;
CREATE POLICY knowledge_items_rw ON knowledge_items
  USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_items TO fm_app;
  END IF;
END $$;
