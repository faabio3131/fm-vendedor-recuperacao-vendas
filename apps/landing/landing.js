/* Aplica a configuração da landing. Sem este script, ou sem config.js, a página fica bloqueada ("Acesso em breve"). */
(function () {
  var c = window.LANDING || {};
  var aberto = c.liberado === true && typeof c.painel === "string" && /^https:\/\//.test(c.painel);

  document.querySelectorAll("[data-cta]").forEach(function (el) {
    if (aberto) {
      el.setAttribute("href", c.painel);
      el.removeAttribute("aria-disabled");
      el.classList.remove("disabled");
      el.textContent = el.getAttribute("data-open-label") || "Entrar no painel";
    }
  });
  document.querySelectorAll("[data-soon]").forEach(function (el) {
    el.hidden = aberto;
  });

  function mostrar(id, valor, prefixo) {
    var el = document.getElementById(id);
    if (!el) return;
    if (valor) {
      el.textContent = (prefixo || "") + valor;
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  }
  mostrar("f-cnpj", c.cnpj, "CNPJ ");
  mostrar("f-sede", c.sede);
  var contato = document.getElementById("f-contato");
  if (contato) {
    if (c.contato) {
      contato.textContent = c.contato;
      if (c.contato.indexOf("@") > 0) contato.setAttribute("href", "mailto:" + c.contato);
      contato.hidden = false;
    } else contato.hidden = true;
  }
  [["f-termos", c.linkTermos], ["f-privacidade", c.linkPrivacidade]].forEach(function (p) {
    var el = document.getElementById(p[0]);
    if (!el) return;
    if (p[1] && /^https?:\/\//.test(p[1])) {
      el.setAttribute("href", p[1]);
      el.hidden = false;
    } else el.hidden = true;
  });
  var site = document.getElementById("f-site");
  if (site && c.siteEmpresa) site.setAttribute("href", c.siteEmpresa);
  var empresa = document.getElementById("f-empresa");
  if (empresa && c.empresa) empresa.textContent = c.empresa;
})();
