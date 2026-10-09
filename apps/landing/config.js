/* Configuração da landing do AtendeVendeIA. Edite SÓ este arquivo para liberar a página.
 *
 * liberado: false  -> todos os botões de entrada ficam como "Acesso em breve" (padrão, e o que vale se este arquivo falhar).
 * liberado: true   -> os botões levam ao painel (campo `painel`). Só ligar depois da produção paga estar no ar.
 *
 * Os campos de texto vazios ("") ficam escondidos na página; nada de "preencher depois" aparece para o visitante.
 */
window.LANDING = {
  liberado: false,
  painel: "", // endereço do painel de PRODUÇÃO, por exemplo "https://painel.exemplo.com.br/login"
  empresa: "FM Tecnologia LTDA",
  cnpj: "07.109.248/0001-57", // por exemplo "00.000.000/0001-00" (aparece no rodapé quando preenchido)
  sede: "Cajamar, SP", // por exemplo "Pouso Alegre, MG"
  contato: "privacidadeatendevendeia@gmail.com", // e-mail ou endereço de contato comercial (aparece no rodapé quando preenchido)
  linkTermos: "/termos/", // endereço dos Termos de uso aprovados
  linkPrivacidade: "/privacidade/", // endereço da Política de privacidade aprovada
  linkAcordo: "/acordo/", // endereço do Acordo de tratamento de dados (operador) aprovado
  siteEmpresa: "https://fmtecnologia.com.br",
};
