-- Tabelas com RLS que o modo sistema (app.system=on) NÃO consegue ler por inteiro.
-- O backup lê o banco nesse modo; uma tabela listada aqui sairia vazia ou incompleta do dump,
-- sem aviso do pg_dump. O backup.sh só aceita as exceções que ele mesmo declara.
SELECT c.relname
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relkind = 'r'
   AND c.relrowsecurity
   AND NOT EXISTS (SELECT 1 FROM pg_policies p
                    WHERE p.schemaname = 'public' AND p.tablename = c.relname
                      AND p.cmd IN ('ALL', 'SELECT') AND p.qual LIKE '%app_system()%')
 ORDER BY c.relname;
