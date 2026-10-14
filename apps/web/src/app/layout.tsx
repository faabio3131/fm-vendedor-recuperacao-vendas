import type { Metadata, Viewport } from "next";
import "@fontsource/sora/600.css";
import "@fontsource/sora/700.css";
import "@fontsource/plus-jakarta-sans/400.css";
import "@fontsource/plus-jakarta-sans/600.css";
import "@fontsource/plus-jakarta-sans/700.css";
import "./globals.css";
import { PwaRegister } from "@/components/PwaRegister";

export const metadata: Metadata = {
  title: "AtendeVendeIA",
  description: "Atende seus clientes e recupera vendas no WhatsApp, levando até o link de pagamento.",
  applicationName: "AtendeVendeIA",
  icons: { apple: "/icons/apple-touch-icon.png" },
  appleWebApp: { capable: true, title: "AtendeVendeIA", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#020504" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>
        <PwaRegister />
        {children}
      </body>
    </html>
  );
}
