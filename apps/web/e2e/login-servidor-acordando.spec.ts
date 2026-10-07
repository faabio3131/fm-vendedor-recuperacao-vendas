import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

// Planos grátis dormem: o repasse devolve 502 até a API acordar. O login deve esperar e tentar de novo,
// em vez de mostrar "Algo deu errado." (bug visto no staging em 06/10/2026).
test("Login: servidor acordando (502) espera e entra sozinho quando a API responde", async ({ page }) => {
  await page.clock.install();
  let calls = 0;
  await page.route("**/v1/auth/google", async (route) => {
    calls += 1;
    if (calls <= 2) {
      await route.fulfill({ status: 502, contentType: "text/html", body: "<html><title>502</title></html>" });
    } else {
      await route.continue();
    }
  });

  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();

  await expect(page.locator(".alert[role=status]")).toContainText("O servidor está acordando");
  await expect(page.locator(".alert.bad")).toHaveCount(0); // nenhum erro enquanto ainda tenta

  // Avança o relógio até a terceira tentativa (a espera só é registrada depois de cada resposta).
  await expect
    .poll(async () => {
      await page.clock.fastForward(10_500);
      return calls;
    })
    .toBeGreaterThanOrEqual(3);
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();
  expect(calls).toBe(3);
});

test("Login: recusa de verdade (401) continua mostrando a mensagem e não fica tentando", async ({ page }) => {
  let calls = 0;
  await page.route("**/v1/auth/google", async (route) => {
    calls += 1;
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "unauthorized", message: "Não foi possível validar o login com o Google." } }),
    });
  });

  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();

  await expect(page.locator(".alert.bad")).toContainText("Não foi possível validar o login com o Google.");
  expect(calls).toBe(1);
});
