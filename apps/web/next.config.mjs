// Se API_PROXY_TARGET estiver definido NO BUILD, o painel chama a API pelo próprio endereço
// (`/v1/...` no mesmo domínio) e o Next repassa para a API. Assim o cookie de sessão (SameSite=Lax)
// é de mesmo site mesmo quando painel e API estão em endereços `onrender.com` diferentes.
// Sem a variável, o painel chama a API direto por NEXT_PUBLIC_API_URL (domínios do mesmo site).
const apiProxyTarget = (process.env.API_PROXY_TARGET ?? "").replace(/\/+$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  async rewrites() {
    if (!apiProxyTarget) return [];
    return [{ source: "/v1/:path*", destination: `${apiProxyTarget}/v1/:path*` }];
  },
};
export default nextConfig;
