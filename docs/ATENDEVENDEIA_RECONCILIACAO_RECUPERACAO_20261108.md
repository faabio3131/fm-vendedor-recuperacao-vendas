# AtendeVendeIA — Entrega de reconciliação e recuperação (2026-11-08)

## Alterações internas

- Mensagens que a Evolution pode ter enviado, mas cujo HTTP/ID ficou incerto, passam a `uncertain`, e não a `failed` ("não enviada").
- Não há reenvio automático após estado incerto: evita mensagens duplicadas.
- Um webhook autenticado `messages.upsert` com `fromMe=true` pode reconciliar uma mensagem `sending/uncertain` para `sent` **somente** quando tenant, instância autenticada, contato, texto exato, ID do provedor, janela de 30 minutos e unicidade coincidirem. Webhooks ambíguos não reconciliam.
- O resultado confirmado não pode ser sobrescrito posteriormente por exceção da chamada HTTP.
- Tratamento de ACK da Evolution com `key.id` dentro de `data`.
- O piloto comercial tem opção governada `FM_PILOT_RECOVERY_ENABLED=true`. Sem ela, continua bloqueado por padrão. Ao ativar, o worker só coleta o **primeiro** passo de `conversation_cold` do telefone/tenant exclusivamente autorizado, com entrada recebida há menos de 24h. O envio recusa links, pagamentos, outros gatilhos e outros contatos.
- Nenhuma integração de Asaas ou Cakto foi modificada.

## Ativação da recuperação do piloto

1. Aplicar migração 0020 e publicar backend/worker/frontend da **mesma revisão**.
2. Conferir worker ativo e heartbeat de recuperação, Evolution conectada e webhook autenticado configurado para `MESSAGES_UPSERT`/`MESSAGES_UPDATE`.
3. Habilitar `FM_PILOT_RECOVERY_ENABLED=true` **somente** na instalação já configurada com `FM_PILOT_DEMO_ENABLED`, `FM_PILOT_GREETING_ENABLED`, `FM_PILOT_GREETING_TENANT_ID`, `FM_PILOT_GREETING_PHONE`. Nenhum segredo deve constar em logs ou commits.
4. No cadastro do tenant piloto, configurar `recovery_enabled=true`, `cold_enabled=true`, `cold_after_hours=3` (ou intervalo legítimo inferior a 24h), `consent_declared_at` válido, e template `conversa_1` existente. Respeitar opt-out e horário de silêncio.
5. O vendedor deve registrar o último envio de resposta com estado comprovado `sent`/`delivered`/`read`; só então a detecção de conversa fria abre oportunidade. O worker agenda e envia o passo 1 após o atraso. Nenhuma venda é declarada recuperada antes da confirmação efetiva de pagamento.
6. Conferir caso `recovery_cases` e passo `recovery_steps` do mesmo tenant; teste com o contato piloto e um contato de controle não autorizado. Testar mensagem duplicada/ACK tardio.

## Limites da prova

Uma URL de checkout do Asaas enviada no chat não cria, por si só, um webhook de abandono nem comprova que o pagamento está pendente. Enquanto não houver integração transacional, o teste é de recuperação de **conversa fria**, não de abandono financeiro confirmado. Mensagens históricas sem ID/recibo ficam com estado incerto e não são automaticamente promovidas a enviadas. O rollout de produção e a configuração do tenant exigem verificação independente.

## Gates

Rodar Ruff, mypy, pytest com PostgreSQL de testes e E2E do worker. Sem CI verde e sem teste real da recuperação na instância conectada, não declarar comercialmente homologado.
