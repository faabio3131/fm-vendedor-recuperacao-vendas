import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Vendedor IA: confirmação de salvamento junto ao botão, sem gravar duas vezes", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Vendedor IA" }).click();

  const persona = page.getByLabel("Tom de voz (opcional)");
  await expect(persona).toBeVisible();
  await expect(persona).toBeEnabled(); // Await authoritative settings before typing.
  const original = await persona.inputValue();
  const updated = "Atendimento claro e objetivo para certificação visual E2E.";
  const save = page.getByRole("button", { name: "Salvar ajustes" });
  await expect(save).toBeDisabled();

  await persona.fill(updated);
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText("Ajustes salvos com sucesso.")).toBeVisible();
  await expect(page.getByRole("button", { name: "✓ Salvo" })).toBeDisabled();

  await page.reload();
  await expect(persona).toHaveValue(updated);
  await expect(page.getByRole("button", { name: "Salvar ajustes" })).toBeDisabled();

  await persona.fill(original);
  await expect(page.getByRole("button", { name: "Salvar ajustes" })).toBeEnabled();
  await page.getByRole("button", { name: "Salvar ajustes" }).click();
  await expect(page.getByText("Ajustes salvos com sucesso.")).toBeVisible();
});
