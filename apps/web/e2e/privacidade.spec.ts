import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Privacidade: retenção, dados de uma pessoa, consentimento e sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Privacidade" }).click();
  await expect(page.getByRole("heading", { name: "Privacidade", exact: true })).toBeVisible();

  const retention = page.getByRole("region", { name: "Quanto tempo guardar as conversas" });
  await retention.getByLabel(/Dias/).fill("10");
  await retention.getByRole("button", { name: "Salvar prazo" }).click();
  await expect(retention.getByRole("status")).toContainText("entre 30 e 3650");
  await retention.getByLabel(/Dias/).fill("365");
  await retention.getByRole("button", { name: "Salvar prazo" }).click();
  await expect(retention.getByRole("status")).toContainText("Prazo de retenção salvo");

  const person = page.getByRole("region", { name: "Dados de uma pessoa" });
  const erase = person.getByRole("button", { name: "Apagar dados" });
  await expect(erase).toBeDisabled();
  await person.getByLabel("Telefone, e-mail ou ID do canal").fill("(11) 90000-0000");
  await expect(erase).toBeDisabled(); // apagar exige confirmar
  await person.getByRole("button", { name: "Baixar dados" }).click();
  await expect(person.getByRole("status")).toContainText("Nenhum contato encontrado");
  await person.getByRole("checkbox", { name: /Confirmo que quero apagar/ }).check();
  await expect(erase).toBeEnabled();
  await erase.click();
  await expect(person.getByRole("status")).toContainText("Nenhum contato encontrado");

  await expect(page.getByRole("region", { name: "Registro de consentimento" })).toBeVisible();
  await expect(page.getByText(/PENDENTE: texto jurídico/)).toBeVisible();

  // exclusão da conta: só o botão desabilitado até digitar; nunca é acionado aqui
  const del = page.getByRole("region", { name: "Excluir a conta" });
  await expect(del.getByRole("button", { name: "Pedir a exclusão da conta" })).toBeDisabled();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
