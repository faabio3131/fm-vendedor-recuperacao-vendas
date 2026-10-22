import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Primeiros passos: instruções de cadastro e conexão disponíveis ao cliente", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();

  await page.getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Primeiros passos" }).click();
  await expect(page.getByRole("heading", { name: "Primeiros passos" })).toBeVisible();

  const whatsapp = page.getByText("Como configurar whatsapp conectado e testado");
  await whatsapp.click();
  await expect(page.getByText(/QR gerado ou conexão pendente não é confirmação/)).toBeVisible();

  await page.getByText("Como configurar ao menos uma oferta ativa").click();
  await expect(page.getByText(/Não ative ofertas de exemplo ou links fictícios/)).toBeVisible();

  await page.getByText("Como configurar tom de voz do vendedor ia").click();
  await expect(page.getByText(/atendimento automático desligado até cadastrar/)).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  expect(overflow).toBe(false);
});
