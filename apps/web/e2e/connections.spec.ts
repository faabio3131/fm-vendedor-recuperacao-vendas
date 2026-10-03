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
  await card().getByLabel("ID do número de telefone").fill("1055550001");
  await card().getByLabel("ID da conta do WhatsApp Business").fill("2077770002");
  await card().getByLabel("Token de acesso").fill(SECRET);
  await card().getByRole("button", { name: "Salvar" }).click();
  await expect(card().getByText("Salvo.")).toBeVisible();
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
