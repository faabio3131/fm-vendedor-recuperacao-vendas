# Operação

## Variáveis de ambiente (API)

Ver `apps/api/.env.example`. Segredos entram pelo painel do provedor de hospedagem, nunca no git.

## Primeiro ambiente

1. Criar o banco e rodar `scripts/db/bootstrap_roles.sql` com um usuário administrador.
2. `python -m fm_seller.cli gen-key` e guardar a chave em cofre. **Perder a chave = perder as
   credenciais dos clientes** (elas precisariam ser digitadas de novo).
3. `python -m fm_seller.cli migrate` com `FM_DATABASE_ADMIN_URL` (papel `fm_owner`).
4. Subir a API com `FM_DATABASE_URL` (papel `fm_app`).

## Criar um cliente manualmente (até o recebimento de compras Cakto/Hotmart existir)

`python -m fm_seller.cli create-tenant --name "Loja" --email dono@exemplo.com --plan fase-1`

O dono entra com a conta Google desse e-mail; o convite vale 30 dias.

## Rotação da chave de cifragem

Adicionar a nova chave **na frente** em `FM_SECRETS_KEYS` (`novo:base64,antigo:base64`). Valores novos
usam a nova chave; os antigos continuam legíveis. A re-cifragem em lote dos valores antigos ainda
não está implementada.
