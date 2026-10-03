-- 0005: limites por plano (dado), medição de uso da IA e limite diário de contatos novos por número.

-- Limites do plano. Chaves reconhecidas: ai_replies_per_month. Ausente = sem limite.
-- Valores abaixo são PROVISÓRIOS (decisão comercial do Fábio) e podem ser editados como dado.
ALTER TABLE plans ADD COLUMN limits jsonb NOT NULL DEFAULT '{}';
UPDATE plans SET limits = '{"ai_replies_per_month": 1000}' WHERE key IN ('fase-1', 'fase-2', 'fase-3', 'fase-4');

-- Uso da IA por dia (no fuso do cliente). Sem texto de conversa: só contagens.
CREATE TABLE ai_usage (
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  day         date NOT NULL,
  calls       int NOT NULL DEFAULT 0,
  failures    int NOT NULL DEFAULT 0,
  tokens_in   bigint NOT NULL DEFAULT 0,
  tokens_out  bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, day)
);
ALTER TABLE ai_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_usage_rw ON ai_usage
  USING (tenant_id = app_tenant() OR app_system())
  WITH CHECK (tenant_id = app_tenant() OR app_system());

-- A Meta limita quantos contatos novos o número pode abordar por 24 h (começa em 250).
-- Padrão abaixo disso; o cliente ajusta conforme a camada do número dele.
ALTER TABLE tenant_settings
  ADD COLUMN number_daily_limit int NOT NULL DEFAULT 200 CHECK (number_daily_limit BETWEEN 1 AND 100000);

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') THEN
    GRANT SELECT, INSERT, UPDATE ON ai_usage TO fm_app;
  END IF;
END $$;
