import { expect, test } from "@playwright/test";

test("Entrega por CEP no pedido bloqueia regiões sem cobertura e valor do cliente", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill("demo@example.test");
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();

  const offer = "3ec79a19-1214-4bf7-892b-b8a6251fb83c";
  const zone = {
    id: "e99c8e12-40ad-44bd-ad8d-1a284b5637c9",
    name: "Centro", start: "01000000", end: "01099999", fee_cents: 950, active: true,
  };
  const account = { provider: "asaas", configured: true, verified: true, mode: "sandbox",
    delivery_fee_cents: 0, webhook_url: "https://example.test/webhooks" };
  let posted: Record<string, unknown> | null = null;
  await page.route("**/v1/merchant/account", (route) => route.fulfill({ json: account }));
  await page.route("**/v1/merchant/delivery/zones", (route) => route.fulfill({ json: [zone] }));
  await page.route("**/v1/seller/offers", (route) => route.fulfill({
    json: [{ id: offer, name: "Pizza Calabresa Grande", price_cents: 5990,
      category: "Pizzas", brand: "", active: true, description: "",
      payment_url: "https://checkout.example.test" }],
  }));
  await page.route("**/v1/merchant/orders", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: [] });
    posted = route.request().postDataJSON() as Record<string, unknown>;
    return route.fulfill({ json: { id: "test-order", status: "pending" } });
  });
  await page.goto("/orders");
  await expect(page.getByRole("heading", { name: "Áreas de entrega e frete" })).toBeVisible();
  await page.getByLabel("Nome do comprador").fill("Cliente Sintético");
  await page.getByLabel("Quantidade").fill("2");
  await page.getByLabel("Entrega ou retirada").selectOption("delivery");
  await page.getByLabel("CEP", { exact: true }).fill("99999999");
  await expect(page.getByText("CEP não coberto por uma única região de entrega.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Criar pedido" })).toBeDisabled();
  await page.getByLabel("CEP", { exact: true }).fill("01001000");
  await page.getByLabel("Rua ou avenida").fill("Praça da Sé");
  await page.getByLabel("Número").fill("100");
  await page.getByLabel("Bairro").fill("Sé");
  await page.getByLabel("Cidade").fill("São Paulo");
  await page.getByLabel("UF").fill("SP");
  await expect(page.getByText("Frete para este CEP: R$ 9,50")).toBeVisible();
  await page.getByRole("button", { name: "Criar pedido" }).click();
  await expect.poll(() => posted).not.toBeNull();
  const payload = posted as unknown as { method: string; address: Record<string,string>; total_cents?: number };
  expect(payload.method).toBe("delivery");
  expect(payload.address.postal_code).toBe("01001000");
  expect(payload.address.street_number).toBe("100");
  expect(payload.total_cents).toBeUndefined();
});
