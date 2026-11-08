import { expect, test } from "@playwright/test";

test("Acompanhamentos de longo prazo exigem revisão, não enviam WhatsApp", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill("demo@example.test");
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();

  let pending = true;
  let marked = 0;
  let outbound = 0;
  await page.route("**/v1/recovery/followups?*", (route) =>
    route.fulfill({
      json: pending ? [{
        id: "757699f9-9ef5-4204-9586-f3598bc17bdf",
        case_id: "757699f9-9ef5-4204-9586-f3598bc17bec",
        reason: "evolution_outside_window",
        status: "pending",
        case_status: "exhausted",
        trigger: "abandoned_cart",
        product: "Pizza calabresa",
        amount_cents: 4990,
        template_key: "carrinho_3",
        contact: { name: "Cliente sintético", phone: "+55 •• •••••-1234", email: "" },
        created_at: "2026-11-08T14:00:00Z",
        closed_at: null,
      }] : [],
    }),
  );
  await page.route("**/v1/recovery/followups/*/resolve", async (route) => {
    marked++;
    const body = route.request().postDataJSON() as { action?: string };
    expect(body.action).toBe("reviewed");
    pending = false;
    return route.fulfill({ json: { id: "757699f9-9ef5-4204-9586-f3598bc17bdf", status: "reviewed" } });
  });
  await page.route("**/v1/inbox/*/reply", (route) => {
    outbound++;
    return route.fulfill({ status: 500, json: {} });
  });
  await page.goto("/recovery");
  const section = page.getByRole("region", { name: "Acompanhamentos de longo prazo" });
  await expect(section.getByText("Pizza calabresa")).toBeVisible();
  await expect(section.getByText(/Fora da janela de contato automático/)).toBeVisible();
  await expect(section.getByText(/Nenhuma tarefa desta lista envia WhatsApp automaticamente/)).toBeVisible();
  await section.getByRole("button", { name: "Marcar revisado" }).click();
  await expect(section.getByText("Nenhum acompanhamento manual pendente.")).toBeVisible();
  expect(marked).toBe(1);
  expect(outbound).toBe(0);
});
