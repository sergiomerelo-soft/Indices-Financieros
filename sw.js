// Service worker: permite abrir la app sin conexión y mostrar al instante los últimos datos guardados
const CACHE = "indices-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png", "apple-touch-icon.png"];
const CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js";

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await Promise.allSettled([...SHELL.map(u => c.add(u)), c.add(new Request(CDN, {mode: "cors"}))]);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin) {
    e.respondWith(url.pathname.includes("/data/") ? datos(e, url) : redPrimero(req));
  } else if (req.url === CDN) {
    e.respondWith(cachePrimero(req));
  }
});

// Aviso a la página cuando los datos guardados han cambiado
async function avisar() {
  for (const c of await self.clients.matchAll({includeUncontrolled: true})) c.postMessage("datos-nuevos");
}

// Datos: se muestra al instante lo guardado y se actualiza en segundo plano.
// Si la página pide datos frescos (cache "reload"), primero la red.
async function datos(e, url) {
  const c = await caches.open(CACHE), clave = url.origin + url.pathname;
  const previo = await c.match(clave);
  const red = fetch(e.request).then(async r => {
    if (r.ok) {
      const antes = previo ? await previo.clone().text() : null;
      const ahora = await r.clone().text();
      await c.put(clave, r.clone());
      if (antes !== null && antes !== ahora) avisar();
    }
    return r;
  }).catch(() => null);
  if (previo && e.request.cache !== "reload") { e.waitUntil(red); return previo; }
  return (await red) || previo || Response.error();
}

// Página y archivos de la app: primero la red (siempre la última versión), y sin conexión lo guardado
async function redPrimero(req) {
  const c = await caches.open(CACHE);
  try {
    const r = await fetch(req, {cache: "no-cache"});
    if (r.ok) c.put(req, r.clone());
    return r;
  } catch (err) {
    return (await c.match(req, {ignoreSearch: true})) || (await c.match("index.html")) || Response.error();
  }
}

async function cachePrimero(req) {
  const c = await caches.open(CACHE);
  const m = await c.match(req);
  if (m) return m;
  const r = await fetch(req);
  if (r.ok) c.put(req, r.clone());
  return r;
}
