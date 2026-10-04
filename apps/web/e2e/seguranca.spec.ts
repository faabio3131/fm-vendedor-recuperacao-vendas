import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL ?? "demo@example.test";

test("Cabeçalhos de segurança no painel e nenhuma violação da política de conteúdo", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (/content security policy/i.test(msg.text())) violations.push(msg.text());
  });
  page.on("pageerror", (err) => {
    if (/content security policy/i.test(err.message)) violations.push(err.message);
  });

  const response = await page.goto("/login");
  const headers = response?.headers() ?? {};
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["referrer-policy"]).toBeTruthy();
  expect(headers["permissions-policy"]).toContain("camera=()");
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["content-security-policy"]).toContain("object-src 'none'");
  expect(headers["strict-transport-security"]).toContain("max-age=");

  // As telas principais seguem funcionando com a política ativa (scripts embutidos do Next incluídos).
  await page.getByLabel("Login de desenvolvimento (e-mail)").fill(EMAIL);
  await page.getByRole("button", { name: "Entrar (simulado)" }).click();
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();
  for (const label of ["Conexões", "Vendedor IA", "Privacidade"]) {
    await page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: label }).click();
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
  }
  expect(violations).toEqual([]);
});
