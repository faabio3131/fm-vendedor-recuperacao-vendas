-- Teste grátis: 7 dias OU 200 envios automáticos (o que ocorrer primeiro).
-- Só muda a política; não inicia nenhum teste e não altera testes já em andamento
-- (cada teste guarda o limite que recebeu ao começar).
SELECT set_config('app.system', 'on', true);
UPDATE trial_policy SET message_cap = 200, warn_at = 160, updated_at = now() WHERE singleton;
