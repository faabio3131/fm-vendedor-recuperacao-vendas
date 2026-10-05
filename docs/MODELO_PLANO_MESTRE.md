# Modelo de plano mestre

Copie este arquivo para `docs/plano/AAAA-MM-<nome>.md` (ou `docs/CRONOGRAMA_MESTRE.md`) e preencha. O plano é a **fonte única da
verdade** do trabalho: o executor (Claude, ChatGPT/Codex ou pessoa) lê o arquivo, não a memória de uma conversa. Plano grande
vira vários arquivos (um por bloco) com um índice. Regras de execução: `AGENTS.md`, seção "Execução de plano mestre".

> **Nunca** coloque no plano: chave, senha, token, dado de cliente, e-mail ou telefone reais, metas financeiras que não possam
> ser públicas. O repositório é público.

## Cabeçalho

- **Plano:** nome e objetivo em uma frase.
- **Autor / aprovador:** quem decide (Fábio) e a data.
- **Estado:** `RASCUNHO` · `APROVADO` · `EM EXECUÇÃO` · `CONCLUÍDO`. Só passa a `APROVADO` com o aval do Fábio, escrito aqui.
- **Fora de escopo:** o que NÃO será feito neste plano (evita o executor inventar trabalho).
- **Autorizações e limites:** o que o executor pode fazer sem perguntar (por exemplo, merge de PR com CI verde) e o que exige
  parar e perguntar (preço, produto, risco de dado ou segurança, gasto, conta real, outro repositório).

## Ordem de execução

Itens numerados, **em ordem**. Cada item só começa quando o anterior está concluído e verificado. Dependências explícitas.

## Modelo de item (repita para cada item)

```
### 1. Título curto do item

- [ ] **Estado:** pendente | em andamento | concluído (data e PR)
- **Objetivo:** uma ou duas frases.
- **Depende de:** número dos itens anteriores, ou "nada".
- **Entregar:** lista objetiva (arquivos, telas, rotas, migrations, documentos).
- **Não fazer:** limites deste item.
- **Critério de aceite:** como provar que ficou pronto E correto (comandos, testes, comportamento observável).
  Exemplo: "ruff, mypy e pytest passam; teste X cobre o caso Y; tela Z funciona em celular, tablet e desktop".
- **Verificação:** comandos exatos a rodar e o resultado esperado.
- **Riscos / não confirmado:** o que não foi provado (por exemplo, "só testado com simulador").
- **Decisões do Fábio pendentes:** o que o executor não pode decidir sozinho; o padrão provisório até decidir.
```

## Registro de execução (o executor preenche)

| Item | PR | Merge | CI do main | Verificado como | Pendências |
|---|---|---|---|---|---|
| 1 | | | | | |

## Relatório final (ao concluir o plano)

O que cada item entregou · links das PRs e commits de merge · estado do CI do `main` · o que foi verificado (e como) · o que
**não** está confirmado · pendências que dependem do Fábio · próximo passo recomendado.
