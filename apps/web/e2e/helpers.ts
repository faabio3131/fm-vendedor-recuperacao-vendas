import { expect, type Page } from "@playwright/test";

/** Deixa o WhatsApp do cliente de demonstração conectado e testado (simulado em dev). */
export async function connectWhatsapp(page: Page): Promise<void> {
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Conexões" }).click();
  await expect(page.getByRole("heading", { name: "Conexões" })).toBeVisible();
  const card = page.locator(".card", { hasText: "WhatsApp oficial" }).first();
  await card.getByRole("button", { name: /Configurar|Gerenciar/ }).click();
  await card.getByLabel("ID do número de telefone").fill("1055550001");
  await card.getByLabel("ID da conta do WhatsApp Business").fill("2077770002");
  await card.getByLabel("Token de acesso").fill("EAAGsegredo-e2e-1234567890");
  await card.getByRole("button", { name: "Salvar" }).click();
  await expect(card.getByRole("status")).toContainText("Salvo.");
  await card.getByRole("button", { name: "Testar conexão" }).click();
  await expect(card.locator(".badge.connected")).toBeVisible();
}
