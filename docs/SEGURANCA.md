# Segurança: modelo de ameaças e o que foi feito

Escrito no Bloco 19 (04/10/2026). É um modelo **curto e honesto**: diz o que foi construído e testado contra simulador e o que
**não** foi provado. Nada aqui substitui um teste de invasão feito por quem é de fora (pendente, ver o fim).

## O que protegemos e de quem

| Ativo | Quem quer | Como |
|---|---|---|
| Conversas e dados dos contatos do cliente | outro cliente; quem rouba uma sessão; quem acha o banco | RLS forçada em toda tabela com `tenant_id`, papel `fm_app` sem bypass, sessão com cookie `HttpOnly`, retenção e exclusão (`docs/ARQUITETURA.md`) |
| Credenciais do cliente (token da Meta, segredos) | quem lê o banco ou um backup | AES-256-GCM com `tenant_id\|provider` como AAD; chave só em variável de ambiente |
| A conta e o painel | quem adivinha ou repete login, ou engana o navegador do dono | login só por Google com e-mail verificado, guarda de origem em toda escrita, `SameSite=Lax` |
| A disponibilidade | enxurrada de pedidos, corpo gigante, webhook falso repetido | limites de taxa, limite de corpo, trava por IP nos webhooks recusados |
| O vendedor IA (preço, link, regras) | cliente que tenta burlar pela conversa | a IA só propõe; preço e link vêm do cadastro (`docs/ARQUITETURA.md`, Bloco 17) |

## Limite de taxa (`security/ratelimit.py`, `api/guards.py`)

Janela deslizante na **memória do processo**, com número de chaves limitado (trocar de chave não enche a memória). Respostas `429`
com `Retry-After`, no formato de erro normal. Todos os valores são configuráveis (`FM_RATE_*`) e o padrão é folgado de propósito.

| Regra | Chave | Padrão |
|---|---|---|
| Login (`POST /v1/auth/google`) | IP | 60 por 5 min |
| Exportar, apagar, importar, planilhas (`/v1/privacy`, `.csv`, `/import`) | **conta** (sessão), não IP | 20 por 10 min |
| Qualquer rota `/v1` (exceto `/health` e `/ready`) | IP | 1200 por min |
| Webhooks | IP | 1200 por min |
| Webhook **recusado** (segredo, assinatura, conexão ou corpo errado: 400, 401, 403, 404) | IP | 30 por 10 min, depois trava o IP nos webhooks |

Limites **não confirmados** para o uso real:

- **IP real atrás do proxy.** O painel repassa `/v1` para a API pelo Next. Se o Render/Next não entregarem o IP do cliente em
  `X-Forwarded-For` como o código espera (`FM_TRUST_PROXY=true` usa a **última** entrada, a que o proxy acrescenta), todo o tráfego
  do painel conta como um IP só. Com `FM_TRUST_PROXY=false` (padrão) o cabeçalho é ignorado, então não dá para furar o limite
  escrevendo-o; o `preflight` avisa. O IP precisa ser conferido no ambiente real antes de ligar isso.
- **Mais de uma instância** da API: cada uma conta sozinha (limite efetivo = limite x instâncias). Aceitável no MVP (uma instância).
- O e2e do CI e o desenvolvimento local desligam o limite (`FM_RATE_LIMIT_ENABLED=false`); o `preflight` avisa em staging e é
  **crítico em produção** se estiver desligado.

## Tamanho de corpo (`BodyLimitMiddleware`)

`FM_MAX_BODY_BYTES` (1 MiB): recusa com `413` pelo `Content-Length` e também **no meio da leitura** quando o corpo vem em pedaços,
antes de ir inteiro para a memória. Vale para toda rota, inclusive webhooks. Os webhooks ainda têm os próprios tetos menores.
O que **não** há: limite de tempo de requisição no servidor (vale o padrão do uvicorn); as chamadas de saída (Meta, Gemini) têm
tempo limite configurado.

## Cabeçalhos de segurança

- **API** (sempre, inclusive em 401, 403, 413 e 429): `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Resource-Policy: same-site`; `Cache-Control: no-store` em tudo de `/v1`; **HSTS só em staging e produção**.
  Em staging e produção `/docs`, `/redoc` e `/openapi.json` ficam desligados.
- **Painel** (Next, `next.config.mjs`): `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`
  (sem câmera, microfone nem localização) e, no build de produção, CSP e HSTS. A CSP libera só o próprio painel, a API configurada
  e o Google (login). **Fraqueza conhecida:** mantém `'unsafe-inline'` em script e estilo porque o Next gera scripts embutidos;
  trocar por nonce é melhoria futura. Ela ainda bloqueia ser embutido em outra página, scripts de origem estranha, conexões para
  fora da lista e `<object>`.
- O `cli smoke` confere esses cabeçalhos na API e no painel (aviso se faltarem, por exemplo se um proxy os remover) e avisa se
  `/openapi.json` estiver exposto fora do local. O Playwright confere os cabeçalhos do painel e que a CSP não bloqueia as telas.

## Sessão e login

- Token aleatório de 32 bytes; só o hash fica no banco. Cookie `HttpOnly`, `SameSite=Lax`, `Secure` obrigatório em staging e
  produção (a configuração recusa subir sem isso). Duração: 14 dias, **sem expiração por inatividade** (decisão do Diretor).
- No máximo **10 sessões ativas por pessoa**: ao entrar, a mais antiga além disso é encerrada. `POST /v1/auth/logout-all` encerra
  todas ("sair de todos os aparelhos").
- Cliente suspenso perde o acesso na hora (a sessão só vale para cliente ativo); excluir a conta apaga usuários e sessões.
- **Sem enumeração:** conta desconhecida e conta que perdeu o acesso recebem a mesma resposta (403, mesma mensagem). O login é só
  por Google, então não há senha nem "esqueci a senha" para consultar quem existe.
- Toda escrita exige `Origin` igual ao do painel (proteção contra CSRF), exceto webhooks, que se autenticam por segredo.

## Administração da plataforma

`/v1/admin/*` é a única rota de pessoa que lê vários clientes (modo `system`). Cercas: papel `platform_admins` só por convite da linha de
comando, sessão e papel validados antes de qualquer consulta, colunas escolhidas à mão (nunca conversa, contato ou credencial),
ações auditadas no cliente afetado, escrita sujeita à guarda de origem e ao limite sensível. Ver `docs/adr/0003-administracao-da-plataforma.md`.
**Não provado:** revisão independente das consultas do administrador (um erro ali seria vazamento entre clientes).

## SSRF, injeção e log

- **SSRF:** nenhuma URL escrita pelo cliente é buscada. Links de pagamento são só guardados e enviados. Só cinco módulos falam com
  a rede (`ai/gemini.py`, `auth/google.py`, `channels/meta_api.py`, `ops/smoke.py` e `ops/loadtest.py`), cada um com endereço fixo, da
  plataforma ou passado pelo operador na linha de comando. Um
  teste trava isso: módulo novo que use HTTP falha até alguém revisar.
- **Planilha (CSV):** texto que uma planilha leria como fórmula (`=`, `+`, `-`, `@`) é escapado na exportação (Bloco 13).
- **SQL:** todo SQL usa parâmetros; as poucas consultas montadas à mão são texto fixo.
- **Log:** formato JSON (texto do usuário não forja linha), sem corpo de requisição, cookie, segredo nem dado pessoal (testado no
  Bloco 18).

## Dependências

`pip-audit` (Python) e `npm audit --omit=dev` (painel) rodam sem conta e sem custo: no fluxo `Auditoria de dependências`
(`.github/workflows/audit.yml`) toda segunda-feira, sob pedido e em PR que mexe em dependência. Fica **fora** do CI principal de
propósito (vulnerabilidade nova numa biblioteca não deve travar merge que não mexeu nela). No dia deste bloco: Python sem
vulnerabilidade conhecida; o painel tinha uma em `postcss` (puxada pelo Next, só no build) e foi resolvida com `overrides` no
`package.json`. O piso do `pyjwt` subiu para 2.14 (havia aviso na 2.13). **Não há arquivo de travamento do Python**: cada build pega
a versão mais nova permitida (as versões ficam fora de controle até existir um lock).

## O que NÃO foi testado

- **Teste de invasão real** por quem é de fora (nenhum foi feito; nem contratado, ver fora de escopo).
- O comportamento real atrás do proxy do Render (IP do cliente, cabeçalhos preservados, limite com mais de uma instância).
- Cabeçalhos e CSP em navegador real com o login do Google de verdade (o e2e usa o login simulado).
- Revisão do app Meta, escopo de permissões e rotação de segredos em produção.
- Backup e restauração com as novas tabelas em ambiente real (só testado local).

## Decisões abertas (do Diretor)

- Duração da sessão e expiração por inatividade (hoje 14 dias fixos).
- Trocar `'unsafe-inline'` da CSP por nonce (custa renderização dinâmica no painel).
- Se o `FM_TRUST_PROXY` deve ficar ligado no Render depois de conferir o IP real.
- Contratar teste de invasão antes de abrir para clientes.
