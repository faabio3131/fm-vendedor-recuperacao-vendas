-- Cria os dois papéis do banco. Rodar uma vez por ambiente, com um usuário administrador:
--   psql "$ADMIN_URL" -v owner_pw="..." -v app_pw="..." -f scripts/db/bootstrap_roles.sql
-- fm_owner: dono das tabelas, usado só por migrations e pela CLI.
-- fm_app:   usado pela API. Sem superusuário e sem bypass de RLS (é isso que isola os clientes).
SELECT format('CREATE ROLE fm_owner LOGIN PASSWORD %L CREATEDB', :'owner_pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_owner') \gexec
SELECT format('CREATE ROLE fm_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'app_pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') \gexec
