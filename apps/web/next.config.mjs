// Se API_PROXY_TARGET estiver definido NO BUILD, o painel chama a API pelo próprio endereço
// (`/v1/...` no mesmo domínio) e o Next repassa para a API. Assim o cookie de sessão (SameSite=Lax)
// é de mesmo site mesmo quando painel e API estão em endereços `onrender.com` diferentes.
// Sem a variável, o painel chama a API direto por NEXT_PUBLIC_API_URL (domínios do mesmo site).
const apiProxyTarget = (process.env.API_PROXY_TARGET ?? "").replace(/\/+$/, "");

const apiUrl = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
const isProd = process.env.NODE_ENV === "production";

// Cabeçalhos de segurança do painel. A CSP mantém 'unsafe-inline' em script e estilo porque o Next gera
// scripts embutidos; trocar por nonce é melhoria futura (docs/SEGURANCA.md). Em desenvolvimento o Next
// precisa de 'unsafe-eval', por isso a CSP só vale no build de produção.
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client",
  "style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style",
  "img-src 'self' data: https:",
  `connect-src 'self' https://accounts.google.com${apiUrl ? ` ${apiUrl}` : ""}`,
  "frame-src https://accounts.google.com",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  ...(isProd
    ? [
        { key: "Content-Security-Policy", value: csp },
        // O navegador só obedece o HSTS em HTTPS; em http://localhost ele é ignorado.
        { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
      ]
    : []),
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      // O navegador precisa rever o service worker a cada abertura, senão uma versão nova demora a valer.
      { source: "/sw.js", headers: [{ key: "Cache-Control", value: "no-cache, max-age=0" }] },
    ];
  },
  async rewrites() {
    if (!apiProxyTarget) return [];
    return [{ source: "/v1/:path*", destination: `${apiProxyTarget}/v1/:path*` }];
  },
};
export default nextConfig;
