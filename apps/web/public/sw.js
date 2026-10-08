/* Service worker do AtendeVendeIA.
 * Faz só o mínimo para o painel ser instalável: se o aparelho estiver sem internet ao abrir uma página,
 * mostra /offline.html. NÃO guarda páginas, respostas da API nem dados de conversa (o painel é autenticado
 * e tem dados de clientes). Mudou este arquivo? Troque a VERSAO. */
const VERSAO = "v1";
const CACHE = "atendevendeia-offline-" + VERSAO;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add("/offline.html")));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((chaves) => Promise.all(chaves.filter((c) => c !== CACHE).map((c) => caches.delete(c))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => caches.match("/offline.html")));
});
