# FM Tecnologia — GitHub Actions → Contabo (piloto interno)

**CURRENT em 22/10/2026:** este documento configura um caminho de publicação
candidato. Criar o workflow no GitHub **não** é configurar credenciais reais,
certificar deploy no servidor ou autorizar migration. Não executar no servidor
antes de aprovação específica do Diretor.

## Preparação única (fora do código)

1. Em **Settings → Environments**, criar `contabo-pilot`. Habilitar
   **Required reviewers / Fábio**, quando disponível no plano GitHub, e
   restringir deployments à branch `main`. Se a proteção não estiver
   disponível após privatização, manter a confirmação explícita no despacho e
   uma aprovação operacional independente.
2. Configurar no ambiente GitHub, nunca em arquivo ou chat:
   - *Variables:* `CONTABO_DEPLOY_HOST` (IP/nome real já conferido),
     `CONTABO_DEPLOY_USER` (usuário dedicado e **não-root**).
   - *Secrets:* `CONTABO_DEPLOY_SSH_KEY` (chave privada dedicada nova),
     `CONTABO_SSH_KNOWN_HOSTS` (host key obtida por canal confiável e conferida),
     `CONTABO_DEPLOY_ENABLED` com o valor `FM-PILOTO-ATIVO` apenas
     depois do inventário e aprovação humana.
3. Criar credenciais SSH **exclusivas para o workflow** e configurar o usuário
   no servidor com permissão estritamente limitada às operações de deploy da
   aplicação. A chave `contabo_admin_ed25519` do computador **não é** a
   chave do workflow. Nunca fazer upload dela ao GitHub.
4. Garantir que esse usuário tenha acesso ao Docker e escrita em
   `/home/SaaS/atendevendeia/incoming`, `releases`, `backups`, `repo`
   e `SOURCE_COMMIT`. Essa concessão de privilégios reais precisa de
   autorização específica e revisão, pois controle de Docker equivale
   praticamente a root. **Não executar provisionamento automaticamente.**
5. Preservar fora do Git o `/home/SaaS/atendevendeia/.env`. Não trocar
   NPM, Evolution, certificados ou banco por outro conjunto de serviços.

## Operação

Abrir **Actions → Contabo pilot release (governed) → Run workflow**.

- **Inspect:** informar o SHA de 40 caracteres do HEAD da `main`,
  `FM-ATENDEVENDEIA-<SHA>`; apenas lê commit, containers e última migration.
- **Deploy:** requer, além da confirmação, marcar **approve_migrations** e
  **approve_deploy**. Esses dois campos equivalem a aprovações específicas
  naquele despacho; não selecioná-los antecipadamente.
- O workflow exige quatro checks verdes do mesmo SHA: `api`, `web`, `e2e`
  e `Cloudflare Pages`. Transfere um pacote `git archive`, que funciona
  mesmo se o repositório estiver **privado** e a instalação não tiver `.git`.
- No servidor o script verifica a instalação e o esquema canônicos, gera
  novo dump RLS-safe, restaura em banco isolado e valida RLS antes de
  construir e atualizar a aplicação. Para mudanças desconhecidas de schema,
  nega a publicação. Não executa bootstrap, `compose down` ou
  restauração automática do banco de produção.
- Imagens de rollback e árvore anterior são preservadas. Se os checks
  pós-publicação falharem, é necessária atuação humana governada;
  **migrations não possuem rollback automático seguro**.
- Os backups do script são **locais**; exigir também evidência do
  procedimento offsite cifrado e da recuperação operacional antes de
  considerar prontidão de produção comercial.

## Permanecer público e depois privatizar

**Não colocar o repositório privado agora.** Concluir primeiro piloto com a
própria FM: login, tenant, WhatsApp real, IA real, links oficiais, quota trial,
worker, restore e monitoramento. Antes de alterar visibilidade, comprovar que:
`actions/checkout` no privado, as credenciais/ambiente do deploy, integração
GitHub–Cloudflare Pages e as permissões de colaboradores continuam válidas.
Somente então pedir aprovação específica e mudar **Settings → General →
Danger Zone → Change repository visibility → Private**, verificando CI
e publicação depois da troca. O SaaS no domínio público pode permanecer
acessível mesmo com código privado.

**Status deste bloco:** construção e certificação de workflow em PR; ambiente,
credenciais, acesso de deploy e ativação aguardam operação autorizada.
