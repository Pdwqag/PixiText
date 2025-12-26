// sw.js
const CACHE_VERSION = "pixi-v10.4";              // ← 上げる
const STATIC_ASSETS = ["/", "/static/icon-192.png", "/static/icon-512.png"]; // ← CSS/JSは入れない

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE_VERSION).then((c) => c.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_VERSION).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// HTML → network-first
async function handleHTML(req){
  try{
    const resp = await fetch(req);
    const c = await caches.open(CACHE_VERSION);
    c.put(req, resp.clone());
    return resp;
  }catch{
    return (await caches.match(req)) || new Response("Offline", {status:503});
  }
}

// CSS/JS → **network-first**（古いのを先に出さない）
async function handleAsset(req){
  try{
    const resp = await fetch(req, {cache: "no-store"});
    const c = await caches.open(CACHE_VERSION);
    c.put(req, resp.clone());
    return resp;
  }catch{
    return (await caches.match(req)) || new Response("/* offline */", {status:503});
  }
}

// 画像 → cache-first（従来どおり）
async function handleImage(req){
  const cached = await caches.match(req);
  if (cached) return cached;
  try{
    const resp = await fetch(req);
    const c = await caches.open(CACHE_VERSION);
    c.put(req, resp.clone());
    return resp;
  }catch{
    return new Response("", {status:404});
  }
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  const same = url.origin === self.location.origin;

  if (e.request.mode === "navigate") {
    e.respondWith(handleHTML(e.request)); return;
  }
  if (same && url.pathname.startsWith("/static/")) {
    if (url.pathname.endsWith(".css") || url.pathname.endsWith(".js")) {
      e.respondWith(handleAsset(e.request)); return;
    }
    if (/\.(png|jpg|jpeg|gif|webp|svg|ico)$/i.test(url.pathname)) {
      e.respondWith(handleImage(e.request)); return;
    }
  }
  e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
});

