# Fixtures de eventos reais (Cakto e Hotmart)

Aqui entram os eventos que a plataforma **realmente** mandou, já **anonimizados**. Cada arquivo `.json` em
`cakto/` ou `hotmart/` é conferido por `tests/test_real_event_fixtures.py` contra o normalizador.

## Como trazer um evento real para cá

1. No ambiente com a captura ligada (`FM_CAPTURE_EVENTS=true`), faça a compra ou o evento de teste na plataforma.
2. `python -m fm_seller.cli capture list` para achar o evento e `capture show <id>` para ver a conferência.
3. `python -m fm_seller.cli capture export <id> --out apps/api/tests/fixtures/real_events/cakto/purchase_approved.json`
   (a exportação troca nome, e-mail, telefone e documento por valores de exemplo e já vem sem segredo).
4. Abra o arquivo, confira que **nada pessoal ficou** (ids de pedido e de produto podem ficar), acrescente o
   bloco `"expect"` (ver os exemplos) e faça a PR.

Formato: `{"provider", "event", "origem", "payload", "expect": {"kind", "has_email", "has_phone", "amount_cents",
"known_event"}}`. `origem` é `"sintetico"` (exemplo escrito por nós, **não** é evento real) ou
`"captura_anonimizada"` (evento real). Só a segunda vale como prova de formato.

Nunca coloque aqui: segredo, `hottok`, token, assinatura com a chave, e-mail, telefone ou documento reais.
