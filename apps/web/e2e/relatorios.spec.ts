import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Relatórios: resumo, agrupamento, planilha e sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Relatórios" }).click();
  await expect(page.getByRole("heading", { name: "Relatórios" })).toBeVisible();
  await expect(page.getByText("Valor recuperado")).toBeVisible();
  await expect(page.getByText("Compraram sem mensagem")).toBeVisible();
  await expect(page.getByText(/sem projeção/)).toBeVisible();

  await page.getByLabel("Agrupar").selectOption("product");
  await expect(page.getByLabel("Agrupar")).toHaveValue("product");
  await expect(page.getByRole("link", { name: "Baixar planilha (CSV)" })).toHaveAttribute(
    "href",
    /recovery\.csv\?from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}&group_by=product/,
  );

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
