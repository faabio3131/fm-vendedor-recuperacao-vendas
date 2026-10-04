import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";
const TOKEN = "EAAGsegredo-social-e2e-1234567890";
const APP_SECRET = "app-secret-social-e2e-0987654321";

const CHANNELS = [
  {
    card: "Facebook Messenger",
    guide: "Como conectar o Messenger (passo a passo)",
    fields: [
      ["ID da página", "104500000001"],
      ["Token de acesso da página", TOKEN],
      ["Segredo do app Meta (opcional)", APP_SECRET],
    ],
    webhook: /\/v1\/webhooks\/messenger\//,
    docs: /messenger-platform/,
  },
  {
    card: "Instagram (mensagens diretas)",
    guide: "Como conectar o Instagram (passo a passo)",
    fields: [
      ["ID da conta do Instagram", "178900000002"],
      ["Token de acesso", TOKEN],
      ["Segredo do app Meta (opcional)", APP_SECRET],
    ],
    webhook: /\/v1\/webhooks\/instagram_dm\//,
    docs: /instagram-platform/,
  },
] as const;

test("Messenger e Instagram: guia, conexão e ausência de segredo na tela", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Conexões" }).click();
  await expect(page.getByRole("heading", { name: "Conexões" })).toBeVisible();

  for (const ch of CHANNELS) {
    const card = () => page.locator(".card", { hasText: ch.card }).first();
    await card().getByRole("button", { name: /Configurar|Gerenciar/ }).click();
    await card().getByText(ch.guide).click();
    await expect(card().getByRole("link", { name: /documentação da Meta/ })).toHaveAttribute("href", ch.docs);
    for (const [label, value] of ch.fields) {
      await card().getByLabel(label, { exact: true }).fill(value);
    }
    await card().getByRole("button", { name: "Salvar" }).click();
    await expect(card().getByRole("status")).toContainText("Salvo.");
    await expect(card().getByText(ch.webhook)).toBeVisible();
    await card().getByRole("button", { name: "Testar conexão" }).click();
    await expect(card().locator(".badge.connected")).toBeVisible();
  }

  // os segredos digitados não podem voltar para a página
  await page.reload();
  await expect(page.getByText(TOKEN)).toHaveCount(0);
  await expect(page.getByText(APP_SECRET)).toHaveCount(0);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("caixa de conversas continua sem rolagem horizontal com o selo do canal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Conversas" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
