import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";
const SECRET = "EAAGsegredo-e2e-1234567890";

test("login de desenvolvimento, conexão do WhatsApp e ausência de segredo na tela", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Conexões" }).click();
  await expect(page.getByRole("heading", { name: "Conexões" })).toBeVisible();

  const card = () => page.locator(".card", { hasText: "WhatsApp oficial" }).first();
  await card().getByRole("button", { name: /Configurar|Gerenciar/ }).click();
  // guia de conexão: aberto sob demanda, sem rolagem horizontal
  await card().getByText("Como conectar o WhatsApp (passo a passo)").click();
  await expect(card().getByText("ID do número de telefone", { exact: false }).first()).toBeVisible();
  await expect(card().getByRole("link", { name: /primeiros passos/ })).toHaveAttribute("href", /developers\.facebook\.com/);
  await card().getByLabel("ID do número de telefone").fill("1055550001");
  await card().getByLabel("ID da conta do WhatsApp Business").fill("2077770002");
  await card().getByLabel("Token de acesso").fill(SECRET);
  await card().getByRole("button", { name: "Salvar" }).click();
  await expect(card().getByRole("status")).toContainText("Salvo.");
  await expect(card().getByText(/\/v1\/webhooks\/whatsapp_cloud\//)).toBeVisible();

  await card().getByRole("button", { name: "Testar conexão" }).click();
  await expect(card().locator(".badge.connected")).toBeVisible();

  // o segredo digitado não pode voltar para a página
  await page.reload();
  await expect(page.getByText(SECRET)).toHaveCount(0);

  // planos acima do contratado aparecem bloqueados
  await expect(page.locator(".card", { hasText: "Google Ads" }).getByText("Plano 4").first()).toBeVisible();

  // sem rolagem horizontal em nenhum tamanho de tela
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);

  await page.screenshot({ path: `test-results/connections-${test.info().project.name}.png`, fullPage: true });

  // limpeza para o teste poder repetir
  await card().getByRole("button", { name: "Gerenciar" }).click();
  page.once("dialog", (d) => void d.accept());
  await card().getByRole("button", { name: "Remover" }).click();
  await expect(card().getByText("Não configurada")).toBeVisible();
});

test("recuperação: ajustes exigem consentimento, template e sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Recuperação" }).click();
  await expect(page.getByRole("heading", { name: "Recuperação de vendas" })).toBeVisible();

  // começa de um estado conhecido, mesmo se uma execução anterior parou no meio
  const enable = page.getByRole("checkbox", { name: "Recuperação de vendas ligada" });
  const consent = page.getByRole("checkbox", { name: /Declaro que meus contatos autorizaram/ });
  await expect(consent).toBeVisible();
  if (await consent.isChecked()) {
    await consent.uncheck();
    await expect(page.getByText("Ajustes salvos.")).toBeVisible();
  }

  // a recuperação só liga depois do consentimento declarado
  await expect(enable).toBeDisabled();
  await consent.check();
  await expect(page.getByText("Ajustes salvos.")).toBeVisible();
  await expect(enable).toBeEnabled();

  await expect(page.getByLabel(/Contatos novos por dia/)).toHaveValue("200");

  // template: salvar o texto não o aprova sozinho
  const tpl = page.locator(".card", { hasText: "carrinho_1" }).first();
  await tpl.getByLabel("Texto").fill(`Oi, {nome}! Teste ${Date.now()} {link}`);
  await tpl.getByRole("button", { name: "Salvar texto" }).click();
  await expect(tpl.getByText("Rascunho")).toBeVisible(); // texto novo sempre volta a rascunho
  // sem WhatsApp conectado e testado, o envio para a Meta é recusado com explicação
  await tpl.getByRole("button", { name: "Enviar para aprovação na Meta" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Conecte e teste o WhatsApp" })).toBeVisible();
  await expect(tpl.getByText("Rascunho")).toBeVisible();
  await tpl.getByRole("button", { name: "Marcar: Aprovado" }).click();
  await expect(tpl.getByText("Aprovado").first()).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: `test-results/recovery-${test.info().project.name}.png`, fullPage: true });

  // limpeza para o teste poder repetir
  await consent.uncheck();
  await expect(enable).toBeDisabled();
});

test("vendedor IA: oferta com link https, ajustes e caixa de conversas sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Vendedor IA" }).click();
  await expect(page.getByRole("heading", { name: "Vendedor IA" })).toBeVisible();
  await expect(page.getByLabel("Uso da IA no mês")).toContainText("Respostas da IA neste mês");

  const name = `Curso E2E ${Date.now()}`;
  await page.getByLabel("Nome", { exact: true }).fill(name);
  await page.getByLabel("Preço (R$)").fill("197,00");
  await page.getByLabel("Link de pagamento (https)").fill("http://inseguro.example");
  await page.getByRole("button", { name: "Cadastrar oferta" }).click();
  await expect(page.getByRole("status")).toContainText("https://"); // link inseguro é recusado

  await page.getByLabel("Link de pagamento (https)").fill("https://pay.example.test/e2e");
  await page.getByRole("button", { name: "Cadastrar oferta" }).click();
  const card = page.locator(".card", { hasText: name });
  await expect(card.getByText("R$ 197,00")).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: `test-results/seller-${test.info().project.name}.png`, fullPage: true });

  page.once("dialog", (d) => void d.accept());
  await card.getByRole("button", { name: "Remover" }).click();
  await expect(page.getByText(name)).toHaveCount(0);

  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Conversas" }).click();
  await expect(page.getByRole("heading", { name: "Conversas" })).toBeVisible();
  const overflow2 = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow2).toBe(false);
});

test("oportunidades: registro exige autorização, aparece na lista e pode ser marcado como perdido", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Recuperação" }).click();
  await expect(page.getByRole("heading", { name: "Recuperação de vendas" })).toBeVisible();

  const consent = page.getByRole("checkbox", { name: /Declaro que meus contatos autorizaram/ });
  const enable = page.getByRole("checkbox", { name: "Recuperação de vendas ligada" });
  await expect(consent).toBeVisible();
  if (!(await consent.isChecked())) {
    await consent.check();
    await expect(page.getByText("Ajustes salvos.")).toBeVisible();
  }
  if (!(await enable.isChecked())) {
    await enable.check();
    await expect(page.getByText("Ajustes salvos.")).toBeVisible();
  }

  const form = page.locator("#oportunidades");
  const product = `Sofá E2E ${Date.now()}`;
  await form.getByLabel("Nome", { exact: true }).fill("Cliente E2E");
  await form.getByLabel("WhatsApp (com DDD)").fill("(11) 98888-7777");
  await form.getByLabel("Produto ou serviço").fill(product);
  await form.getByLabel("Valor (R$)").fill("1.500,00");
  await form.getByRole("button", { name: "Registrar" }).click();
  await expect(form.getByRole("status")).toContainText("Confirme que o cliente autorizou");

  await form.getByRole("checkbox", { name: "Este cliente autorizou receber mensagens por WhatsApp." }).check();
  await form.getByRole("button", { name: "Registrar" }).click();
  await expect(form.getByRole("status")).toContainText("Oportunidade registrada");

  const row = page.locator("tr", { hasText: product });
  await expect(row.getByText("Registro manual")).toBeVisible();
  await expect(row.getByText("R$ 1.500,00")).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await page.screenshot({ path: `test-results/opportunities-${test.info().project.name}.png`, fullPage: true });

  await row.getByRole("button", { name: "Perdi" }).click();
  await expect(page.locator("tr", { hasText: product }).getByText("Interrompida")).toBeVisible();

  // limpeza para o teste poder repetir
  await consent.uncheck();
  await expect(enable).toBeDisabled();
});
