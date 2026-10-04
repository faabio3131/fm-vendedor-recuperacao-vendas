import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Primeiros passos: lista do estado real, ações e sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  await page
    .getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Primeiros passos" })
    .click();
  await expect(page.getByRole("heading", { name: "Primeiros passos" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Progresso dos primeiros passos" })).toBeVisible();
  const list = page.getByRole("list", { name: "Lista de passos" });
  await expect(list.getByRole("listitem")).toHaveCount(8);
  await expect(list.getByText("WhatsApp conectado e testado")).toBeVisible();
  await expect(list.getByText("Ao menos uma oferta ativa")).toBeVisible();
  // item pendente traz o que fazer e um botão que leva à tela certa
  const pending = list.getByRole("listitem").filter({ hasText: "Falta" }).first();
  await expect(pending.getByRole("link")).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
