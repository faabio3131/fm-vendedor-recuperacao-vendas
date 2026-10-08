"use client";

import { useEffect } from "react";

/** Registra o service worker só no build de produção (em desenvolvimento ele atrapalha o recarregamento). */
export function PwaRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* sem service worker o painel continua funcionando normalmente */
    });
  }, []);
  return null;
}
