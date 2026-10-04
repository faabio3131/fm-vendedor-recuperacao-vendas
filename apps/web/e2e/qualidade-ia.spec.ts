import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Vendedor IA: testar conversa sem enviar nada e relatório de transferências", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Vendedor IA" }).click();
  await expect(page.getByRole("heading", { name: "Vendedor IA" })).toBeVisible();

  const box = page.getByRole("region", { name: "Testar conversa" });
  await expect(box.getByText(/nada é enviado a\s+ninguém/)).toBeVisible();
  await box.getByLabel("Mensagem do cliente (teste)").fill("Quero falar com um atendente");
  await box.getByRole("button", { name: "Testar" }).click();
  await expect(box.getByRole("status")).toContainText("Passaria para uma pessoa: Cliente pediu uma pessoa");

  await box.getByLabel("Mensagem do cliente (teste)").fill("SAIR");
  await box.getByRole("button", { name: "Testar" }).click();
  await expect(box.getByRole("status")).toContainText("Não responderia");

  await box.getByRole("button", { name: "Limpar conversa" }).click();
  await expect(box.getByRole("status")).toHaveCount(0);

  await expect(page.getByRole("region", { name: "Por que passou para uma pessoa" })).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
