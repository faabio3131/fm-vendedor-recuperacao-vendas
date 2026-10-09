"""Gera apps/landing/termos/index.html e apps/landing/privacidade/index.html a partir de docs/juridico/.

Uso (na raiz do repositório): python3 scripts/landing/gerar_paginas_juridicas.py
Os textos aprovados ficam em docs/juridico/*.md; este script só troca o formato (Markdown simples para HTML)
e tira as anotações internas de rascunho. Rodar de novo sempre que o texto mudar e commitar o resultado.
"""
import html
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DATA = "08/10/2026"  # usado só para a data de publicação, se houver no texto
PAGINAS = [
    ("TERMOS_DE_USO.md", "termos", "Termos de Uso", "09/10/2026"),
    ("POLITICA_DE_PRIVACIDADE.md", "privacidade", "Política de Privacidade", "08/10/2026"),
    ("ACORDO_OPERADOR_DADOS.md", "acordo", "Acordo de Tratamento de Dados", "09/10/2026"),
]


def limpar(md: str) -> str:
    linhas = []
    for l in md.splitlines():
        if l.startswith("# "):
            continue
        if l.startswith(">"):  # avisos de rascunho
            continue
        linhas.append(l)
    t = "\n".join(linhas)
    t = t.replace("Base legal (a confirmar com advogado)", "Base legal")
    t = re.sub(r"\s*\*\*\[Provedor a definir:[^\]]*\]\*\*", "", t)
    t = re.sub(r"\s*\*\*\[advogado:[^\]]*\]\*\*", "", t)
    t = t.replace(" **[confirmar]**", "")
    t = t.replace(" (atualizar quando houver)", "")
    t = t.replace(" (decisão do Diretor, 05/10/2026)", "")
    t = t.replace("**[data de publicação]**", f"**{DATA}**")
    t = re.sub(r" Detalhes técnicos em `docs/SEGURANCA.md`\.", "", t)
    return t.strip() + "\n"


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"([\w.+-]+@[\w-]+\.[\w.]+)", r'<a href="mailto:\1">\1</a>', s)
    return s


def converter(md: str) -> str:
    out, par, lista, tabela = [], [], [], []

    def fecha():
        nonlocal par, lista, tabela
        if par:
            out.append("<p>" + " ".join(inline(p) for p in par) + "</p>")
            par = []
        if lista:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in lista) + "</ul>")
            lista = []
        if tabela:
            cab, *corpo = [r for r in tabela if not re.match(r"^\|[-| ]+\|$", r)]
            cel = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            h = "".join(f"<th>{inline(c)}</th>" for c in cel(cab))
            b = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cel(r)) + "</tr>" for r in corpo)
            out.append(f'<div class="tabela"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>')
            tabela = []

    for l in md.splitlines():
        if not l.strip():
            fecha()
        elif l.startswith("## "):
            fecha()
            out.append(f"<h2>{inline(l[3:])}</h2>")
        elif l.startswith("|"):
            if par or lista:
                fecha()
            tabela.append(l)
        elif l.startswith("- "):
            if par or tabela:
                fecha()
            lista.append(l[2:])
        else:
            if lista or tabela:
                fecha()
            par.append(l.strip())
    fecha()
    return "\n".join(out)


MODELO = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{titulo} | AtendeVendeIA</title>
<meta name="theme-color" content="#000610">
<link rel="icon" type="image/svg+xml" href="../assets/icon.svg">
<link rel="stylesheet" href="../styles.css">
</head>
<body>
<header class="top">
  <div class="wrap">
    <a class="brand" href="../" aria-label="AtendeVendeIA, início">
      <img src="../assets/logo-mark.svg" width="42" height="42" alt="">
      <span class="brand-name"><span class="brand-a">AtendeVende</span><span class="brand-ia">IA</span></span>
    </a>
    <nav class="links" aria-label="Documentos">
      <a href="../termos/">Termos de uso</a>
      <a href="../privacidade/">Política de privacidade</a>
      <a href="../acordo/">Acordo de dados</a>
    </nav>
    <a class="btn" href="../">Voltar</a>
  </div>
</header>
<main>
  <div class="wrap legal">
    <h1>{titulo}</h1>
    <p class="note">AtendeVendeIA, da FM Tecnologia LTDA. Última atualização: {data}.</p>
{corpo}
  </div>
</main>
<footer>
  <div class="wrap">
    <div class="row">
      <div class="by"><i></i><span>AtendeVendeIA é um produto da FM Tecnologia</span></div>
      <div class="row"><a href="../termos/">Termos de uso</a><a href="../privacidade/">Política de privacidade</a><a href="../acordo/">Acordo de dados</a></div>
    </div>
  </div>
</footer>
</body>
</html>
"""

for arq, pasta, titulo, data in PAGINAS:
    corpo = converter(limpar((RAIZ / "docs/juridico" / arq).read_text(encoding="utf-8")))
    destino = RAIZ / "apps/landing" / pasta
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "index.html").write_text(MODELO.format(titulo=titulo, data=data, corpo=corpo), encoding="utf-8")
    print("gerado", destino / "index.html")
