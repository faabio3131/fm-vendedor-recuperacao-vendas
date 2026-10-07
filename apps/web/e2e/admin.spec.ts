import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Administração da plataforma: saúde, números, clientes e sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Operação" })).toBeVisible();
  await expect(page.getByText(/Aqui não aparece conversa, contato nem credencial/)).toBeVisible();
  await expect(page.getByRole("region", { name: "Saúde" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Números do sistema" })).toBeVisible();

  // sem chave de IA neste ambiente: a verificação avisa e não chama ninguém (nenhum custo)
  const ai = page.getByRole("region", { name: "Teste da IA" });
  await expect(ai.getByText(/Não grava nada, não envia a ninguém/)).toBeVisible();
  await ai.getByRole("button", { name: "Testar IA" }).click();
  await expect(ai.getByRole("status")).toContainText("Nenhuma chave de IA configurada");

  const clients = page.getByRole("region", { name: /^Clientes/ });
  await expect(clients.getByRole("row", { name: /Loja Demo/ })).toBeVisible();
  // o convite do cliente de demonstração já foi aceito: renovar mostra o motivo, não quebra nada
  await clients.getByRole("button", { name: "Renovar convite de Loja Demo" }).click();
  await expect(page.getByRole("status").filter({ hasText: "não tem convite pendente" })).toBeVisible();
  // o botão de trocar plano só liga quando o plano muda
  await expect(clients.getByRole("button", { name: "Trocar plano" }).first()).toBeDisabled();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
