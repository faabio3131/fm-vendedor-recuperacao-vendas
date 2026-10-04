-- Lista as tabelas com coluna tenant_id que NÃO têm RLS ativada e forçada.
-- Resultado vazio = isolamento por cliente íntegro. Usado pelo restore_check.sh e pelos testes.
SELECT c.relname
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relkind = 'r'
   AND EXISTS (SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped)
   AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
 ORDER BY c.relname;
