-- Para bancos gerenciados com UM usuário só (ex.: Render): o usuário padrão do provedor faz o papel
-- de DONO (roda as migrations e o backup, via FM_DATABASE_ADMIN_URL) e este script cria só o papel
-- do app, `fm_app`: sem superusuário e sem bypass de RLS (é isso que isola os clientes).
--
-- Rodar UMA vez, ANTES da primeira migration (as migrations só dão permissão a fm_app se ele existir):
--   psql "$URL_DO_USUARIO_PADRAO" -v app_pw="SENHA_FORTE_NOVA" -f scripts/db/bootstrap_app_role.sql
-- Se der "permission denied to create role", o usuário padrão não pode criar papéis: não siga adiante
-- com este banco (rodar a API como dono desliga o isolamento por cliente). Ver docs/STAGING_RENDER.md.
SELECT format('CREATE ROLE fm_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS', :'app_pw')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fm_app') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO fm_app', current_database()) \gexec

-- Confere o resultado: fm_app existe, não é superusuário e não ignora RLS.
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'fm_app';
