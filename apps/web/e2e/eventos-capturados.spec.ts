import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Eventos capturados: aviso de captura desligada e sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Conexões" }).click();
  await page.getByRole("link", { name: /Ver eventos capturados/ }).click();
  await expect(page.getByRole("heading", { name: "Eventos capturados" })).toBeVisible();
  // por padrão a captura vem desligada: a tela explica como ligar e não mostra nada
  await expect(page.getByRole("status").filter({ hasText: "A captura está desligada" })).toBeVisible();
  await expect(page.getByText("FM_CAPTURE_EVENTS=true")).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
