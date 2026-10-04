import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Meu plano: estado da assinatura, uso da IA e sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Meu plano" }).click();
  await expect(page.getByRole("heading", { name: "Meu plano" })).toBeVisible();
  await expect(page.locator(".badge", { hasText: "Ativa" })).toBeVisible();
  await expect(page.getByText("Respostas da IA neste mês")).toBeVisible();
  await expect(page.getByText(/próxima cobrança ainda não é informada/)).toBeVisible();
  // plano ativo: nenhuma faixa de aviso
  await expect(page.getByText("Assinatura inativa")).toHaveCount(0);
  await expect(page.getByText("Seu pagamento está em atraso")).toHaveCount(0);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
