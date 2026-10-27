import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Ajuda e modelos: passo a passo com modelos prontos, sem estourar a tela no celular", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();

  await page.getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Ajuda e modelos" }).click();
  await expect(page.getByRole("heading", { name: "Ajuda e modelos prontos" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Conecte o WhatsApp/ })).toBeVisible();
  await expect(page.getByText("Aparelhos conectados").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: /Recuperação de vendas/ })).toBeVisible();
  await expect(page.getByText("Carrinho abandonado")).toBeVisible();
  expect(await page.getByRole("button", { name: "Copiar texto" }).count()).toBeGreaterThan(10);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  expect(overflow).toBe(false);
});
