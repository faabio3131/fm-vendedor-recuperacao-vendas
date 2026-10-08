# Produção em um servidor (Hetzner ou outro com Docker)

**Estado:** arquivos prontos em `deploy/hetzner/`, **ainda não usados**. O staging continua no Render. Nada aqui altera o que roda hoje.

## O que foi testado e o que NÃO foi
| Parte | Estado |
|---|---|
| Backup cifrado (`age`), checksum, retenção, falha sem arquivo parcial | **Testado** num Postgres 16 local com as migrations reais (`scripts/selftest-backup.sh`) |
| Restauração para banco novo + conferência (migrations, tabelas, RLS forçada, clientes) | **Testado** |
| Recusas: chave errada, arquivo adulterado, restaurar por cima do banco em uso, nome inválido | **Testado** |
| `docker-compose.prod.yml` | Validado só na sintaxe (`docker compose config`); **não subiu** (sem Docker no ambiente de teste) |
| Caddy (HTTPS), `setup-server.sh`, `deploy.sh`, `rollback.sh`, cron | **Não executados**: serão provados no primeiro servidor, antes de entrar cliente |

## Arquitetura
Um servidor, tudo em contêineres: Postgres 16 (sem porta pública), `bootstrap` (migrations a cada subida), API, worker, painel e Caddy (HTTPS automático nas portas 80/443). Painel em `APP_HOST` e API em `API_HOST` (subdomínios do mesmo site). Firewall: só 22, 80 e 443.

## Passo a passo (primeira vez)
1. **Conta e servidor.** Criar conta no Hetzner, servidor **Ubuntu 24.04**, localização **Ashburn (EUA)**, plano de **4 CPUs e 8 GB** para começar (reduzir só depois de medir), com **IPv4**, **Backups do Hetzner ligados** e a sua **chave SSH** (nunca senha).
2. **DNS (Cloudflare).** Registros `A` para `app.` e `api.` apontando para o IP do servidor, com a nuvem **cinza (somente DNS)**.
3. **Preparar o servidor (uma vez):** como root, `bash deploy/hetzner/scripts/setup-server.sh` (atualizações automáticas, firewall, SSH só por chave, swap, Docker, agendamentos).
4. **Código:** como `fmapp`, `git clone https://github.com/faabio3131/fm-vendedor-recuperacao-vendas /opt/fm-seller`.
5. **Segredos:** criar `/opt/fm-seller/.env` a partir de `deploy/hetzner/.env.example` (`chmod 600`). Gerar senhas com `openssl rand -hex 24` e a chave de cifragem com o comando `gen-key` indicado no modelo.
6. **Chaves do backup:** no seu computador, `age-keygen -o chave-backup.txt`. Só a linha **Public key** vai para `AGE_RECIPIENT` no `.env`. **A chave privada fica com você (cofre/gerenciador de senhas), fora do servidor.** Para o teste semanal automático, copie-a para `/opt/fm-seller/restore-key.txt` (`chmod 600`) ou rode o teste manualmente.
7. **Guardar fora do servidor:** `FM_SECRETS_KEYS` e a chave privada do backup. Perder `FM_SECRETS_KEYS` perde as credenciais dos clientes.
8. **Subir:** `cd /opt/fm-seller/deploy/hetzner && docker compose --env-file /opt/fm-seller/.env -f docker-compose.prod.yml up -d --build`.
9. **Google:** acrescentar `https://app.…` nas origens autorizadas do cliente OAuth e publicar o app (ver P1 em `docs/PENDENCIAS_EXTERNAS.md`).
10. **Provas antes de entrar cliente:** `https://api.…/v1/health` responde; login real com um Gmail fora da lista de teste; `scripts/backup.sh` e depois `RESTORE_KEY=… scripts/restore-test.sh` com saída `restore-test OK`; reiniciar o servidor e conferir que tudo volta sozinho.

## Rotina
- **Atualizar:** `scripts/deploy.sh` (faz backup antes, atualiza, sobe e confere `/v1/health`).
- **Voltar o código:** `scripts/rollback.sh`. O banco não é desfeito; as migrations são só aditivas. Se uma migration causar dano, restaurar o backup.
- **Backup:** todo dia 03:10 UTC, 14 dias de retenção no servidor; para cópia fora da máquina, configurar `RCLONE_REMOTE`. Teste de restauração aos domingos.
- **Alertas:** `ops-check` a cada 5 minutos; com `ALERT_URL` configurado, falhas de backup, de restauração, de deploy e do `ops-check` geram aviso.

## Restaurar um desastre
1. `scripts/restore.sh <arquivo.dump.age> <chave-privada> fm_seller_restaurado` (cria um banco novo e confere o checksum).
2. Parar API e worker, trocar o banco: renomear o banco em uso e o restaurado (`ALTER DATABASE … RENAME TO …`), subir de novo e conferir `/v1/health`.
3. Só apagar o banco antigo depois de confirmar com clientes reais.

## Riscos aceitos e limites
- **Um servidor só:** se cair, o sistema cai até voltar. Backup e restauração testados reduzem o dano, não a queda.
- **Operação é nossa:** atualizações do sistema operacional são automáticas (só segurança), mas Docker e imagens exigem `deploy.sh` periódico.
- **Memória não medida:** começar com 8 GB e reduzir depois de ver o consumo real.
- O preço do Hetzner não foi confirmado em fonte oficial (ver a conversa de 08/10/2026): confira no console antes de contratar.
