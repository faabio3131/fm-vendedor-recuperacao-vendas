# ADR-0003 — Administração da plataforma: a exceção controlada à regra 4

Status: ADOTADA no Bloco 20 (prompt mestre 16 a 20, 04/10/2026); aguarda ciência do Diretor · Data: 2026-10-04

## Contexto

A regra 4 do `AGENTS.md` diz que o modo `system` do banco serve só para rotinas de plataforma, "nunca em rota de usuário".
Para operar vários clientes (ver quem está atrasado, o que está falhando, suspender, trocar plano, renovar convite) a F&M
precisa enxergar **todos** os clientes de uma vez, o que a RLS por cliente impede de propósito. O prompt do Bloco 20 pediu
uma área de administração da plataforma, com papel separado do dono de cliente, acessível só por convite criado pela linha de
comando e fora de qualquer rota de cliente.

## Decisão

As rotas de `/v1/admin/*` são **as únicas rotas de pessoa** que usam o modo `system`, e só com estas cercas:

1. **Papel próprio.** `platform_admins` (uma linha por pessoa), separado de `memberships`. Ser dono de um cliente não dá acesso;
   ser administrador não dá acesso a nenhum cliente.
2. **Só por convite da linha de comando.** `cli create-platform-admin --email ...` (papel dono do banco) cria um convite que vale
   uma vez, expira (7 dias) e só casa com o e-mail verificado do login Google. O banco recusa (RLS) quem tenta se inserir sem
   convite vigente; nenhuma rota de cliente ou de administrador concede o papel.
3. **Rota própria.** `get_admin` valida a sessão **e** a linha em `platform_admins` antes de qualquer consulta; 401 sem sessão, 403
   sem o papel. A pessoa nem precisa pertencer a um cliente.
4. **Colunas escolhidas.** Cada consulta é texto fixo com colunas listadas à mão: nunca texto de conversa, telefone ou e-mail de
   contato, nem credencial. O e-mail do dono do cliente sai mascarado. Um teste confere que nada disso aparece nas respostas.
5. **Ações auditadas.** Suspender, reativar, trocar plano e renovar convite gravam na auditoria **do cliente afetado**, com o
   usuário que agiu (`platform.*`). Não há ação que apague dado nem que leia conteúdo.
6. **Limites.** Escritas passam pela guarda de origem e pelo limite de taxa "sensível" por conta.

## Consequências

- O modo `system` em rota continua sendo risco: um erro numa consulta do `platform_admin.py` seria um vazamento entre clientes.
  Por isso o arquivo é pequeno, sem SQL montado a partir de entrada, e qualquer coluna nova passa por revisão e pelo teste de vazamento.
- Revogar é `cli revoke-platform-admin`. Não há convite por e-mail: o convite vale no primeiro login com o e-mail informado.
- Se o Diretor preferir não ter essa exceção, basta não criar nenhum administrador: sem linha em `platform_admins` as rotas
  respondem 403 para todo mundo. As consultas continuam possíveis pela linha de comando (`cli ops-check`, `cli metrics`).
