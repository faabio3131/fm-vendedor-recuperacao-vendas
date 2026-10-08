import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("App instalável: manifesto, ícones, service worker e página offline", async ({ request }) => {
  const manifesto = await request.get("/manifest.webmanifest");
  expect(manifesto.ok()).toBe(true);
  const m = await manifesto.json();
  expect(m.name).toBe("AtendeVendeIA");
  expect(m.display).toBe("standalone");
  expect(m.start_url).toBe("/");
  const tamanhos = m.icons.map((i: { sizes: string; purpose?: string }) => `${i.sizes}:${i.purpose}`);
  expect(tamanhos).toEqual(expect.arrayContaining(["192x192:any", "512x512:any", "512x512:maskable"]));
  for (const icone of m.icons as { src: string }[]) {
    const r = await request.get(icone.src);
    expect(r.ok(), icone.src).toBe(true);
    expect(r.headers()["content-type"]).toContain("image/png");
  }
  expect((await request.get("/icons/apple-touch-icon.png")).ok()).toBe(true);

  const sw = await request.get("/sw.js");
  expect(sw.ok()).toBe(true);
  expect(sw.headers()["cache-control"]).toContain("no-cache");
  // o service worker não pode guardar dados do painel
  const codigo = await sw.text();
  expect(codigo).not.toMatch(/cache\.put|addAll/);

  const offline = await request.get("/offline.html");
  expect(offline.ok()).toBe(true);
  expect(await offline.text()).toContain("Sem conexão com a internet");
});

test("Tela 'App no celular' explica a instalação sem rolagem horizontal", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();

  await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "App no celular" }).click();
  await expect(page.getByRole("heading", { name: "App no celular" })).toBeVisible();
  await expect(page.getByText("No Android (Chrome)")).toBeVisible();
  await expect(page.getByText("No iPhone (Safari)")).toBeVisible();
  await expect(page.getByText("Adicionar à Tela de Início")).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});
