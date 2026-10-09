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
1. **Conta e servidor.** Criar conta no Hetzner, servidor **Ubuntu 24.04**, localização **Falkenstein, Nuremberg (Alemanha) ou Helsinki (Finlândia)**, plano **CX33** (4 CPUs e 8 GB) para começar (reduzir só depois de medir), com **IPv4**, **Backups do Hetzner ligados** e a sua **chave SSH** (nunca senha). **Não escolher os EUA:** no Hetzner o servidor equivalente lá custa cerca de 7 vezes mais (ver "Preço").
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

## Endurecimento (checklist de segurança do servidor)
Conferido ponto por ponto contra o que está em `deploy/hetzner/`:

| Ponto | Estado |
|---|---|
| Banco sem porta publicada (só rede interna do Docker) | **Já era assim**; só `80` e `443` são publicadas (pelo Caddy) |
| Firewall: o Docker ignora o UFW em porta publicada | Sem risco hoje: o único serviço que publica porta é o Caddy (80/443, já liberadas). **Regra para o futuro:** nunca acrescentar `ports:` a banco, API ou worker; se precisar de acesso de gerência, publicar só em `127.0.0.1` |
| Limites de CPU e memória por serviço | **Novo.** banco 2 GB, API 1 GB, painel 1 GB, worker 768 MB, Caddy 256 MB (soma ≈ 5,1 GB + 512 MB do passo de migração); um serviço que estoura é encerrado sozinho em vez de derrubar a máquina |
| Sem root dentro do contêiner | API e painel já tinham `USER` sem root. **Novo:** `no-new-privileges` em todos e `cap_drop: ALL` + `read_only` (com `/tmp` em memória) nos serviços da aplicação |
| Segredos | `.env` fora do Git (já era). **Novo:** `deploy.sh` recusa rodar se o `.env` não estiver com permissão `600` |
| SSH só por chave e sem root | **Já era** (`setup-server.sh`) |
| Bloqueio de tentativas (fail2ban) | **Novo:** regra explícita para o SSH (5 falhas, bloqueio de 1 hora) |
| Rotação de logs | **Novo:** nos dois lugares, `daemon.json` do Docker e no compose (20 MB × 3 arquivos por serviço) |

**Não testado em contêiner de verdade** (sem Docker no ambiente de teste): `read_only`, `cap_drop` e os limites. No primeiro servidor, conferir com `docker compose ps` e `docker stats`; se um serviço não subir por causa de `read_only`, retirar só essa linha daquele serviço e anotar aqui. O banco e o Caddy ficaram **sem** `cap_drop` e sem `read_only` de propósito (precisam ajustar permissões ao subir) e só têm `no-new-privileges`.
Conferência de fora, depois de subir: `ss -tlnp` no servidor deve mostrar escutando para fora só `22`, `80` e `443`.

## Vários produtos na mesma máquina (futuro; hoje só o AtendeVendeIA)
Só depois de **medir** o consumo do AtendeVendeIA por algumas semanas. Regras para entrar um segundo produto:
- projeto Docker Compose próprio, **rede interna própria** e **Postgres próprio** (nenhum produto enxerga o banco do outro);
- credenciais, chave de cifragem e `.env` próprios por produto;
- um único Caddy na frente (rede compartilhada só com ele), um domínio por produto;
- soma dos limites de memória dentro da RAM da máquina, com folga;
- backup e teste de restauração por produto.
Os produtos que já têm infraestrutura própria (como o Kordena) não entram sem decisão separada.

## Riscos aceitos e limites
- **Um servidor só:** se cair, o sistema cai até voltar. Backup e restauração testados reduzem o dano, não a queda.
- **Operação é nossa:** atualizações do sistema operacional são automáticas (só segurança), mas Docker e imagens exigem `deploy.sh` periódico.
- **Memória não medida:** começar com 8 GB e reduzir depois de ver o consumo real.
- **Latência:** servidor na Europa responde mais devagar para quem está no Brasil (ordem de 200 ms por pedido, não medida). Webhooks e WhatsApp não sentem; o painel fica um pouco menos ágil.

## Preço (fonte oficial, conferida em 08/10/2026)
Tabela do Hetzner "Price Adjustment 15 June 2026" (https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/), valores **sem IVA e sem IPv4**:

| Plano | Alemanha / Finlândia | EUA (Ashburn / Hillsboro) |
|---|---|---|
| CX23 (2 CPUs, 4 GB) | €5,49 (US$6,49) | não existe |
| **CX33 (4 CPUs, 8 GB)** | **€8,49 (US$9,99)** | não existe |
| CPX21 (3 CPUs, 4 GB) | n/d | €31,99 (US$37,49) |
| CPX31 (4 CPUs, 8 GB) | n/d | €62,49 (US$73,49) |

Falta confirmar no console, ao criar a conta: preço do IPv4, preço do Backup do Hetzner, se há IVA para empresa/pessoa do Brasil e disponibilidade (as páginas de plano mostravam "currently unavailable" para vários itens; pode ser só a exibição).

