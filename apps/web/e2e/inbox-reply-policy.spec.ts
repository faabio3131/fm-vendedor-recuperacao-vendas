import { expect, test } from "@playwright/test";

const ID = "524cdfab-90da-4a58-aedc-17ec07075545";
const EARLIER = "2026-11-05T15:37:00Z";

async function openConversation(
  page: import("@playwright/test").Page,
  detail: { reply_allowed: boolean; window_open: boolean; evolution_manual_reply: boolean; ai_enabled: boolean },
) {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill("demo@example.test");
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();

  await page.route(/\/v1\/inbox(?:\/|\?|$)/, (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/v1/inbox")) {
      return route.fulfill({
        json: [{
          id: ID,
          channel: "whatsapp",
          name: "Cliente de teste",
          phone: "+55 •••• 1234",
          status: "human",
          handoff_reason: "ia_desligada",
          preview: "Solicitação de demonstração",
          last_message_at: EARLIER,
          ...detail,
        }],
      });
    }
    return route.fulfill({
      json: {
        channel: "whatsapp",
        name: "Cliente de teste",
        phone: "+55 •••• 1234",
        status: "human",
        ...detail,
        messages: [{
          id: "1",
          direction: "in",
          author: "customer",
          body: "Solicitação de demonstração",
          status: "received",
          error: null,
          created_at: EARLIER,
        }],
      },
    });
  });
  await page.goto("/inbox");
  await page.getByRole("button", { name: /Cliente de teste/ }).click();
  await expect(page.getByText("Solicitação de demonstração").last()).toBeVisible();
}

test("Evolution permite resposta humana tardia e avisa que IA está desligada", async ({ page }) => {
  await openConversation(page, {
    reply_allowed: true,
    window_open: false,
    evolution_manual_reply: true,
    ai_enabled: false,
  });
  const answer = page.getByLabel("Responder como equipe");
  await expect(answer).toBeEnabled();
  await expect(page.getByText(/WhatsApp Web \(Evolution\): resposta manual disponível/)).toBeVisible();
  const resume = page.getByRole("button", { name: "Devolver ao vendedor IA" });
  await expect(resume).toBeDisabled();
  await answer.fill("Boa tarde! Posso encaminhar uma demonstração com acesso limitado.");
  await expect(page.getByRole("button", { name: "Enviar", exact: true })).toBeEnabled();
});

test("Cloud API fora das 24h mantém resposta livre bloqueada", async ({ page }) => {
  await openConversation(page, {
    reply_allowed: false,
    window_open: false,
    evolution_manual_reply: false,
    ai_enabled: true,
  });
  await expect(page.getByLabel("Responder como equipe")).toBeDisabled();
  await expect(page.getByText(/API oficial exige template aprovado/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Enviar", exact: true })).toBeDisabled();
});
