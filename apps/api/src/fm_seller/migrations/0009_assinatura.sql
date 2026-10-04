-- 0009: ciclo de vida da assinatura do cliente.
-- Estados: active · past_due (em atraso, ainda dentro da carência) · suspended (carência acabou ou
-- suspensão manual) · canceled · refunded. Os três últimos pausam envios e vendedor IA, sem apagar dado.

DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'tenant_plans'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) LIKE '%past_due%'
  LOOP
    EXECUTE format('ALTER TABLE tenant_plans DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE tenant_plans
  ADD CONSTRAINT tenant_plans_status_check
  CHECK (status IN ('active', 'past_due', 'suspended', 'canceled', 'refunded'));
ALTER TABLE tenant_plans
  ADD COLUMN status_since timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN past_due_since timestamptz;

-- Carência (dias em atraso antes de suspender) é dado do plano, não código.
ALTER TABLE plans
  ADD COLUMN grace_days smallint NOT NULL DEFAULT 3 CHECK (grace_days BETWEEN 0 AND 60);
