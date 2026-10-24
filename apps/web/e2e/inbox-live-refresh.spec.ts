import { expect, test } from "@playwright/test";

test("Conversas mostra respostas posteriores sem recarregar e preserva rascunho da equipe", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill("demo@example.test");
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  const id = "624cdfab-90da-4a58-aedc-17ec07075543";
  const at = "2026-10-24T21:00:00Z";
  let answerIsAvailable = false;
  await page.route("**/v1/inbox", (route) =>
    route.fulfill({
      json: [
        {
          id,
          name: "Contato piloto sintético",
          phone: "contato mascarado",
          channel: "whatsapp",
          status: "bot",
          handoff_reason: null,
          preview: answerIsAvailable ? "Resposta posterior" : "Mensagem inicial",
          last_message_at: at,
          window_open: true,
        },
      ],
    }),
  );
  await page.route(`**/v1/inbox/${id}`, (route) =>
    route.fulfill({
      json: {
        name: "Contato piloto sintético",
        phone: "contato mascarado",
        channel: "whatsapp",
        status: "bot",
        window_open: true,
        messages: [
          {
            id: "m1",
            direction: "in",
            author: "customer",
            body: "Mensagem inicial",
            created_at: at,
            status: "received",
            error: null,
          },
          ...(answerIsAvailable
            ? [
                {
                  id: "m2",
                  direction: "out",
                  author: "bot",
                  body: "Resposta posterior da IA",
                  created_at: "2026-10-24T21:00:02Z",
                  status: "sent",
                  error: null,
                },
              ]
            : []),
        ],
      },
    }),
  );

  await page.goto("/inbox");
  await page.getByRole("button", { name: /Contato piloto sintético/ }).click();
  await expect(page.getByLabel("Mensagens").getByText("Mensagem inicial")).toBeVisible();
  await page.getByLabel("Responder como equipe").fill("Rascunho que não pode sumir");

  // Simula mensagem posterior do Worker. A atualização ocorre sem ação do operador.
  answerIsAvailable = true;
  await expect(page.getByLabel("Mensagens").getByText("Resposta posterior da IA")).toBeVisible({
    timeout: 12000,
  });
  await expect(page.getByLabel("Responder como equipe")).toHaveValue(
    "Rascunho que não pode sumir",
  );
  await expect(page.getByLabel("Mensagens").getByText("Resposta posterior da IA").locator("..").locator("..")).toContainText("enviada");
});
