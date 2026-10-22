import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("painel mostra Evolution mas não libera QR sem backend habilitado", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Conexões" }).click();

  const evolution = page.locator(".card", { hasText: "WhatsApp por QR Code (Evolution)" }).first();
  await expect(evolution).toBeVisible();
  await expect(evolution.getByText("Integração ainda não liberada pela FM")).toBeVisible();
  await expect(evolution.getByRole("button", { name: "Conectar WhatsApp" })).toBeDisabled();
  await expect(evolution.getByAltText("QR Code temporário para conectar WhatsApp")).toHaveCount(0);
  await expect(page.locator(".card", { hasText: "WhatsApp oficial" }).first()).toBeVisible();
});
