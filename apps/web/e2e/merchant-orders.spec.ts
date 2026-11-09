import { expect, test } from "@playwright/test";

test("Pedidos: preço canônico, cobrança exclusiva e status financeiro", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill("demo@example.test");
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("navigation", { name: "Principal" })).toBeVisible();

  const id = "a90d15ab-2e7d-4dc9-9974-61d1e1eb9acc";
  const offer = "3ec79a19-1214-4bf7-892b-b8a6251fb83c";
  const mockAccount = {
    provider: "asaas", configured: true, verified: true,
    mode: "sandbox", delivery_fee_cents: 500,
    webhook_url: "https://api.example.test/v1/webhooks/merchant/asaas/demo",
  };
  let orders: Array<Record<string, unknown>> = [];
  let created = 0;
  await page.route("**/v1/merchant/account", (route) => route.fulfill({ json: mockAccount }));
  await page.route("**/v1/seller/offers", (route) => route.fulfill({
    json: [{
      id: offer, name: "Pizza Calabresa", category: "Pizzas",
      price_cents: 5990, description: "Sintética", payment_url: "https://checkout.example.test",
      active: true, photos: [], brand: "",
    }],
  }));
  await page.route("**/v1/merchant/orders", async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: orders });
    }
    const body = route.request().postDataJSON() as {
      items: Array<{ offer_id: string; quantity: number }>;
      idempotency_key: string;
    };
    expect(body.items).toEqual([{ offer_id: offer, quantity: 2 }]);
    expect(body.idempotency_key.length).toBeGreaterThan(12);
    expect("total_cents" in body).toBe(false);
    created++;
    const newOrder = {
      id, customer_name: "Cliente de teste", customer_phone: "",
      customer_email: "", items_cents: 11980, delivery_fee_cents: 500,
      total_cents: 11980, status: "pending", paid_at: null, settled_at: null,
      charge: null, created_at: "2026-11-08T14:00:00Z",
      items: [{ name: "Pizza Calabresa", quantity: 2,
        unit_price_cents: 5990, line_cents: 11980 }],
    };
    orders = [newOrder];
    return route.fulfill({ json: newOrder });
  });
  await page.route("**/v1/merchant/orders/*/charge", (route) => {
    const body = route.request().postDataJSON() as { customer_cpf_cnpj: string };
    expect(body.customer_cpf_cnpj).toBe("52998224725");
    const charged = {
      ...orders[0], status: "awaiting_payment",
      charge: { provider: "asaas", mode: "sandbox", status: "pending",
        invoice_url: "https://sandbox.asaas.com/i/synthetic" },
    };
    orders = [charged];
    return route.fulfill({ json: charged });
  });
  await page.goto("/orders");
  await expect(page.getByRole("heading", { name: "Pedidos e pagamentos" })).toBeVisible();
  await page.getByLabel("Nome do comprador").fill("Cliente de teste");
  await page.getByLabel("Quantidade").fill("2");
  await expect(page.getByText("Total estimado: R$ 119,80")).toBeVisible();
  await page.getByRole("button", { name: "Criar pedido" }).click();
  await expect(page.getByText("Pedido criado. Gere sua cobrança individual")).toBeVisible();
  expect(created).toBe(1);
  await page.getByLabel(/CPF ou CNPJ do comprador/).fill("52998224725");
  await page.getByRole("button", { name: "Gerar cobrança exclusiva" }).click();
  await expect(page.getByText("Aguardando pagamento", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Abrir cobrança Asaas" })).toBeVisible();
});
