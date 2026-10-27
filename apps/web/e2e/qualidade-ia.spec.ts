import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Vendedor IA: testar conversa sem enviar nada e relatório de transferências", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Vendedor IA" }).click();
  await expect(page.getByRole("heading", { name: "Vendedor IA" })).toBeVisible();

  const box = page.getByRole("region", { name: "Testar conversa" });
  await expect(box.getByText(/nada é enviado a\s+ninguém/)).toBeVisible();
  await box.getByLabel("Mensagem do cliente (teste)").fill("Quero falar com um atendente");
  await box.getByRole("button", { name: "Testar" }).click();
  await expect(box.getByRole("status")).toContainText("Passaria para uma pessoa: Cliente pediu uma pessoa");

  await box.getByLabel("Mensagem do cliente (teste)").fill("SAIR");
  await box.getByRole("button", { name: "Testar" }).click();
  await expect(box.getByRole("status")).toContainText("Não responderia");

  await box.getByRole("button", { name: "Limpar conversa" }).click();
  await expect(box.getByRole("status")).toHaveCount(0);

  await expect(page.getByRole("region", { name: "Por que passou para uma pessoa" })).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});


test("Conversas: mostra a data e o horário local de cada mensagem", async ({ page }) => {
  const timestamp = "2026-10-24T17:38:00Z";
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  // O login é assíncrono: sem esperar a sessão existir, o goto("/inbox") abaixo disputa com ela e,
  // quando perde, cai de volta no login (o título "Conversas" nunca aparece).
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();

  // UI contract fixture only. Delivery and audio are certified separately
  // using actual WhatsApp/Evolution on the controlled pilot.
  // Match the complete endpoint family, including same-origin Next rewrites
  // and optional query strings. No real messages are sent by this UI test.
  await page.route(/\/v1\/inbox(?:\/|\?|$)/, (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/v1/inbox")) {
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "conv-time-test",
            name: "Cliente de teste",
            phone: "+55 •••• 1234",
            channel: "whatsapp",
            status: "bot",
            handoff_reason: null,
            preview: "Mensagem de teste",
            last_message_at: timestamp,
            window_open: true,
          },
        ]),
      });
    }
    if (pathname.endsWith("/v1/inbox/conv-time-test")) {
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          name: "Cliente de teste",
          phone: "+55 •••• 1234",
          channel: "whatsapp",
          status: "bot",
          window_open: true,
          messages: [
            {
              id: "message-time-test",
              direction: "in",
              author: "customer",
              body: "Mensagem de teste",
              status: "received",
              error: null,
              created_at: timestamp,
            },
          ],
        }),
      });
    }
    return route.continue();
  });
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { name: "Conversas" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Cliente de teste/ })).toBeVisible();
  await page.getByRole("button", { name: /Cliente de teste/ }).click();
  const expected = await page.evaluate(
    (value) => new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" })
      .format(new Date(value)),
    timestamp,
  );
  const times = page.locator("time[datetime]");
  await expect(times).toHaveCount(2);
  await expect(times.nth(0)).toHaveText(expected);
  await expect(times.nth(1)).toHaveText(expected);
  await expect(times.nth(1)).toHaveAttribute("datetime", timestamp);
});
